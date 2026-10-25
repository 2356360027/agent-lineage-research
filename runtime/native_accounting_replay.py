"""Completed native API journal replay, independent of transport execution.

Use after loop/tool replay and external archive verification. One shared guard
must be passed in the frozen job order; never reset it per trajectory. Failures
or partial journals are retained and not certified as completed. This is client
accounting at conservative declared rates, never a verified provider invoice.
"""
import json
import math

from runtime.native_action_interface import _load, parse_native_response_v2
from runtime.native_trajectory_counter import validate_trajectory_payload
from runtime.cache_route_symmetric_design import classify
from runtime.native_trajectory_replay import equal, IncompleteTrajectory


def replay_completed(records, *, loop_records, counter, guard):
    if type(records) is not dict or type(loop_records) is not dict:
        raise ValueError('Separate API and loop journal maps required')
    if 'adapter_failure' in records or 'failure' in loop_records or 'summary' not in loop_records:
        raise IncompleteTrajectory('Failed or unfinished API trajectory; retain uncertain usage')
    n_calls = loop_records['summary'].get('generation_calls')
    if type(n_calls) is not int or not 1 <= n_calls <= 4:
        raise ValueError('Invalid trajectory horizon')
    expected = {f'api-g{i:03d}.{kind}' for i in range(1,n_calls+1)
                for kind in ('context','pending','provider','cost','admission')}
    expected |= {f'transport-{i:03d}' for i in range(1,n_calls+1)}
    if set(records) != expected:
        raise IncompleteTrajectory('API inventory missing or extra; no silent repair')
    prompt = completion = 0
    closed = False
    for i in range(1,n_calls+1):
        if closed: raise ValueError('API call after terminal action')
        prefix=f'api-g{i:03d}'
        request=loop_records.get(f'g{i:03d}.pending')
        response=loop_records.get(f'g{i:03d}.response')
        if type(request) is not dict or type(response) is not dict:
            raise ValueError('Missing loop cross-binding')
        payload=request['payload']
        validate_trajectory_payload(payload)
        if sum(m['role']=='assistant' for m in payload['messages']) != i-1:
            raise ValueError('Generation history length differs')
        receipt=counter.inspect(payload)
        equal(records[prefix+'.context'],receipt,'API reference count receipt')
        n=receipt['reference_input_tokens']
        if type(n) is not int or not 0<n<=8192: raise ValueError('Input admission cap exceeded')
        pending=records[prefix+'.pending']
        if (type(pending) is not dict
                or set(pending)!={'payload','ticket','automatic_retry','timeout_seconds'}):
            raise ValueError('API pending schema')
        equal(pending['payload'],payload,'Loop/API payload binding')
        equal(pending['automatic_retry'],False,'API retry flag')
        timeout=pending['timeout_seconds']
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=90:
            raise ValueError('API timeout is not bounded')
        ticket=guard.reserve(n,512)
        equal(pending['ticket'],ticket,'Shared reservation order')
        transport=records[f'transport-{i:03d}']
        if type(transport) is not dict or set(transport)!={'transport_status','response_text'}:
            raise ValueError('Completed transport record schema')
        if transport['transport_status']!='RESPONSE_RECEIVED':
            raise IncompleteTrajectory('Transport not completed')
        raw=transport['response_text']
        if type(raw) is not str or len(raw.encode('utf-8'))>1048576:
            raise ValueError('Bounded raw response required')
        equal(records[prefix+'.provider'],{'raw_response_text':raw},'Transport/API raw binding')
        equal(response['raw_response_text'],raw,'API/loop raw binding')
        body=_load(raw)
        if type(body) is not dict or 'error' in body or type(body.get('usage')) is not dict:
            raise ValueError('Trustworthy usage missing')
        usage=body['usage']
        guard.settle(ticket,usage)
        equal(records[prefix+'.cost'],guard.snapshot(),'Recomputed cumulative cost')
        if body.get('model')!='deepseek-flash' or usage['prompt_tokens']!=n or usage['completion_tokens']>512:
            raise ValueError('Provider identity/count/output mismatch')
        try:
            parsed=parse_native_response_v2(raw)
            decision=classify(json.dumps(parsed['action']),i)
            observed=dict(format_admitted=True,protocol_status=decision['status'])
            closed=decision['stop']
        except (ValueError,TypeError,RecursionError):
            observed=dict(format_admitted=False,protocol_status='INVALID_RESPONSE')
            closed=True
        equal(records[prefix+'.admission'],dict(observed,semantic_correctness_assessed=False,
              execution_authorized=False),'Recomputed API admission')
        prompt+=usage['prompt_tokens']; completion+=usage['completion_tokens']
    return dict(status='COMPLETED_API_ACCOUNTING_REPLAY_VERIFIED',calls=n_calls,
        reported_prompt_tokens=prompt,reported_completion_tokens=completion,
        cumulative_guard=guard.snapshot(),provider_invoice_verified=False,
        further_calls_authorized=False,semantic_correctness_assessed=False)
