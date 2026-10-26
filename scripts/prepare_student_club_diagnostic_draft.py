"""Generate sealed OFFLINE draft inputs; no SQL, transport, price or approval."""
import argparse
import json
from pathlib import Path
import shutil
from runtime import bird_readonly_audit as bird
from runtime.arcwise_compatibility import verify_arcwise
from runtime.student_club_diagnostic_design import build
from scripts.check_student_club_reference import sealed
from scripts.audit_student_club_structure import TASK_SHA, DB_SHA
from scripts.audit_cache_action import snapshot
from scripts.fetch_bird_fixed import read_json


def run(project, output):
    project,output=Path(project).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(project/'runtime/runs'):
        raise ValueError('New private output required')
    reference=project/'runtime/runs/student-club-reference-20261026-001'
    sealed(reference,'artifact_hashes.json')
    if bird.hash_file(reference/'artifact_hashes.json')!='87f7a03c676893c157a1bd87fb88da086c3559107b039373904e88c694000f84':
        raise ValueError('Reference binding differs')
    taskdir=project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001'
    _,tasks=verify_arcwise(taskdir)
    if bird.hash_file(taskdir/'arcwise_plat_full_with_diff.json')!=TASK_SHA:
        raise ValueError('Tasks changed')
    # Relevant field projection is explicit; source contains additional tables/columns.
    schema=('Relevant schema projection (not the full schema): '
        'event(event_id TEXT PRIMARY KEY, event_name TEXT, event_date TEXT); '
        'attendance(link_to_event TEXT REFERENCES event(event_id), '
        'link_to_member TEXT REFERENCES member(member_id), '
        'PRIMARY KEY(link_to_event,link_to_member)); '
        'member(member_id TEXT PRIMARY KEY,t_shirt_size TEXT). '
        'No event-time shirt-size field is provided. '
        'Student_Club students denotes the relevant member records, without an extra identity filter.')
    common=dict(current_request=tasks[1317]['question'],current_evidence=tasks[1317]['evidence'],
        history_request=tasks[1371]['question'],history_evidence=tasks[1371]['evidence'],
        history_sql=tasks[1371]['SQL'],schema_projection=schema,database_sha256=DB_SHA)
    plan=build(common)
    policy=read_json(project/'configs/student_club_diagnostic_policy_v1.json')
    if policy['resources']['max_model_calls']!=plan['max_model_calls']:
        raise ValueError('Resource proposal mismatch')
    output.mkdir(parents=True,exist_ok=False)
    bird.save_new(output/'PUBLIC_INPUTS_PRIVATE_DISTRIBUTION.json',common)
    bird.save_new(output/'PRIVATE_draft_plan.json',plan)
    # Separate oracle input never becomes part of common or prompts.
    bird.save_new(output/'PRIVATE_reference_binding.json',dict(
        source_seal_sha256=bird.hash_file(reference/'artifact_hashes.json'),
        summary=read_json(reference/'summary.json'),not_for_model_input=True))
    for rel in ('runtime/student_club_diagnostic_design.py','scripts/prepare_student_club_diagnostic_draft.py',
                'configs/student_club_diagnostic_policy_v1.json'):
        dest=output/'design_source'/rel;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(project/rel,dest)
    bird.save_new(output/'draft_artifact_hashes.json',snapshot(output))
    sealed(output,'draft_artifact_hashes.json')
    return dict(status=plan['status'],jobs=len(plan['jobs']),model_calls=0,
        max_calls_proposed=plan['max_model_calls'],runner_ready=False,
        artifact_sha256=bird.hash_file(output/'draft_artifact_hashes.json'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],a.output),indent=2))
