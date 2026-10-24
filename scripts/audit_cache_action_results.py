"""Supplementary saved-result comparison, not human scoring or SQL equivalence.

No model calls, database opens, SQL execution, or edits to sealed run artifacts.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from scripts.audit_cache_action import read, sha, snapshot, require, no_links, precheck
from scripts.export_cache_action_reviews import MANIFEST, AUDIT, WITNESS, tree


def bag(rows):
    return Counter(json.dumps(row, ensure_ascii=False, separators=(',', ':')) for row in rows)


def compare(record, target, history):
    response = record['response']
    value = record['private']['backend_result']
    require(response['status'] == value['status'] == 'EXECUTED', 'Non-successful result')
    require(response['complete'] is True and value['complete'] is True, 'Incomplete result')
    require(response['rows'] == value['rows'] and response['columns'] == value['columns'],
            'Visible and private results differ')
    require(all(len(row) == len(value['columns']) for row in value['rows']), 'Invalid row width')
    tool = record['request']['tool']
    require(tool in ('run_sql', 'read_cache'), 'Unexpected tool')
    direct = bag(value['rows']) == bag(target['rows'])
    transformed = None
    if tool == 'read_cache' and history == 'h234b':
        require(value['columns'] == ['bond_id'], 'Wrong transform input')
        ids = {json.dumps(row[0], ensure_ascii=False) for row in value['rows'] if row[0][0] != 'null'}
        transformed = bag([[['integer', str(len(ids))]]]) == bag(target['rows'])
    return dict(tool=tool, direct_typed_bag_match=direct,
                scripted_distinct_count_match=transformed,
                scripted_transform_is_not_observed_model_computation=True)


def run(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    no_links(output); require(not output.exists(), 'Never overwrite an audit')
    runs = project/'runtime/runs'
    prepared = runs/'cache-action-prepared-20261024-002'
    auditdir = runs/'cache-action-audit-20261024-002'
    witness = runs/'staged-recovery-audit-20261024-001'
    require(not any(output == p or output.is_relative_to(p) for p in (prepared, auditdir, witness)),
            'Output must be separate')
    before = {str(p): snapshot(p) for p in (prepared, auditdir, witness)}
    manifest, seal = precheck(prepared, MANIFEST)
    require(sha(auditdir/'audit.json') == AUDIT, 'Wrong prior audit')
    require(sha(witness/'artifact_hashes.json') == WITNESS, 'Wrong witness')
    tree(auditdir); tree(witness)
    prior = read(auditdir/'audit.json')
    require(prior['experiment_manifest_sha256'] == seal and
            prior['status'] == 'SEALED_FINISHED_TRACE_REPLAY_VERIFIED', 'Unverified experiment')
    witness_report = read(witness/'report.json')
    require(all(witness_report[k] == manifest['database_sha256'] for k in
                ('database_sha256_before', 'database_sha256_after')), 'Different database snapshot')
    rows = []
    for entry in prior['rows']:
        hid = entry['history_id']; task = hid[1:4]
        files = sorted((prepared/'experiment'/entry['id']/'tools').glob('*.completed.json'))
        require(len(files) == entry['tool_attempts'] == 1, 'Unexpected tool coverage')
        target = read(witness/f'h{task}c-p1-delay20/tools/attempt-021.completed.json')['response']
        require(target['complete'] is True and target['status'] == 'EXECUTED', 'Incomplete witness')
        rows.append(dict(id=entry['id'], history_id=hid, record_sha256=sha(files[0]),
                         **compare(read(files[0]), target, hid)))
    require(len(rows) == len({r['id'] for r in rows}) == 12, 'Wrong slot coverage')
    result = dict(status='SUPPLEMENTARY_SAVED_TYPED_RESULT_COMPARISON',
        prepared_manifest_sha256=MANIFEST, prior_audit_sha256=AUDIT,
        witness_manifest_sha256=WITNESS, rows=rows,
        SQL_results=sum(r['tool']=='run_sql' for r in rows),
        SQL_typed_bag_matches=sum(r['tool']=='run_sql' and r['direct_typed_bag_match'] for r in rows),
        cache_results=sum(r['tool']=='read_cache' for r in rows),
        cache_direct_matches=sum(r['tool']=='read_cache' and r['direct_typed_bag_match'] for r in rows),
        cache_scripted_count_matches=sum(r['scripted_distinct_count_match'] is True for r in rows),
        new_API_calls=0, new_SQL_executions=0, human_labels=0, C_drift_assessed=False,
        limits=['Typed row bags ignore order and column aliases, preserve types and multiplicities.',
                'One fixed snapshot is not general SQL equivalence or independent task confirmation.',
                'Final-answer correctness still requires the separate human review.',
                'The scripted count is a possibility witness, not an observed internal model algorithm.',
                'Two reused development tasks; no novel-method advantage established.'])
    require(all(snapshot(Path(p)) == hashes for p, hashes in before.items()), 'Inputs changed')
    output.mkdir(parents=True, exist_ok=False)
    with (output/'report.json').open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    with (output/'audit_source.py').open('xb') as f: f.write(Path(__file__).read_bytes())
    hashes = snapshot(output)
    with (output/'artifact_hashes.json').open('x', encoding='utf-8') as f:
        json.dump(hashes, f, indent=2)
    return {k:v for k,v in result.items() if k != 'rows'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1], args.output), indent=2))
