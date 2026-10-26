"""Read-only verification and deterministic rescoring of a frozen local batch.

Does not start a model or modify the run. Optional audit output is created
exclusively and must live OUTSIDE the frozen prepared/run directory.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def verify_directory(root, hashes):
    for name, expected in hashes.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path) != expected:
            raise ValueError('Archive integrity mismatch: ' + name)


def audit(prepared):
    prepared = prepared.resolve()
    manifest = json.loads((prepared / 'manifest.json').read_bytes())
    verify_directory(prepared, manifest['files'])
    out = prepared / 'experiment'
    verify_directory(out, json.loads((out / 'sha256.json').read_bytes()))
    spec = importlib.util.spec_from_file_location('frozen_separation',
        prepared / 'source/runtime/reuse_separation.py')
    frozen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frozen)
    schedule = json.loads((prepared / 'jobs.json').read_bytes())
    cases = {c['id']: c for c in json.loads((prepared / 'cases.private.json').read_bytes())}
    responses, transport = {}, []
    for index, job in enumerate(schedule):
        prefix = f'{index:02d}'
        rawpath = out / (prefix + '.response.json')
        if not rawpath.exists():
            continue
        raw = json.loads(rawpath.read_bytes())
        request = json.loads((out / (prefix + '.request.json')).read_bytes())
        dispatch = json.loads((out / (prefix + '.dispatch.json')).read_bytes())
        measurement = json.loads((out / (prefix + '.measurement.json')).read_bytes())
        proposal = None
        if 'proposal_id' in job:
            proposal = frozen.parse_response(responses[job['proposal_id']], 'M1')['admissibility']
        if request['messages'] != frozen.messages(cases[job['case_id']], job, proposal):
            raise ValueError('Prompt differs from frozen design')
        if dispatch['input_sha256'] != frozen.digest(request) or dispatch['job_id'] != job['id']:
            raise ValueError('Dispatch mismatch')
        responses[job['id']] = raw
        usage = raw['usage']
        transport.append({'seconds': measurement['seconds'],
            'prompt_tokens': usage['prompt_tokens'], 'completion_tokens': usage['completion_tokens'],
            'cached_tokens': usage['prompt_tokens_details']['cached_tokens'],
            'tokenization_matches': measurement['preflight_prompt_tokens'] == usage['prompt_tokens'],
            'finish_reason': raw['choices'][0]['finish_reason']})
    recomputed = frozen.summarize(responses)
    recorded = json.loads((out / 'summary.json').read_bytes())
    for key, value in recomputed.items():
        if recorded[key] != value:
            raise ValueError('Frozen rescore mismatch: ' + key)
    if recorded['manifest_sha256'] != sha(prepared / 'manifest.json'):
        raise ValueError('Recorded manifest hash mismatch')
    metrics = {}
    for method in ('M1','M2','M3','M4'):
        rows = [r for r in recomputed['judgments'] if r['method'] == method]
        metrics[method] = {'n_unique_outputs': len(rows),
            'exact_semantic_label_correct': sum(r['admissibility'] ==
                ('applicable' if r['sufficient'] else 'inapplicable') for r in rows),
            'positive_false_reject': sum(r['sufficient'] and r['admissibility']=='inapplicable' for r in rows),
            'unknown': sum(r['admissibility']=='unknown' for r in rows),
            'invalid': sum(not r['valid'] for r in rows)}
    price_contrasts = {}
    for method in ('M1','M2'):
        group = {}
        for price in (1,10,20):
            rows = [r for r in recomputed['judgments'] if r['method']==method
                    and r['price']==price and not r['sufficient']]
            group[str(price)] = {'false_applicable': sum(r['false_applicable'] for r in rows),
                                 'negative_contexts': len(rows),
                                 'unknown': sum(r['admissibility']=='unknown' for r in rows)}
        price_contrasts[method] = group
    times = [r['seconds'] for r in transport]
    return {'status': 'VERIFIED_FROZEN_RESCORE', 'manifest_sha256': sha(prepared/'manifest.json'),
        'summary_sha256': sha(out/'summary.json'), 'source_and_outputs_hashes_verified': True,
        'requests_match_frozen_builder': True, 'score_exact_match': True,
        'complete_generations': len(responses), 'strict_json_valid': sum(r['valid'] for r in recomputed['judgments']),
        'input_tokens': sum(r['prompt_tokens'] for r in transport),
        'output_tokens': sum(r['completion_tokens'] for r in transport),
        'cached_tokens': sum(r['cached_tokens'] for r in transport),
        'all_preflight_token_counts_exact': all(r['tokenization_matches'] for r in transport),
        'all_stop': all(r['finish_reason']=='stop' for r in transport),
        'generation_http_seconds_sum': sum(times),
        'generation_http_seconds_median': statistics.median(times) if times else None,
        'generation_http_seconds_max': max(times, default=None),
        'max_prompt_tokens': max((r['prompt_tokens'] for r in transport), default=None),
        'cold_start_seconds': recorded['cold_start_seconds'],
        'owned_server_seconds_including_startup_cleanup': recorded['server_seconds'],
        'total_wall_seconds_including_hashes': recorded['total_seconds_including_preflight'],
        'semantic_metrics': metrics, 'negative_price_contrasts': price_contrasts,
        'method_actions': recomputed['methods'],
        'resource_scope': 'New goal: 32/10000 generations; this owned server ran ~17 seconds. '
                          'This is server wall time, not metered GPU active hours or energy.',
        'caveats': ['Only 2 development bases; no confirmation inference.',
                    'Same six methods, but M4 single low-price proposal calibration is not the final fair-comparison schedule.',
                    'Current SQL was executed for hidden oracle checking, NOT as a model-selected action.',
                    'Frozen no_current_SQL_or_model_transform_executed field refers to agent execution only.',
                    'No semantic claim based on free-text rationale; all raw rationales retained locally.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    result = audit(a.prepared)
    if a.output:
        if a.output.resolve().is_relative_to(a.prepared.resolve()):
            raise ValueError('Do not modify the frozen prepared/run directory')
        with a.output.open('x', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
