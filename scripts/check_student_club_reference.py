"""Four fixed, read-only aggregate checks after the recorded human supplement.

No model calls, no positive-roster construction, no automatic retries. Results
are snapshot-specific development checks, not universal SQL equivalence.
"""
import argparse
import json
from pathlib import Path
import re
import shutil

from runtime import bird_readonly_audit as bird
from runtime.arcwise_compatibility import verify_arcwise
from scripts import fetch_bird_fixed as fetch
from scripts.audit_cache_action import snapshot
from scripts.audit_student_club_structure import DB_SHA, TASK_SHA


ENTITY_H = """SELECT COUNT(DISTINCT m.member_id) FROM member m
WHERE EXISTS (SELECT 1 FROM attendance a JOIN event e ON e.event_id=a.link_to_event
WHERE a.link_to_member=m.member_id
AND e.event_name COLLATE BINARY = 'Women''s Soccer')"""
ENTITY_C = ENTITY_H + " AND m.t_shirt_size COLLATE BINARY = 'Medium'"


def scalar(response):
    if response.get('complete') is not True or response.get('status') != 'EXECUTED':
        raise ValueError('Incomplete query; retain failure and stop')
    rows = response.get('rows')
    if (not isinstance(rows, list) or len(rows) != 1 or len(rows[0]) != 1
            or len(response.get('columns', [])) != 1):
        raise ValueError('Expected a single count')
    value = rows[0][0]
    if (not isinstance(value,list) or len(value)!=2 or value[0]!='integer'
            or not isinstance(value[1],str) or not re.fullmatch(r'0|[1-9][0-9]*',value[1])):
        raise ValueError('Expected a nonnegative integer count')
    return int(value[1])


def sealed(path, manifest):
    actual = snapshot(path)
    actual.pop(manifest)
    if actual != fetch.read_json(path/manifest):
        raise ValueError('Changed sealed inputs')


def run(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    fetch.absent(output)
    if not output.is_relative_to(project/'runtime/runs'):
        raise ValueError('Private output required')
    data = project/'runtime/runs/datasets/student-club-fixed-20261026-002'
    intake = project/'runtime/runs/student-club-review-intake-20261026-001'
    structure = project/'runtime/runs/student-club-structure-20261026-001'
    sealed(data,'preparation_artifact_hashes.json')
    sealed(structure,'artifact_hashes.json')
    confirmation = fetch.read_json(intake/'admission_supplement.json')
    process = fetch.read_json(intake/'process_confirmation.json')
    original_sha = bird.hash_file(intake/'submitted_reviews_original.txt')
    if (confirmation['original_sha256'] != original_sha or process['original_sha256'] != original_sha
            or process['independent_before_model_judgment_confirmed_by_user'] is not True
            or confirmation['reviewer_2_readonly_negative_control_agreement'] is not True
            or confirmation['students_means_relevant_members_no_extra_identity_filter'] is not True):
        raise ValueError('Human supplement missing or inconsistent')
    taskdir = project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001'
    _, tasks = verify_arcwise(taskdir)
    if bird.hash_file(taskdir/'arcwise_plat_full_with_diff.json') != TASK_SHA:
        raise ValueError('Task source changed')
    db = data/'databases/student_club/student_club.sqlite'
    if bird.hash_file(db) != DB_SHA:
        raise ValueError('Database changed')
    bird.verify_download(data,['student_club'],{'student_club':DB_SHA})
    if any(tasks[i]['db_id'] != 'student_club' for i in (1371,1317)):
        raise ValueError('Wrong task database')
    queries = dict(original_H=tasks[1371]['SQL'], original_C=tasks[1317]['SQL'],
                   entity_H=ENTITY_H, entity_C=ENTITY_C)
    before = snapshot(data)
    intake_before = snapshot(intake)
    output.mkdir(parents=True,exist_ok=False)
    try:
        for rel in ('scripts/check_student_club_reference.py','scripts/audit_student_club_structure.py',
                    'scripts/audit_cache_action.py','scripts/fetch_bird_fixed.py',
                    'runtime/bird_readonly_audit.py','runtime/arcwise_compatibility.py'):
            dest = output/'source'/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(project/rel,dest)
        for p in intake.iterdir():
            if p.is_file():
                (output/'private_review').mkdir(exist_ok=True)
                shutil.copyfile(p,output/'private_review'/p.name)
        bird.save_new(output/'plan.json',dict(queries=queries,database_sha256=DB_SHA,
            task_sha256=TASK_SHA,limits=bird.LIMITS,model_calls=0,automatic_retry=False,
            scope='One fixed legacy snapshot; four count queries; not human semantic inference',
            input_review_hashes=intake_before,structure_seal_sha256=bird.hash_file(structure/'artifact_hashes.json')))
        values = {}
        for name,sql in queries.items():
            bird.save_new(output/(name+'.pending.json'),{'sql':sql})
            response = bird.run_child(db,sql)
            bird.save_new(output/(name+'.response.json'),response)
            values[name] = scalar(response)
        if snapshot(data)!=before or snapshot(intake)!=intake_before:
            raise ValueError('Inputs changed during check')
        agreement = all(values['original_'+x]==values['entity_'+x] for x in ('H','C'))
        result = dict(status='REFERENCE_CHECK_COMPLETE' if agreement else 'REFERENCE_DISAGREEMENT_REQUIRES_REVIEW',
            counts=values,original_and_entity_counts_agree=agreement,
            database_unchanged=True,readonly_queries=4,model_calls=0,
            limitations=['One researcher-paired development case, not independent confirmation',
                         'Legacy snapshot not certified as corrected Arcwise database',
                         'Output equality here is not universal SQL equivalence',
                         'No cache-induced interpretation shift or method benefit tested',
                         'No paid batch authorized; positive roster not admitted'])
        bird.save_new(output/'summary.json',result)
    except BaseException as exc:
        bird.save_new(output/'failure.json',dict(type=type(exc).__name__,message=str(exc),automatic_retry=False))
        raise
    finally:
        bird.save_new(output/'artifact_hashes.json',snapshot(output))
    sealed(output,'artifact_hashes.json')
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],a.output),indent=2))
