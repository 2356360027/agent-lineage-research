"""Injected multi-turn accounting adapter; no HTTP, secrets or tool dispatch.

Caller supplies a separately authorized transport, exclusive durable save,
shared CostGuard, native reference counter and finite deadline. This module
does not authorize a batch, establish worst-case horizon cost, or grade tasks.
"""
from copy import deepcopy
import math
import time

from runtime.native_action_interface import _load, parse_native_response_v2
from runtime.native_trajectory_counter import validate_trajectory_payload
from runtime.native_action_history import phase
from runtime.cache_route_symmetric_design import classify
import json


class NativeTrajectoryAdapter:
    def __init__(self, *, counter, transport, save, guard, deadline, clock=time.monotonic):
        if type(deadline) not in (int,float) or not math.isfinite(deadline):
            raise ValueError('Finite deadline required')
        self.counter, self.transport, self.save, self.guard = counter, transport, save, guard
        self.deadline, self.clock = deadline, clock
        self.count, self.closed = 0, False
        self.previous_payload, self.previous_message = None, None

    def __call__(self, request):
        if self.closed or self.count >= 4 or self.guard.terminal:
            raise RuntimeError('Closed adapter; no retry')
        try:
            return self._call(request)
        except BaseException as exc:
            self.closed = True
            self.guard.terminal = True
            self.save('adapter_failure',dict(error_type=type(exc).__name__,automatic_retry=False,
                                           guard=self.guard.snapshot()))
            raise

    def _call(self, request):
        index=self.count+1
        if (type(request) is not dict
                or set(request)!={'generation_id','stage','payload','execution_authorized'}
                or request['generation_id']!=f'g{index:03d}' or request['stage']!=phase(index)
                or request['execution_authorized'] is not False):
            raise ValueError('Generation or explicit profile mismatch')
        payload=deepcopy(request['payload'])
        validate_trajectory_payload(payload)
        prior_calls=sum(m['role']=='assistant' for m in payload['messages'])
        if prior_calls!=index-1:
            raise ValueError('History length differs from generation')
        if self.previous_payload is not None:
            old=self.previous_payload['messages']; new=payload['messages']
            if (new[:len(old)]!=old or new[len(old)]!=self.previous_message
                    or {k:v for k,v in payload.items() if k!='messages'} !=
                       {k:v for k,v in self.previous_payload.items() if k!='messages'}):
                raise ValueError('Request history does not preserve received response')
        receipt=self.counter.inspect(payload)
        n=receipt['reference_input_tokens']
        if type(n) is not int or not 0<n<=8192:
            raise ValueError('Native context cap exceeded; no truncation')
        remaining=self.deadline-self.clock()
        if remaining<=0: raise TimeoutError('Deadline before reservation')
        name=f'api-g{index:03d}'
        self.save(name+'.context',receipt)
        ticket=self.guard.reserve(n,512)
        self.save(name+'.pending',dict(payload=deepcopy(payload),ticket=ticket,
                  automatic_retry=False,timeout_seconds=min(90,remaining)))
        remaining=self.deadline-self.clock()
        if remaining<=0: raise TimeoutError('Deadline after pending write; not dispatched')
        self.count=index
        raw=self.transport(deepcopy(payload),min(90,remaining))
        self.save(name+'.provider',dict(raw_response_text=raw))
        if type(raw) is not str or len(raw.encode('utf-8'))>1048576:
            raise ValueError('Bounded raw response required')
        body=_load(raw)
        if type(body) is not dict or 'error' in body or type(body.get('usage')) is not dict:
            raise ValueError('Trustworthy usage missing; reservation retained')
        usage=body['usage']
        try:
            self.guard.settle(ticket,usage)
        finally:
            self.save(name+'.cost',self.guard.snapshot())
        if self.clock()>=self.deadline: raise TimeoutError('Late raw response retained')
        if (body.get('model')!='deepseek-flash' or usage['prompt_tokens']!=n
                or usage['completion_tokens']>512):
            raise ValueError('Provider identity/count/output differs; no offset fitting')
        try:
            parsed=parse_native_response_v2(raw)
            decision=classify(json.dumps(parsed['action']),index)
            observed=dict(format_admitted=True,protocol_status=decision['status'])
            self.closed=decision['stop']
            call=body['choices'][0]['message']['tool_calls'][0]
            self.previous_message=dict(role='assistant',content=None,
                tool_calls=[{k:deepcopy(call[k]) for k in ('id','type','function')}])
        except (ValueError,TypeError,RecursionError):
            observed=dict(format_admitted=False,protocol_status='INVALID_RESPONSE')
            self.closed=True
        self.save(name+'.admission',dict(observed,semantic_correctness_assessed=False,
                                        execution_authorized=False))
        self.previous_payload=deepcopy(payload)
        return raw
