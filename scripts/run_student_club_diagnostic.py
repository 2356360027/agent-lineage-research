"""Frozen fixed-batch launcher; default verify only. No implicit paid run.

One development pair, twelve conditions, <=56 calls and <=36 readonly tool
attempts. Hidden credential input, 600-second calls / 630-second owned worker,
fresh 1.10-CNY approval. No automatic retry/resume or model training.
"""
import argparse
from datetime import datetime,timezone
import getpass
import json
from pathlib import Path
import sys
import time
import warnings

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from runtime import bird_readonly_audit as audit
from runtime.student_club_input_admission import load
from runtime.student_club_native_batch import run
from runtime.student_club_native_transport import Transport,validate_key
from runtime.native_action_interface import _load
from scripts.prepare_student_club_runtime import verify,counters,LIVE_LIMITS
from scripts.run_superhero_factorial import owned_worker
from scripts.run_cache_workflow import day
from scripts.fetch_bird_fixed import read_json

SELF='scripts/run_student_club_diagnostic.py'
MODE='OFFICIAL_NATIVE_DEVELOPMENT'
APPROVED_CNY=1.10
PRICE_DATE=LIVE_LIMITS['price_checked_date']


def preflight(root,sha):
    root=Path(root).absolute()
    if Path(__file__).resolve()!=root/'source'/SELF:raise ValueError('Use the copied frozen launcher')
    verify(root,sha,require_frozen=True)
    return read_json(root/'manifest.json')


def validate_claim(claim,sha):
    if (type(claim) is not dict or set(claim)!={'manifest_sha256','approved_cny','utc','automatic_retry'}
            or claim['manifest_sha256']!=sha or type(claim['approved_cny']) not in (int,float)
            or claim['approved_cny']!=APPROVED_CNY or claim['automatic_retry'] is not False
            or type(claim['utc']) is not str):raise ValueError('Matching fixed approval claim required')
    stamp=datetime.fromisoformat(claim['utc'])
    if stamp.utcoffset() is None:raise ValueError('Timezone required')


def worker(packet):
    if type(packet) is not dict or set(packet)!={'prepared','sha','key'}:raise ValueError('Worker packet schema')
    root=Path(packet['prepared']).absolute()
    m=preflight(root,packet['sha'])
    validate_claim(read_json(root/'execution_claim.json'),packet['sha'])
    if day()!=PRICE_DATE:raise ValueError('Prices require new dated review')
    if (root/'experiment').exists():raise ValueError('No retry or resume')
    validate_key(packet['key'])
    inputs=load(root/'evidence');plan=read_json(root/'PRIVATE_plan.json')
    plain,native=counters(root,m)
    # Recheck loaded project modules after dependency setup.
    preflight(root,packet['sha'])
    result=run(plan,inputs['common'],inputs['cached_result'],expected_plan_sha=m['plan_sha256'],
        database=inputs['database_path'],output=root/'experiment',native_counter=native,plain_counter=plain,
        transport_factory=lambda ident,log:Transport(packet['key'],log),
        deadline=time.monotonic()+LIVE_LIMITS['max_seconds'],mode=MODE)
    return dict(status=result['status'],dispatch_attempts=result['dispatch_attempts'])


def execute(root,sha,approved):
    if type(approved) not in (int,float) or approved!=APPROVED_CNY:
        raise ValueError('Fresh fixed 1.10-CNY approval required')
    root=Path(root).absolute();preflight(root,sha)
    if day()!=PRICE_DATE:raise ValueError('Recheck prices and prepare a new dated version')
    if (root/'execution_claim.json').exists() or (root/'experiment').exists():
        raise ValueError('No retry or resume, including failed starts')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=getpass.getpass('Official DeepSeek API key (hidden, not saved): ')
    validate_key(key)
    audit.save_new(root/'execution_claim.json',dict(manifest_sha256=sha,approved_cny=approved,
        utc=datetime.now(timezone.utc).isoformat(),automatic_retry=False))
    packet=json.dumps(dict(prepared=str(root),sha=sha,key=key)).encode('utf-8')
    try:
        outcome=owned_worker([sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),'--worker'],
            packet,root/'source',timeout=LIVE_LIMITS['outer_worker_seconds'])
    except BaseException as error:
        audit.save_new(root/'launch_failure.json',dict(error_type=type(error).__name__,automatic_retry=False))
        raise
    audit.save_new(root/'launch_outcome.json',outcome)
    if outcome.get('status') in (MODE+'_FINISHED',MODE+'_STOPPED'):
        summary=read_json(root/'experiment/summary.json')
        if outcome!={k:summary[k] for k in ('status','dispatch_attempts')}:
            raise ValueError('Parent/worker summary mismatch; retain all files')
        audit.save_new(root/'archive_receipt.json',dict(manifest_sha256=sha,
            archive_sha256=audit.hash_file(root/'experiment/artifact_hashes.json'),
            worker_returned=True,semantic_review_complete=False,full_trace_audit_complete=False))
    return dict(outcome,output=str(root/'experiment'))


def main():
    if sys.argv[1:]==['--worker']:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise ValueError('Worker packet too large')
        result=worker(_load(raw.decode('utf-8')))
    else:
        p=argparse.ArgumentParser(description=__doc__)
        p.add_argument('--execute',action='store_true')
        p.add_argument('--prepared',type=Path,required=True);p.add_argument('--manifest-sha256',required=True)
        p.add_argument('--approve-cny',type=float);a=p.parse_args()
        if a.execute:result=execute(a.prepared,a.manifest_sha256,a.approve_cny)
        else:
            preflight(a.prepared,a.manifest_sha256)
            result=dict(status='FROZEN_STUDENT_CLUB_PREFLIGHT_PASSED',api_calls=0,paid_approval=False)
    print(json.dumps(result,indent=2,ensure_ascii=True))


if __name__=='__main__':main()
