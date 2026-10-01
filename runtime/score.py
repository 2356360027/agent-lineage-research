"""Descriptive paired pilot summary; no confirmatory significance claims."""
import argparse
import json
from pathlib import Path
from statistics import mean
from .e1 import world, save, digest


def summarize(root):
    manifests = list(root.glob('shard-*/manifest.json'))
    if not manifests:
        raise ValueError('No manifests')
    specs = [json.loads(p.read_text()) for p in manifests]
    signatures = {digest({k: s[k] for k in ('config', 'code_hash', 'shards')}) for s in specs}
    if len(signatures) != 1:
        raise ValueError('Incompatible runs')
    config = specs[0]['config']
    records = [json.loads(p.read_text()) for p in root.glob('shard-*/result-*.json')]
    keys = [(r['item'], r['repetition']) for r in records]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate item/repetition')
    expected = {(i, r) for i in range(config['items']) for r in range(config['repetitions'])}
    if set(keys) - expected:
        raise ValueError('Unexpected item/repetition')
    arms = {}
    for arm in ['E0C0', 'E0C1', 'E1C0', 'E1C1']:
        rows = []
        for r in records:
            _, y = world(config['seed'], r['item'])
            a = r['arms'][arm]['assessment']
            s = r['initial']['answer']
            rows.append((int(a['answer'] == y), (a['p_state_1'] - y)**2,
                         int(s == y and a['answer'] != y), int(s != y and a['answer'] == y)))
        arms[arm] = dict(zip(['accuracy', 'brier', 'correct_to_wrong_rate', 'wrong_to_correct_rate'],
                            [mean(x) for x in zip(*rows)])) if rows else None
    delta = mean(r['arms']['E1C1']['assessment']['p_state_1'] -
                 r['arms']['E1C0']['assessment']['p_state_1'] for r in records) if records else None
    calls = []
    for p in root.glob('shard-*/i*.json'):
        calls.append(json.loads(p.read_text()))
    usage_complete = bool(calls) and all(isinstance(c.get('usage'), dict) and
        all(isinstance(c['usage'].get(k), int) for k in ('prompt_tokens', 'completion_tokens')) for c in calls)
    return {'status': 'DESCRIPTIVE_PILOT_ONLY', 'completed_pairs': len(records),
            'expected_pairs': len(expected), 'missing_pairs': sorted(expected-set(keys)),
            'arms': arms, 'paired_probability_delta_E1C1_minus_E1C0': delta,
            'attempt_records': len(calls), 'failed_or_pending': sum(c['status'] != 'ok' for c in calls),
            'usage_complete': usage_complete,
            'reported_prompt_tokens': sum(c['usage']['prompt_tokens'] for c in calls) if usage_complete else None,
            'reported_completion_tokens': sum(c['usage']['completion_tokens'] for c in calls) if usage_complete else None,
            'warning': 'Synthetic tasks; verify backend provenance. Missing/failed items are not imputed. '
                       'Repetitions are clustered by item; this summary has no confidence intervals.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--run', required=True)
    args = p.parse_args()
    root = Path(args.run)
    result = summarize(root)
    save(root / 'summary.json', result)
    print(json.dumps(result, indent=2))
