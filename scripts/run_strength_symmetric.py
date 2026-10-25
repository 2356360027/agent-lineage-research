"""Prepare offline; new 0.25-CNY batch requires explicit fresh approval.

Execute/audit only the frozen copy. No retry/resume. Key hidden in terminal,
passed in memory via owned worker stdin; never saved. 600s call horizon, 630s
outer worker limit; cleanup is additional. Audit never launches inference.
"""
import argparse
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import sqlite3
import sys
import time
import warnings

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from runtime import bird_readonly_audit as audit
from runtime.cache_route_symmetric_batch import build_plan, validate, run
from runtime.cache_route_symmetric_capacity import SymmetricCapacity
from runtime.cache_route_symmetric_batch_audit import finished_batch, stopped_inventory
from runtime.cache_route_symmetric_readout import summarize
from runtime.deepseek_official_transport import OfficialTransport, validate_key
from scripts.run_superhero_factorial import owned_worker, FILES as OLD_FILES, day
from scripts.run_cache_action import verify_archive
from scripts.run_cache_workflow import counter_for
from scripts.fetch_bird_fixed import no_link_ancestors

SELF='scripts/run_strength_symmetric.py'
INPUT_SEAL='ba4976e3c1de96edb2396f46bdca3e3838ab6ed421096f4f4e8bbb47edf9b630'
SOURCE_PLAN_SHA='68ccc578404b8d9a35ba93c2c689a74589ae0ecfab04d7efc6becdfac619cacf'
DATABASE_SHA='75e94a2c3236ee3bb2c01fb97a1c4b4c1c269bcefd4eab1d04be323d2d0825b1'
DRAFT='reports/cache_relation_review_20261025/CLARIFIED_PAIR_REVIEW_V1.txt'
DRAFT_SHA='3172efd672168824195ac05d62e07a97c2f0b2cfafaf8f5b7953f63372f015d5'
ADMISSION='configs/run_records/strength_clarified_admission_20261025_001.json'
POLICY='configs/strength_symmetric_readout_v1.json'
PRICE_DATE='2026-10-25'
LIMITS=dict(max_calls=16,max_tool_attempts=12,max_seconds=600,outer_worker_seconds=630,
    max_output_tokens=512,ceiling_micro_cny=250000,rates_micro_cny_per_token=[2,8],
    conditional_micro_cny=191200,price_checked_date=PRICE_DATE)
FILES=tuple(dict.fromkeys((*OLD_FILES,SELF,POLICY,DRAFT,ADMISSION,
    'runtime/cache_action_trace_audit.py','runtime/superhero_answer_readout.py',
    *('runtime/cache_route_symmetric_'+name+'.py' for name in
      ('design','tools','loop','capacity','adapter','batch','readout','replay','batch_audit')))))


def read(path):return json.loads(Path(path).read_text('utf-8'))


def frozen(root):
    if Path(__file__).resolve()!=root/'source'/SELF:raise ValueError('Use frozen copied launcher')
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('runtime.','scripts.')) and getattr(module,'__file__',None):
            location=Path(module.__file__).resolve()
            if not location.is_relative_to(root/'source') or location.relative_to(root/'source').as_posix() not in FILES:
                raise ValueError('Unfrozen project import')


def verify(root,sha):
    root=Path(root).resolve();no_link_ancestors(root)
    if audit.hash_file(root/'manifest.json')!=sha:raise ValueError('Manifest changed')
    m=read(root/'manifest.json');verify_archive(root/'inputs',INPUT_SEAL)
    entries=list((root/'source').rglob('*'))
    for p in entries:no_link_ancestors(p)
    actual={p.relative_to(root/'source').as_posix():audit.hash_file(p) for p in entries if p.is_file()}
    if actual!=m['source_files'] or set(actual)!=set(FILES):raise ValueError('Source inventory or bytes changed')
    if (any(m.get(k)!=v or type(m.get(k)) is not type(v) for k,v in LIMITS.items())
            or m['database_sha256']!=DATABASE_SHA or m['python']!=sys.version
            or m['sqlite_version']!=sqlite3.sqlite_version):raise ValueError('Limits or runtime changed')
    if audit.hash_file(root/'source'/DRAFT)!=DRAFT_SHA:raise ValueError('Reviewed task changed')
    admission=read(root/'source'/ADMISSION)
    if (admission['status']!='ADAPTED_DEVELOPMENT_PAIR_READONLY_ADMISSION_AND_BINDING_COMPLETE'
            or admission['draft_sha256']!=DRAFT_SHA
            or admission['binding']['database_sha256']!=DATABASE_SHA):raise ValueError('Missing task admission')
    if audit.hash_file(root/'source'/POLICY)!=m['readout_policy_sha256']:raise ValueError('Readout changed')
    inputs=read(root/'inputs/inputs.json')
    if inputs['plan_sha256']!=SOURCE_PLAN_SHA:raise ValueError('Preflight plan changed')
    common=read(root/'inputs/PRIVATE_common.json');cache=read(root/'inputs/PRIVATE_cache.json')
    expected=build_plan(common,cache,SOURCE_PLAN_SHA);plan=read(root/'PRIVATE_batch_plan.json')
    if audit.encoded(plan)!=audit.encoded(expected):raise ValueError('Prepared plan differs from preflight')
    validate(plan,cache,m['plan_sha256'])
    db=Path(m['database']);no_link_ancestors(db)
    if (common['database_sha256']!=DATABASE_SHA or audit.hash_file(db)!=DATABASE_SHA
            or any(Path(str(db)+s).exists() for s in ('-wal','-shm','-journal'))):raise ValueError('Database changed or has sidecars')
    # Reference remains private; build_plan accepts only public common fields.
    if audit.hash_file(root/'inputs/PRIVATE_reference.json')!=m['reference_file_sha256']:
        raise ValueError('Reference changed')
    counter=counter_for(m)
    capacities=[dict(id=j['id'],**SymmetricCapacity(j['envelope'],common,counter).certificate()) for j in plan['jobs']]
    cost=sum(c['total_input_bound']*2+c['total_output_bound']*8 for c in capacities)
    if capacities!=m['capacities'] or cost!=LIMITS['conditional_micro_cny'] or cost>250000:
        raise ValueError('Capacity or fee changed')
    return m,plan,cache,counter


