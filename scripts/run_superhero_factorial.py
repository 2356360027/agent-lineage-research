"""Offline preparation by default; fixed 0.20-CNY batch needs fresh approval.

Only the frozen copy can execute. Credentials use hidden input and worker stdin,
never files/arguments. Owned worker has a 630-second outer limit (600-second
inference horizon plus setup/finalization allowance). No retry or resume.
"""
import argparse
from datetime import datetime, timezone, timedelta
import getpass
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import warnings

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from runtime import bird_readonly_audit as audit
from runtime.cache_action_factorial_batch import run, validate
from runtime.cache_action_factorial_capacity import FactorialCapacity
from runtime.deepseek_official_transport import OfficialTransport, validate_key
from scripts.run_cache_action import verify_archive, FILES as OLD_FILES
from scripts.run_cache_workflow import counter_for, OLD_MANIFEST_SHA
from scripts.fetch_bird_fixed import no_link_ancestors

INPUT_SEAL='90f2cc1199a65f71c08f1caa9374a16ef4c7610f1a08f4754c18970d5debcd8a'
PLAN_SHA='f0a99c527169c4b0fca9bf0a12625d23fe108f047175cf3ac6703ae9ccc9eab8'
DATABASE_SHA='75e94a2c3236ee3bb2c01fb97a1c4b4c1c269bcefd4eab1d04be323d2d0825b1'
PRICE_DATE='2026-10-25'
POLICY='configs/superhero_factorial_readout_v1.json'
LIMITS=dict(max_calls=16,max_tool_attempts=12,max_seconds=600,outer_worker_seconds=630,
            max_output_tokens=512,ceiling_micro_cny=200000,rates_micro_cny_per_token=[2,8],
            conditional_micro_cny=160128,price_checked_date=PRICE_DATE)
FILES=tuple(dict.fromkeys((*OLD_FILES,'runtime/cache_action_factorial_preview.py',
    'runtime/cache_action_factorial_capacity.py','runtime/cache_action_factorial_loop.py',
    'runtime/cache_action_factorial_batch.py','scripts/run_superhero_factorial.py',POLICY)))


def read(path): return json.loads(Path(path).read_text('utf-8'))


def day(): return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def check_imports(root):
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('runtime.','scripts.')) and getattr(module,'__file__',None):
            location=Path(module.__file__).resolve()
            if not location.is_relative_to(root/'source') or location.relative_to(root/'source').as_posix() not in FILES:
                raise ValueError('Unfrozen project import')


def verify(root, sha):
    root=Path(root).resolve(); no_link_ancestors(root)
    if audit.hash_file(root/'manifest.json') != sha: raise ValueError('Manifest changed')
    m=read(root/'manifest.json'); verify_archive(root/'inputs',INPUT_SEAL)
    entries=list((root/'source').rglob('*'))
    for p in entries: no_link_ancestors(p)
    actual={p.relative_to(root/'source').as_posix():audit.hash_file(p) for p in entries if p.is_file()}
    if actual != m['source_files'] or set(actual)!=set(FILES): raise ValueError('Source set or bytes changed')
    if audit.hash_file(root/'source'/POLICY)!=m['readout_policy_sha256']: raise ValueError('Readout policy changed')
    if (any(m.get(k)!=v or type(m.get(k)) is not type(v) for k,v in LIMITS.items())
            or m['plan_sha256']!=PLAN_SHA or m['database_sha256']!=DATABASE_SHA
            or m['python']!=sys.version or m['sqlite_version']!=sqlite3.sqlite_version):
        raise ValueError('Fixed design, limits or Python/SQLite version changed')
    db=Path(m['database']); no_link_ancestors(db)
    if audit.hash_file(db)!=DATABASE_SHA or any(Path(str(db)+s).exists() for s in ('-wal','-shm','-journal')):
        raise ValueError('Database changed or has sidecars')
    plan=read(root/'inputs/PRIVATE_batch_plan.json'); cache=read(root/'inputs/PRIVATE_cache.json')
    validate(plan,cache,PLAN_SHA); counter=counter_for(m)
    certificates=[dict(id=j['id'],**FactorialCapacity(j['envelope'],counter).certificate()) for j in plan['jobs']]
    cost=sum(c['total_input_bound']*2+c['total_output_bound']*8 for c in certificates)
    if certificates!=m['capacities'] or cost!=LIMITS['conditional_micro_cny'] or cost>LIMITS['ceiling_micro_cny']:
        raise ValueError('Capacity or conditional fee changed')
    return m,plan,cache,counter


