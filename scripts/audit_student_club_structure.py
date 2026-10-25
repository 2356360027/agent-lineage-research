"""Fixed aggregate-only development audit; no model calls or task admission."""
import argparse
import json
from pathlib import Path
import shutil

from scripts import fetch_bird_fixed as fetch
from scripts.audit_cache_action import snapshot
from runtime import bird_readonly_audit as bird
from runtime.arcwise_compatibility import verify_arcwise

DB_SHA = 'eb89bcfe97eefa386a27904ec5aa15159811a7eac894ec659a36e48fa9f76b77'
TASK_SHA = 'baefe2ca4fbab86c000df72aad9eaa563f7d422e425af2cbff071f656ea4eea8'
EVENT = "event_name COLLATE BINARY = 'Women''s Soccer'"
TARGET = f"SELECT event_id FROM event WHERE {EVENT}"
QUERIES = {
    'event_scope': f"SELECT COUNT(*) AS event_rows, COUNT(DISTINCT event_id) AS event_ids, COUNT(DISTINCT event_date) AS dates, SUM(CASE WHEN event_id IS NULL THEN 1 ELSE 0 END) AS null_ids FROM event WHERE {EVENT}",
    'attendance_scope': f"SELECT COUNT(*) AS attendance_rows, COUNT(link_to_member) AS nonnull_member_links, COUNT(DISTINCT link_to_member) AS distinct_member_links FROM attendance WHERE link_to_event IN ({TARGET})",
    'repeated_pairs': "SELECT COUNT(*) AS repeated_event_member_pairs FROM (SELECT link_to_event, link_to_member FROM attendance GROUP BY link_to_event, link_to_member HAVING COUNT(*) > 1)",
    'target_link_integrity': f"SELECT COUNT(*) AS rows_checked, COALESCE(SUM(CASE WHEN a.link_to_member IS NULL THEN 1 ELSE 0 END),0) AS null_links, COALESCE(SUM(CASE WHEN a.link_to_member IS NOT NULL AND NOT EXISTS (SELECT 1 FROM member m WHERE m.member_id=a.link_to_member) THEN 1 ELSE 0 END),0) AS orphan_links FROM attendance a WHERE a.link_to_event IN ({TARGET})",
    'member_identity': "SELECT COUNT(*) AS member_rows, COUNT(member_id) AS nonnull_ids, COUNT(DISTINCT member_id) AS distinct_ids FROM member",
    'event_identity': "SELECT COUNT(*) AS event_rows, COUNT(event_id) AS nonnull_ids, COUNT(DISTINCT event_id) AS distinct_ids FROM event",
    'target_size_quality': f"SELECT COUNT(*) AS joined_rows, COALESCE(SUM(CASE WHEN m.t_shirt_size IS NULL THEN 1 ELSE 0 END),0) AS null_sizes, COALESCE(SUM(CASE WHEN m.t_shirt_size = '' THEN 1 ELSE 0 END),0) AS empty_sizes, COALESCE(SUM(CASE WHEN typeof(m.t_shirt_size) NOT IN ('text','null') THEN 1 ELSE 0 END),0) AS nontext_sizes FROM attendance a JOIN member m ON m.member_id=a.link_to_member WHERE a.link_to_event IN ({TARGET})",
}


def integers(response):
    if not response.get('complete') or len(response.get('rows', [])) != 1:
        raise ValueError('Expected one complete aggregate row')
    values = response['rows'][0]
    if any(v[0] not in ('integer', 'null') for v in values):
        raise ValueError('Unexpected nonaggregate value')
    return dict(zip(response['columns'], [None if v[0]=='null' else int(v[1]) for v in values], strict=True))


def source_comparison(dataset, taskdir):
    _, tasks = verify_arcwise(taskdir)
    if bird.hash_file(taskdir/'arcwise_plat_full_with_diff.json') != TASK_SHA:
        raise ValueError('Task source changed')
    metadata = fetch.validate_metadata(dataset)
    sources = {'questions.jsonl': fetch.question_records((dataset/'questions.jsonl').read_bytes())[0]}
    for name in metadata['legacy_question_files']:
        sources[name] = fetch.read_json(dataset/name)
    rows = []
    for name, records in sources.items():
        if not isinstance(records, list):
            raise ValueError('Unrecognized legacy task container')
        for qid in (1317, 1371):
            matches = [r for r in records if r.get('question_id') == qid]
            rows.append(dict(source=name, question_id=qid, matches=len(matches),
                field_equal={k: matches[0].get(k)==tasks[qid].get(k) for k in ('db_id','question','evidence','SQL')} if len(matches)==1 else None))
    return rows


def run(project, dataset, output):
    project, dataset, output = map(lambda p: Path(p).resolve(), (project,dataset,output))
    fetch.absent(output)
    if not output.is_relative_to(project/'runtime/runs'):
        raise ValueError('Private output required')
    before_files = snapshot(dataset)
    actual = dict(before_files)
    actual.pop('preparation_artifact_hashes.json')
    if actual != fetch.read_json(dataset/'preparation_artifact_hashes.json'):
        raise ValueError('Dataset preparation seal changed')
    db = dataset/'databases/student_club/student_club.sqlite'
    if bird.hash_file(db) != DB_SHA:
        raise ValueError('Wrong fixed database')
    bird.verify_download(dataset, ['student_club'], {'student_club': DB_SHA})
    comparison = source_comparison(dataset, project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001')
    output.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copyfile(__file__, output/'audit_source.py')
        shutil.copyfile(bird.__file__, output/'readonly_source.py')
        bird.save_new(output/'plan.json', dict(database_sha256=DB_SHA, task_sha256=TASK_SHA,
            queries=QUERIES, limits=bird.LIMITS, source_comparison=comparison,
            purpose='Structural suitability only; not human semantic approval or model evaluation',
            original_reference_queries_executed=False, model_calls=0, automatic_retry=False))
        aggregates = {}
        for name, sql in QUERIES.items():
            bird.save_new(output/(name+'.pending.json'), {'sql':sql})
            response = bird.run_child(db, sql)
            bird.save_new(output/(name+'.response.json'), response)
            aggregates[name] = integers(response)
        if snapshot(dataset) != before_files:
            raise ValueError('Dataset bytes changed')
        result = dict(status='STRUCTURE_CHECKED_NOT_SEMANTICALLY_ADMITTED',
            database_sha256=DB_SHA, aggregates=aggregates, source_comparison=comparison,
            readonly_calls=len(QUERIES), model_calls=0, human_approvals=0,
            formal_tasks_admitted=0, original_reference_queries_executed=False,
            limitations=['One legacy snapshot, not certified as corrected Arcwise database',
                'Data uniqueness does not establish intended counting semantics',
                'Member size is stored without an event-time size field in the inspected schema',
                'No Medium subset target answer evaluated; no model-based screening',
                'Pair and any future complete-roster positive control are researcher-constructed'])
        bird.save_new(output/'summary.json', result)
    except BaseException as exc:
        bird.save_new(output/'failure.json', dict(type=type(exc).__name__, message=str(exc), automatic_retry=False))
        raise
    finally:
        bird.save_new(output/'artifact_hashes.json', snapshot(output))
    observed = snapshot(output)
    observed.pop('artifact_hashes.json')
    if observed != fetch.read_json(output/'artifact_hashes.json'):
        raise ValueError('Output seal changed')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1], args.dataset, args.output), indent=2))
