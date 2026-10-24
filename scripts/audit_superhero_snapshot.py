"""Three bounded read-only snapshot checks; no model or semantic admission."""
import argparse
import json
import shutil
from pathlib import Path

from runtime import bird_readonly_audit as bird
from runtime.arcwise_compatibility import verify_arcwise
from scripts.audit_cache_action import no_links, snapshot
from scripts.audit_native_555 import decode_scalar
from scripts.audit_native_positive_relation import gold_count, SOURCE_SHA
from scripts.fetch_bird_fixed import validate_metadata, file_receipt

DB_SHA = '75e94a2c3236ee3bb2c01fb97a1c4b4c1c269bcefd4eab1d04be323d2d0825b1'


def compare_results(history, current):
    if not history.get('complete') or not current.get('complete'):
        raise ValueError('Incomplete snapshot results; never impute')
    rows = [[decode_scalar(cell) for cell in row] for row in history['rows']]
    derived = gold_count(history['columns'], rows, complete=True)
    direct = current['rows']
    if len(direct) != 1 or len(direct[0]) != 1:
        raise ValueError('Current result is not a scalar')
    direct_value = decode_scalar(direct[0][0])
    if type(direct_value) is not int or direct_value < 0:
        raise ValueError('Current result is not a nonnegative count')
    return {'historical_group_rows': len(rows), 'derived_count': derived,
            'reference_count': direct_value, 'matches_on_this_snapshot': derived == direct_value,
            'semantic_approval': False, 'formal_tasks_admitted': 0,
            'scope': 'One legacy database compatibility diagnostic, not a model result or universal rewrite proof'}


def run(project, assets, output):
    project, assets, output = [Path(p).resolve() for p in (project, assets, output)]
    for p in (assets, output): no_links(p)
    if output.exists(): raise ValueError('Refuse overwrite')
    seal = json.loads((assets/'preparation_artifact_hashes.json').read_text())
    actual = snapshot(assets); actual.pop('preparation_artifact_hashes.json')
    if seal != actual: raise ValueError('Acquisition artifact seal mismatch')
    validate_metadata(assets)
    acquisition = json.loads((assets/'dataset_manifest.json').read_text())
    db = assets/'databases/superhero/superhero.sqlite'
    before = file_receipt(db)
    if acquisition['stage'] != 'databases_complete' or acquisition['selected_db_ids'] != ['superhero']:
        raise ValueError('Unexpected acquisition')
    record, = acquisition['databases']
    if record['sha256'] != before['sha256'] or before['sha256'] != DB_SHA or record['bytes'] != before['bytes']:
        raise ValueError('Database hash or size mismatch')
    source_dir = project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001'
    _, source = verify_arcwise(source_dir)
    if bird.hash_file(source_dir/'arcwise_plat_full_with_diff.json') != SOURCE_SHA:
        raise ValueError('Unexpected question source')
    queries = {'schema': "SELECT name, sql FROM sqlite_schema WHERE type='table' AND name IN ('superhero','colour','publisher') ORDER BY name",
               'history_728': source[728]['SQL'], 'current_733': source[733]['SQL']}
    output.mkdir(parents=True, exist_ok=False)
    for p in (Path(__file__), Path(bird.__file__), project/'scripts/audit_native_positive_relation.py', project/'scripts/audit_native_555.py'):
        shutil.copyfile(p, output/p.name)
    bird.save_new(output/'design.json', {'queries': queries, 'limits': bird.LIMITS,
        'database_before': before, 'source_sha256': SOURCE_SHA, 'max_readonly_calls': 3,
        'human_semantics_pending': True, 'formal_tasks_admitted': 0, 'model_calls': 0,
        'official_arcwise_database_verified': False})
    results = {}
    try:
        for name, query in queries.items():
            result = bird.run_child(db, query)
            bird.save_new(output/(name+'.json'), result)
            results[name] = result
            if not result.get('complete'): raise ValueError('Incomplete query: '+name)
        summary = compare_results(results['history_728'], results['current_733'])
        summary.update(status='LOCAL_SNAPSHOT_CHECK_COMPLETE', readonly_calls=3, model_calls=0)
        bird.save_new(output/'summary.json', summary)
        if not summary['matches_on_this_snapshot']:
            raise ValueError('Snapshot conversion mismatch; preserve, do not repair by changing results')
    except BaseException as exc:
        bird.save_new(output/'failure.json', {'type': type(exc).__name__, 'message': str(exc), 'automatic_retry': False})
        raise
    finally:
        after = file_receipt(db)
        bird.save_new(output/'database_after.json', {'receipt': after, 'unchanged': before == after})
        hashes = snapshot(output)
        bird.save_new(output/'artifact_hashes.json', hashes)
    actual = snapshot(output); actual.pop('artifact_hashes.json')
    if actual != hashes or after != before: raise ValueError('Artifact or database changed')
    return {k: v for k, v in summary.items() if k not in ('derived_count', 'reference_count')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1], args.assets, args.output), indent=2))