def prepare(project,output):
    project=Path(project).resolve(); output=Path(output).resolve(); no_link_ancestors(output)
    if output.exists(): raise ValueError('Never overwrite a preparation')
    inputs=project/'runtime/runs/superhero-factorial-binding-20261025-001'
    verify_archive(inputs,INPUT_SEAL)
    prior=project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450/prepared_manifest.json'
    if audit.hash_file(prior)!=OLD_MANIFEST_SHA: raise ValueError('Tokenizer manifest changed')
    old=read(prior); counter=counter_for(old)
    plan=read(inputs/'PRIVATE_batch_plan.json'); cache=read(inputs/'PRIVATE_cache.json')
    validate(plan,cache,PLAN_SHA)
    capacities=[dict(id=j['id'],**FactorialCapacity(j['envelope'],counter).certificate()) for j in plan['jobs']]
    output.mkdir(parents=True,exist_ok=False)
    for p in inputs.rglob('*'):
        if p.is_file():
            target=output/'inputs'/p.relative_to(inputs); target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as f: f.write(p.read_bytes())
    for name in FILES:
        source=project/name; no_link_ancestors(source)
        target=output/'source'/name; target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f: f.write(source.read_bytes())
    db=project/'runtime/runs/datasets/superhero-fixed-20261025-002/databases/superhero/superhero.sqlite'
    manifest=dict(status='PREPARED_NOT_AUTHORIZED',**LIMITS,
        source_files={n:audit.hash_file(output/'source'/n) for n in FILES},
        input_manifest_sha256=INPUT_SEAL,plan_sha256=PLAN_SHA,capacities=capacities,
        readout_policy_sha256=audit.hash_file(output/'source'/POLICY),
        database=str(db),database_sha256=DATABASE_SHA,python=sys.version,sqlite_version=sqlite3.sqlite_version,
        advertised_version='DeepSeek-V4.1-Flash-1017',model='deepseek-flash',thinking='disabled',temperature=0,
        actual_remote_weights_verified=False,automatic_retry=False,independent_task_pairs=1,
        pricing_source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
        settings_source='https://api-docs.deepseek.com/guides/thinking_mode/',
        price_basis='Peak cache-miss rates; no discounts assumed; local token bound conditional',
        created_utc=datetime.now(timezone.utc).isoformat(),
        **{k:old[k] for k in ('tokenizer','tokenizer_dependencies','tokenizer_package_files')})
    audit.save_new(output/'manifest.json',manifest)
    sha=audit.hash_file(output/'manifest.json'); verify(output,sha)
    return dict(status=manifest['status'],prepared=str(output),manifest_sha256=sha,
                conditional_upper_cny=LIMITS['conditional_micro_cny']/1e6,proposed_ceiling_cny=.20,model_calls=0)


def owned_worker(command, packet, cwd, timeout=630):
    """Terminate only this newly created worker tree, never a shared server."""
    env=os.environ.copy()
    for key in ('DEEPSEEK_API_KEY','PYTHONPATH','PYTHONHOME'): env.pop(key,None)
    env['PYTHONDONTWRITEBYTECODE']='1'
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        cwd=cwd,env=env,creationflags=flags,start_new_session=os.name!='nt')
    try:
        stdout,stderr=process.communicate(packet,timeout=timeout)
    except (subprocess.TimeoutExpired,KeyboardInterrupt) as exc:
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,creationflags=flags,timeout=15)
        else: os.killpg(process.pid,signal.SIGKILL)
        process.wait(timeout=15)
        return dict(status='WORKER_INTERRUPTED' if isinstance(exc,KeyboardInterrupt) else 'WORKER_TIMEOUT',
                    automatic_retry=False,billing_unknown=True,server_cancellation_verified=False)
    if process.returncode:
        return dict(status='WORKER_FAILED',returncode=process.returncode,stderr_not_published=True,automatic_retry=False)
    try:
        result=json.loads(stdout)
        if type(result) is not dict or set(result)!= {'status','dispatch_attempts'}:
            raise ValueError('Unexpected worker summary')
        return result
    except (ValueError,UnicodeError):
        return dict(status='INVALID_WORKER_SUMMARY',automatic_retry=False)