def prepare(project,output):
    project=Path(project).resolve();output=Path(output).resolve();no_link_ancestors(output)
    if output.exists():raise ValueError('Never overwrite a preparation')
    inputs=project/'runtime/runs/strength-symmetric-preflight-20261025-001'
    verify_archive(inputs,INPUT_SEAL)
    old=read(inputs/'inputs.json')['tokenizer_files'];counter=counter_for(old)
    common=read(inputs/'PRIVATE_common.json');cache=read(inputs/'PRIVATE_cache.json')
    plan=build_plan(common,cache,SOURCE_PLAN_SHA)
    capacities=[dict(id=j['id'],**SymmetricCapacity(j['envelope'],common,counter).certificate()) for j in plan['jobs']]
    output.mkdir(parents=True,exist_ok=False)
    for p in inputs.rglob('*'):
        if p.is_file():
            target=output/'inputs'/p.relative_to(inputs);target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as f:f.write(p.read_bytes())
    for name in FILES:
        source=project/name;no_link_ancestors(source)
        target=output/'source'/name;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f:f.write(source.read_bytes())
    audit.save_new(output/'PRIVATE_batch_plan.json',plan)
    db=project/'runtime/runs/datasets/superhero-fixed-20261025-002/databases/superhero/superhero.sqlite'
    m=dict(status='PREPARED_NOT_AUTHORIZED',**LIMITS,
        source_files={name:audit.hash_file(output/'source'/name) for name in FILES},
        input_manifest_sha256=INPUT_SEAL,plan_sha256=audit.digest(plan),capacities=capacities,
        reference_file_sha256=audit.hash_file(output/'inputs/PRIVATE_reference.json'),
        readout_policy_sha256=audit.hash_file(output/'source'/POLICY),
        database=str(db),database_sha256=DATABASE_SHA,python=sys.version,sqlite_version=sqlite3.sqlite_version,
        model='deepseek-flash',advertised_version='DeepSeek-V4.1-Flash-1017',
        thinking='disabled',temperature=0,actual_remote_weights_verified=False,
        endpoint='https://api.deepseek.com/v1/chat/completions',automatic_retry=False,
        independent_task_pairs=1,dependent_conditions=4,researcher_adaptation=True,
        pricing_source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
        settings_source='https://api-docs.deepseek.com/guides/thinking_mode/',
        price_basis='Peak cache-miss rates, no discounts assumed; conditional reference-token bound',
        created_utc=datetime.now(timezone.utc).isoformat(),**old)
    audit.save_new(output/'manifest.json',m)
    sha=audit.hash_file(output/'manifest.json');verify(output,sha)
    return dict(status=m['status'],prepared=str(output),manifest_sha256=sha,model_calls=0,
                conditional_upper_cny=.1912,proposed_ceiling_cny=.25)


