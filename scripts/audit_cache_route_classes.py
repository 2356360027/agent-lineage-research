"""Offline saved-row transformations; no SQL/API, no model or human labels.

Separates direct answer, derivable answer, and non-direct result. A first read
request is not an erroneous answer even for the non-direct result condition.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_tools import valid_result
from scripts.prepare_staged_contract_review import load_archive
from scripts.audit_deepseek_cache_controls import INPUT_SHA, WITNESS_SHA, PREPARED_SHA


def filter_project(cache):
    valid_result(cache)
    if cache['columns'] != ['molecule_id', 'label']:
        raise ValueError('Unexpected filter/project columns')
    rows = [row[:1] for row in cache['rows'] if row[1] == ['text', '+']]
    return list({audit.encoded(row): deepcopy(row) for row in rows}.values())


def count_distinct(cache):
    valid_result(cache)
    if cache['columns'] != ['bond_id']:
        raise ValueError('Unexpected count column')
    values = {audit.encoded(row[0]) for row in cache['rows'] if row[0][0] != 'null'}
    return [[['integer', str(len(values))]]]


def run(root, witness, output):
    root, witness, output = map(Path, (root, witness, output))
    if output.exists():
        raise ValueError('Never overwrite an audit')
    if audit.hash_file(root/'prepared_manifest.json') != PREPARED_SHA:
        raise ValueError('Prepared manifest changed')
    manifest = json.loads((root/'prepared_manifest.json').read_text('utf-8'))
    load_archive(root/'inputs', INPUT_SHA)
    load_archive(witness, WITNESS_SHA)
    report = json.loads((witness/'report.json').read_text('utf-8'))
    if any(report[k] != manifest['database_sha256'] for k in
           ('database_sha256_before', 'database_sha256_after')):
        raise ValueError('Witness snapshot mismatch')
    plan = json.loads((root/'inputs/PRIVATE_plan.json').read_text('utf-8'))
    caches = json.loads((root/'inputs/PRIVATE_caches.json').read_text('utf-8'))
    rows = []
    for task in (200, 234):
        target_file = witness/f'h{task}c-p1-delay20/tools/attempt-021.completed.json'
        response = json.loads(target_file.read_text('utf-8'))['response']
        target = audit.fingerprints(response['rows'])['bag_sha256']
        for suffix in 'abc':
            hid = f'h{task}{suffix}'
            selected = [j for j in plan['jobs'] if j['history_id']==hid and j['read_price']==1]
            if len(selected)!=1:
                raise ValueError('Missing historical input')
            job = selected[0]
            packet = json.loads(job['envelope']['messages'][1]['content'])
            cache = caches[hid]
            valid_result(cache)
            if (job['cached_result_sha256'] != audit.digest(cache) or
                    cache['sql_sha256'] != audit.digest(packet['history']['sql'])):
                raise ValueError('Historical cache binding differs')
            match = cache['bag_sha256'] == target
            transformed = None
            transform = None
            if suffix == 'b':
                transform = 'filter_label_plus_project_distinct_id' if task == 200 else 'count_distinct_nonnull_id'
                computed = filter_project(cache) if task == 200 else count_distinct(cache)
                transformed = audit.fingerprints(computed)['bag_sha256'] == target
                if not transformed:
                    raise ValueError('Derived control failed against saved witness')
            if (suffix == 'a' and not match) or (suffix in 'bc' and match):
                raise ValueError('Unexpected directness; inspect before changing plan')
            rows.append(dict(history_id=hid, saved_rows=len(cache['rows']),
                direct_typed_bag_match=match, local_transformation=transform,
                transformed_typed_bag_match=transformed,
                route_class={'a':'direct_answer_control','b':'derivable_answer_control',
                             'c':'not_direct_answer_no_transform_certified'}[suffix]))
    result = dict(status='OFFLINE_ROUTE_CLASS_AUDIT', rows=rows,
        input_manifest_sha256=INPUT_SHA, witness_manifest_sha256=WITNESS_SHA,
        new_SQL_queries=0, model_calls=0, new_research_samples=0,
        assistant_semantic_interpretation_not_human_review=True,
        limits=['Only saved typed-row bags on one database snapshot were compared.',
                'Transforms are scripted witnesses, not model behavior or a new method.',
                'No proof that all useful transformations of c histories are impossible.',
                'Reading a c history can be an information-gathering action; it is not automatically wrong reuse.',
                'No inference that the agent noticed applicability, and no semantic drift label.',
                'Future generic provenance statement still requires the original historical execution receipt to be checked.'])
    output.mkdir(parents=True, exist_ok=False)
    audit.save_new(output/'report.json', result)
    with (output/'audit_source.py').open('xb') as f:
        f.write(Path(__file__).read_bytes())
    audit.save_new(output/'artifact_hashes.json', {p.name:audit.hash_file(p) for p in output.iterdir()})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'witness', 'output'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.prepared, args.witness, args.output), indent=2))
