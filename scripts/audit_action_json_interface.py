"""Post-hoc, read-only interface diagnostic; never repairs, dispatches or scores.

Run the frozen whole-batch audit first. This narrower check compares recorded
requests and first responses; it is not a replacement for provenance replay.
Only hashes and format metadata are printed, never prompts or response text.
"""
import argparse
import hashlib
import json
from pathlib import Path

from runtime.cache_intent_staged_loop import ACTION_INSTRUCTION, parse_action
from runtime.cache_route_symmetric_design import classify
from runtime.deepseek_official_transport import validate_payload


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def diagnose(payload, transport, provider, adapter, recorded_decision):
    validate_payload(payload)  # Pure validation; no network access.
    if transport['transport_status'] != 'RESPONSE_RECEIVED':
        raise ValueError('No complete transport response')
    raw = json.loads(transport['response_text'])
    if raw != provider['raw']:
        raise ValueError('Provider journal differs from transport record')
    choice = raw['choices'][0]
    content = choice['message']['content']
    if content != adapter['response']['content']:
        raise ValueError('Adapter changed response content')
    if choice['finish_reason'] != adapter['response']['finish_reason']:
        raise ValueError('Finish reason mismatch')
    expected = classify(content, 1) if choice['finish_reason'] == 'stop' else dict(
        status='INVALID_ACTION_RESPONSE', stop=True, dispatch_tool=False, action=None)
    if expected != recorded_decision:
        raise ValueError('Recorded decision differs from strict parser')
    syntax = {'valid_single_json': True}
    try:
        json.loads(content)
    except json.JSONDecodeError as exc:
        syntax = dict(valid_single_json=False, error=exc.msg, error_position=exc.pos)
    schema_valid = True
    try:
        parse_action(content)
    except (ValueError, TypeError):
        schema_valid = False
    texts = [m['content'] for m in payload['messages'] if m['role'] in ('system', 'user')]
    return dict(
        payload_sha256=hashlib.sha256(json.dumps(payload, sort_keys=True,
            ensure_ascii=False).encode()).hexdigest(),
        response_content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        json_mode_enabled=payload['response_format'] == {'type': 'json_object'},
        json_word_in_prompt=any('json' in text.lower() for text in texts),
        canonical_action_examples_present=any(ACTION_INSTRUCTION in text for text in texts),
        max_tokens=payload['max_tokens'],
        completion_tokens=raw['usage']['completion_tokens'],
        finish_reason=choice['finish_reason'],
        transport_provider_adapter_equal=True, syntax=syntax,
        action_schema_valid=schema_valid, recorded_status=expected['status'],
        dispatch_tool=expected['dispatch_tool'], content_repaired=False)


def audit(prepared):
    prepared = Path(prepared).resolve()
    project = Path(__file__).resolve().parents[1]
    for name in ('cache_intent_staged_loop.py', 'cache_route_symmetric_design.py',
                 'deepseek_official_transport.py'):
        if (project/'runtime'/name).read_bytes() != (prepared/'source/runtime'/name).read_bytes():
            raise ValueError('Diagnostic parser/validator differs from frozen source')
    experiment = prepared/'experiment'
    before = hashes(experiment)
    rows = []
    for number in range(4):
        cell = f'cell-{number}'
        trace = experiment/cell/'trace'
        def read(name):
            return json.loads((trace/name).read_text(encoding='utf-8'))
        row = diagnose(read('api-g001.pending.json')['payload'], read('transport-001.json'),
                       read('api-g001.provider.json'), read('g001.response.json'),
                       read('g001.decision.json'))
        tool_records = len(list(trace.glob('t*.pending.json')))
        if row['recorded_status'].startswith('INVALID') and tool_records:
            raise ValueError('Invalid first response followed by tool dispatch')
        rows.append(dict(id=cell, tool_pending_records=tool_records, **row))
    if before != hashes(experiment):
        raise ValueError('Experiment files changed during read-only audit')
    return dict(status='POST_HOC_READONLY_INTERFACE_DIAGNOSTIC', cells=rows,
                unchanged_experiment_files=len(before), model_calls=0, SQL_calls=0,
                old_scores_modified=False, provider_internal_cause_known=False,
                limitation='Local records do not independently attest provider internals.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.prepared), indent=2))
