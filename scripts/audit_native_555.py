"""Bounded local snapshot diagnostics, not human admission or model evaluation."""
import argparse
import json
import shutil
from pathlib import Path

from runtime import bird_readonly_audit as bird
from runtime.arcwise_compatibility import verify_arcwise
from scripts.audit_cache_action import no_links, snapshot

DB_SHA = '92101be6d2a9f6adceea59d38f6d1c556087f9eea1432432a21268cb1348036d'
REVIEW_SHA = None  # Supplied explicitly on the command line; never inferred as approval.
SCOPE = "FROM posts AS p JOIN users AS u ON p.OwnerUserId=u.Id WHERE u.DisplayName='csgillespie'"
QUERIES = {
    'schema': "SELECT name, sql FROM sqlite_schema WHERE type='table' AND name IN ('posts','users') ORDER BY name",
    'user_scope': "SELECT COUNT(*) AS matching_users, COUNT(DISTINCT Id) AS distinct_user_ids FROM users WHERE DisplayName='csgillespie'",
    'post_scope': 'SELECT COUNT(*) AS joined_rows, COUNT(p.Id) AS post_ids, COUNT(DISTINCT p.Id) AS distinct_post_ids, COUNT(p.Score) AS nonnull_scores, SUM(p.Score) AS score_sum, AVG(p.Score) AS score_avg ' + SCOPE,
    'score_types': 'SELECT typeof(p.Score) AS score_type, COUNT(*) AS n ' + SCOPE + ' GROUP BY typeof(p.Score) ORDER BY score_type',
}


def decode_scalar(cell):
    kind, value = cell
    if kind == 'integer':
        return int(value)
    if kind == 'real':
        return float.fromhex(value)
    if kind == 'null':
        return None
    if kind == 'text':
        return value
    raise ValueError('Unexpected diagnostic type')


def one_row(result):
    if not result.get('complete') or len(result.get('rows', [])) != 1:
        raise ValueError('Incomplete or non-scalar diagnostic; do not impute')
    row = result['rows'][0]
    if len(result['columns']) != len(row):
        raise ValueError('Invalid row width')
    return dict(zip(result['columns'], map(decode_scalar, row)))


def facts(results):
    users = one_row(results['user_scope'])
    posts = one_row(results['post_scope'])
    return {
        'matching_users': users['matching_users'],
        'distinct_user_ids': users['distinct_user_ids'],
        'joined_rows': posts['joined_rows'],
        'distinct_post_ids': posts['distinct_post_ids'],
        'null_score_rows': posts['joined_rows'] - posts['nonnull_scores'],
        'nonnull_scores': posts['nonnull_scores'],
        'join_multiplicity_observed': posts['joined_rows'] != posts['distinct_post_ids'],
        'empty_or_all_null_observed': posts['nonnull_scores'] == 0,
        'score_sum': posts['score_sum'], 'score_avg': posts['score_avg'],
        'formal_tasks_admitted': 0,
        'semantic_approval': False,
        'scope': 'Facts of this legacy snapshot only; not a decision about other snapshots or user intent',
    }


def run(project, output, review_source, review_sha):
    project, output, review_source = map(lambda p: Path(p).resolve(), (project, output, review_source))
    for p in (output, review_source):
        no_links(p)
    if output.exists():
        raise ValueError('Refuse overwrite')
    if bird.hash_file(review_source) != review_sha:
        raise ValueError('Review input hash mismatch')
    _, source = verify_arcwise(project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001')
    dbdir = project/'runtime/runs/datasets/bird-mini-fixed-20261005-002'
    db = dbdir/'databases/codebase_community/codebase_community.sqlite'
    no_links(db)
    before = bird.hash_file(db)
    manifest = json.loads((dbdir/'dataset_manifest.json').read_text(encoding='utf-8'))
    entry = [r for r in manifest['databases'] if r['db_id']=='codebase_community']
    if len(entry)!=1 or entry[0]['sha256']!=before or before!=DB_SHA or entry[0]['bytes']!=db.stat().st_size:
        raise ValueError('Pinned acquisition mismatch')
    queries = dict(QUERIES)
    for qid in (537, 555):
        if source[qid]['db_id']!='codebase_community':
            raise ValueError('Unexpected task database')
        queries[f'reference_{qid}'] = source[qid]['SQL']
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(review_source, output/'review-original.txt')
    shutil.copyfile(__file__, output/'audit_source.py')
    shutil.copyfile(bird.__file__, output/'readonly_source.py')
    bird.save_new(output/'design.json', {
        'purpose': 'Local snapshot diagnostic only; no model or human-label substitution',
        'queries': queries, 'limits': bird.LIMITS, 'model_calls': 0,
        'excluded_current_task': 1480, 'database_before_sha256': before,
        'review_sha256': review_sha,
        'review_provenance': 'Submitted text self-reports two independent humans; not independently verified',
        'source_json_sha256': bird.hash_file(project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001/arcwise_plat_full_with_diff.json'),
        'formal_tasks_admitted': 0,
    })
    results = {}
    try:
        for name, sql in queries.items():
            result = bird.run_child(db, sql)
            bird.save_new(output/(name+'.json'), result)
            results[name] = result
            if not result.get('complete'):
                raise ValueError(f'Incomplete query {name}: {result.get("status")}; no retry')
        result = facts(results)
        result.update(status='LOCAL_SNAPSHOT_DIAGNOSTICS_COMPLETE', query_calls=len(results), model_calls=0,
                      official_arcwise_database_verified=False)
        bird.save_new(output/'facts.json', result)
    except BaseException as exc:
        bird.save_new(output/'failure.json', {'type': type(exc).__name__, 'message': str(exc), 'automatic_retry': False})
        raise
    finally:
        after = bird.hash_file(db)
        bird.save_new(output/'database_after.json', {'sha256': after, 'unchanged': after==before})
        hashes = snapshot(output)
        bird.save_new(output/'artifact_hashes.json', hashes)
    if after != before:
        raise ValueError('Database changed; invalidate diagnostics')
    actual = snapshot(output); actual.pop('artifact_hashes.json')
    if actual != hashes:
        raise ValueError('Archive changed')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--review-source', type=Path, required=True)
    p.add_argument('--review-sha256', required=True)
    args = p.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1], args.output, args.review_source, args.review_sha256), indent=2))
