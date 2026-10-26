"""Offline frozen review handoff: export A, lock two returned forms, release B.

Never calls a provider or executes SQL. Distribute only the named reviewer's
form, not the PRIVATE directory, archive, mapping or the other person's form.
"""
import argparse
import json
from pathlib import Path
import secrets
import sys

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from runtime import bird_readonly_audit as audit
from runtime.native_trajectory_replay import equal
from runtime.student_club_input_admission import load
from runtime.student_club_review_files import export,lock_stage_a,release_stage_b
from scripts.prepare_student_club_runtime import verify,counters
from scripts.fetch_bird_fixed import read_json

SELF='scripts/manage_student_club_review.py'
SOURCES={'tool_source.py':'runtime/cache_route_symmetric_tools.py',
         'base_tool_source.py':'runtime/cache_intent_tools.py',
         'readonly_source.py':'runtime/bird_readonly_audit.py',
         'column_scoped_source.py':'runtime/student_club_readonly.py'}


def export_frozen(root,sha):
    root=Path(root).absolute()
    if Path(__file__).resolve()!=root/'source'/SELF:raise ValueError('Run the frozen review manager')
    verify(root,sha,require_frozen=True)
    m=read_json(root/'manifest.json');receipt=read_json(root/'archive_receipt.json')
    equal(receipt['manifest_sha256'],sha,'Launcher/manifest binding')
    equal(receipt['worker_returned'],True,'Worker closure required')
    summary=read_json(root/'experiment/summary.json')
    equal(read_json(root/'launch_outcome.json'),{k:summary[k] for k in ('status','dispatch_attempts')},'Worker outcome binding')
    inputs=load(root/'evidence');plain,native=counters(root,m)
    result=export(root/'experiment',read_json(root/'PRIVATE_plan.json'),inputs['common'],inputs['cached_result'],
        output=root/'review',masking_seed=secrets.token_hex(32),expected_plan_sha=m['plan_sha256'],
        expected_archive_sha=receipt['archive_sha256'],native_counter=native,plain_counter=plain,
        frozen_tool_sources={name:m['source_files'][source] for name,source in SOURCES.items()},
        mode='OFFICIAL_NATIVE_DEVELOPMENT')
    return dict(result,review_directory=str(root/'review'),handoff_sha256=audit.hash_file(root/'review/handoff.json'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('export-a','lock-a','release-b'))
    p.add_argument('--prepared',type=Path,required=True);p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--handoff-sha256');p.add_argument('--reviewer',type=int,choices=(1,2));p.add_argument('--submission',type=Path)
    p.add_argument('--lock-1-sha256');p.add_argument('--lock-2-sha256');a=p.parse_args()
    root=a.prepared.absolute()
    if Path(__file__).resolve()!=root/'source'/SELF:raise ValueError('Run the frozen review manager')
    verify(root,a.manifest_sha256,require_frozen=True)
    if a.action=='export-a':result=export_frozen(root,a.manifest_sha256)
    elif a.action=='lock-a':
        if not a.handoff_sha256 or not a.reviewer or not a.submission:p.error('Handoff seal, reviewer and submission required')
        result=lock_stage_a(root/'review',a.submission,reviewer_number=a.reviewer,expected_handoff_sha=a.handoff_sha256)
    else:
        if not all((a.handoff_sha256,a.lock_1_sha256,a.lock_2_sha256)):p.error('Handoff and both review lock seals required')
        result=release_stage_b(root/'review',expected_handoff_sha=a.handoff_sha256,expected_lock_shas={1:a.lock_1_sha256,2:a.lock_2_sha256})
    print(json.dumps(result,indent=2,ensure_ascii=True))


if __name__=='__main__':main()