def worker(packet):
    if type(packet) is not dict or set(packet)!={'prepared','sha','key'}:raise ValueError('Invalid worker packet')
    root=Path(packet['prepared']).resolve();frozen(root)
    m,plan,cache,counter=verify(root,packet['sha'])
    claim=read(root/'execution_claim.json')
    if claim.get('manifest_sha256')!=packet['sha'] or claim.get('approved_cny')!=.25 or day()!=PRICE_DATE:
        raise ValueError('Missing current batch approval')
    validate_key(packet['key'])
    result=run(plan,cache,expected_plan_sha=m['plan_sha256'],database=m['database'],output=root/'experiment',
        counter=counter,transport_factory=lambda ident,log:OfficialTransport(packet['key'],log),
        deadline=time.monotonic()+600,mode='OFFICIAL_DEVELOPMENT',ceiling_micro_cny=250000,input_rate=2,output_rate=8)
    return dict(status=result['status'],dispatch_attempts=result['dispatch_attempts'])


def execute(root,sha,approved):
    if type(approved) not in (int,float) or approved!=.25:raise ValueError('Fresh fixed-batch 0.25 CNY approval required')
    root=Path(root).resolve();frozen(root);verify(root,sha)
    if day()!=PRICE_DATE:raise ValueError('Recheck prices and prepare a new version for another date')
    if (root/'execution_claim.json').exists() or (root/'experiment').exists():raise ValueError('Already claimed; no resume')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=getpass.getpass('Official DeepSeek API key (hidden, not saved): ')
    validate_key(key)
    audit.save_new(root/'execution_claim.json',dict(manifest_sha256=sha,approved_cny=.25,
        utc=datetime.now(timezone.utc).isoformat(),automatic_retry=False))
    packet=json.dumps(dict(prepared=str(root),sha=sha,key=key)).encode('utf-8')
    try:outcome=owned_worker([sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),'--worker'],packet,root/'source')
    except BaseException as exc:
        audit.save_new(root/'launch_failure.json',dict(error_type=type(exc).__name__,automatic_retry=False));raise
    audit.save_new(root/'launch_outcome.json',outcome)
    return dict(outcome,output=str(root/'experiment'))


def analyze(root,sha):
    root=Path(root).resolve();frozen(root)
    m,plan,cache,counter=verify(root,sha)
    claim=read(root/'execution_claim.json');outcome=read(root/'launch_outcome.json')
    if (claim.get('manifest_sha256')!=sha or claim.get('approved_cny')!=.25
            or claim.get('automatic_retry') is not False):raise ValueError('Execution claim differs')
    # A hard-killed/unsealed process remains a forensic failure; never invent
    # a complete batch summary or restart it to obtain analyzable results.
    if outcome.get('status') not in ('OFFICIAL_DEVELOPMENT_FINISHED','OFFICIAL_DEVELOPMENT_STOPPED'):
        raise ValueError('Parent did not confirm normal worker closure; retain failure artifacts for manual audit')
    summary=read(root/'experiment/summary.json')
    if {k:summary[k] for k in ('status','dispatch_attempts')}!=outcome:
        raise ValueError('Parent/worker outcome mismatch')
    fn=finished_batch if summary['status'].endswith('_FINISHED') else stopped_inventory
    result=fn(root/'experiment',plan,cache,counter,expected_plan_sha=m['plan_sha256'],mode='OFFICIAL_DEVELOPMENT',
              ceiling_micro_cny=250000,input_rate=2,output_rate=8)
    output=root/'analysis';output.mkdir(exist_ok=False)
    audit.save_new(output/'audit.json',result)
    if summary['status'].endswith('_FINISHED'):
        audit.save_new(output/'readout.json',summarize(result['rows'],read(root/'inputs/PRIVATE_reference.json')))
    audit.save_new(output/'artifact_hashes.json',{p.name:audit.hash_file(p) for p in sorted(output.iterdir())})
    return dict(status=result['status'],output=str(output),model_calls=0)


def main():
    if sys.argv[1:]==['--worker']:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise ValueError('Worker packet too large')
        result=worker(json.loads(raw))
    else:
        p=argparse.ArgumentParser(description=__doc__);group=p.add_mutually_exclusive_group()
        for name in ('execute','verify','audit'):group.add_argument('--'+name,action='store_true')
        p.add_argument('--prepared',type=Path);p.add_argument('--manifest-sha256');p.add_argument('--approve-cny',type=float)
        p.add_argument('--output',type=Path);args=p.parse_args()
        if args.execute or args.verify or args.audit:
            if not args.prepared or not args.manifest_sha256:p.error('Prepared path and manifest hash required')
            if args.execute:result=execute(args.prepared,args.manifest_sha256,args.approve_cny)
            elif args.audit:result=analyze(args.prepared,args.manifest_sha256)
            else:
                verify(args.prepared,args.manifest_sha256);frozen(args.prepared.resolve())
                result=dict(status='FROZEN_PREFLIGHT_PASSED',model_calls=0)
        else:
            if not args.output:p.error('New output directory required')
            result=prepare(Path(__file__).resolve().parents[1],args.output)
    print(json.dumps(result,indent=2,ensure_ascii=True))


if __name__=='__main__':main()