def worker(packet):
    if type(packet) is not dict or set(packet)!= {'prepared','sha','key'}: raise ValueError('Invalid worker packet')
    root=Path(packet['prepared']).resolve()
    if Path(__file__).resolve()!=root/'source/scripts/run_superhero_factorial.py': raise ValueError('Unfrozen worker')
    m,plan,cache,counter=verify(root,packet['sha']); check_imports(root)
    claim=read(root/'execution_claim.json')
    if claim.get('manifest_sha256')!=packet['sha'] or claim.get('approved_cny')!=.20 or day()!=PRICE_DATE:
        raise ValueError('No matching current execution claim')
    validate_key(packet['key'])
    result=run(plan,cache,expected_plan_sha=PLAN_SHA,database=m['database'],output=root/'experiment',
        counter=counter,transport_factory=lambda ident,log:OfficialTransport(packet['key'],log),
        deadline=time.monotonic()+600,mode='OFFICIAL_DEVELOPMENT',ceiling_micro_cny=200000,input_rate=2,output_rate=8)
    return dict(status=result['status'],dispatch_attempts=result['dispatch_attempts'])


def execute(root,sha,approved):
    if type(approved) not in (int,float) or approved!=.20: raise ValueError('Fresh fixed-batch 0.20 CNY approval required')
    root=Path(root).resolve()
    if Path(__file__).resolve()!=root/'source/scripts/run_superhero_factorial.py': raise ValueError('Use frozen copied launcher')
    verify(root,sha); check_imports(root)
    if day()!=PRICE_DATE: raise ValueError('Recheck pricing and prepare a new version for another date')
    if (root/'execution_claim.json').exists() or (root/'experiment').exists(): raise ValueError('Already claimed; no resume')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=getpass.getpass('Official DeepSeek API key (hidden, not saved): ')
    validate_key(key)
    audit.save_new(root/'execution_claim.json',dict(manifest_sha256=sha,approved_cny=.20,
        utc=datetime.now(timezone.utc).isoformat(),automatic_retry=False))
    packet=json.dumps(dict(prepared=str(root),sha=sha,key=key)).encode('utf-8')
    try:
        outcome=owned_worker([sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),'--worker'],packet,root/'source')
    except BaseException as exc:
        audit.save_new(root/'launch_failure.json',dict(error_type=type(exc).__name__,automatic_retry=False))
        raise
    audit.save_new(root/'launch_outcome.json',outcome)
    return dict(outcome,output=str(root/'experiment'))


def main():
    if sys.argv[1:]==['--worker']:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536: raise ValueError('Worker packet too large')
        result=worker(json.loads(raw))
    else:
        p=argparse.ArgumentParser(description=__doc__)
        group=p.add_mutually_exclusive_group(); group.add_argument('--execute',action='store_true'); group.add_argument('--verify',action='store_true')
        p.add_argument('--prepared',type=Path); p.add_argument('--manifest-sha256'); p.add_argument('--approve-cny',type=float)
        p.add_argument('--output',type=Path); args=p.parse_args()
        if args.execute or args.verify:
            if not args.prepared or not args.manifest_sha256: p.error('Prepared path and hash required')
            if args.verify:
                verify(args.prepared,args.manifest_sha256)
                if Path(__file__).resolve()==args.prepared.resolve()/'source/scripts/run_superhero_factorial.py':
                    check_imports(args.prepared.resolve())
                result=dict(status='FROZEN_PREFLIGHT_PASSED',model_calls=0)
            else: result=execute(args.prepared,args.manifest_sha256,args.approve_cny)
        else:
            if not args.output: p.error('New output required')
            result=prepare(Path(__file__).resolve().parents[1],args.output)
    print(json.dumps(result,indent=2,ensure_ascii=True))


if __name__=='__main__': main()
