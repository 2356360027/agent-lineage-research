"""Count all eight existing first contexts locally; no network or inference."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from runtime.native_action_counter import NativeFirstCounter, SOURCE_HASHES
from runtime.native_action_interface import build_candidate

PLAN_SHA = 'f55859a61bbc5bdcb20cf8ea407e8854aafa838b388ab49d4e059569270d5e4f'


def audit(plan, reference, tokenizer, dependencies, output):
    plan = Path(plan)
    raw = plan.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PLAN_SHA:
        raise ValueError('Unexpected source plan')
    source = json.loads(raw)
    jobs = [j for j in source['jobs'] if j['arm'] == 'original']
    if len(jobs) != 8 or len({j['context_id'] for j in jobs}) != 8:
        raise ValueError('All original contexts required')
    sys.path.insert(0, str(Path(dependencies).resolve()))
    counter = NativeFirstCounter(reference, tokenizer)
    rows = []
    for job in jobs:
        candidate = build_candidate(job['payload'])
        rows.append({'context_id': job['context_id'], **counter.inspect(candidate['payload'])})
    if plan.read_bytes() != raw:
        raise ValueError('Source changed during audit')
    result = {'status': 'OFFLINE_NATIVE_FIRST_REQUEST_REFERENCE_COUNTS',
              'source_plan_sha256': PLAN_SHA, 'source_hashes': SOURCE_HASHES,
              'rows': rows, 'total_reference_input_tokens': sum(r['reference_input_tokens'] for r in rows),
              'maximum_reference_input_tokens': max(r['reference_input_tokens'] for r in rows),
              'total_requested_output_token_ceiling_if_eight_calls': 8*512,
              'source_unchanged': True, 'new_API_calls': 0, 'SQL_calls': 0,
              'paid_batch_prepared': False, 'inference_authorized': False,
              'limitation': 'Source-derived first-request counter, not executed upstream Rust parity or observed beta endpoint parity. Strict decoding overhead may differ. No provider cost guarantee.'}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with (output/'audit.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    root = Path(__file__).resolve().parents[1]
    for name in ('scripts/audit_native_action_counts.py', 'runtime/native_action_counter.py',
                 'runtime/native_action_interface.py', 'runtime/deepseek_text_counter.py'):
        destination = output/'source'/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream: stream.write((root/name).read_bytes())
    hashes = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in output.rglob('*') if p.is_file()}
    with (output/'artifact_hashes.json').open('x', encoding='utf-8') as stream:
        json.dump(hashes, stream, indent=2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'reference', 'tokenizer', 'dependencies', 'output'):
        parser.add_argument('--'+name, required=True)
    print(json.dumps(audit(**vars(parser.parse_args())), indent=2))
