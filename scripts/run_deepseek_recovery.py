"""Prepare offline by default. Execute ONE frozen paid batch only after approval.

Usage: python -m scripts.run_deepseek_recovery --prepare
Then, only after scope/budget approval, use --execute --prepared PATH
--manifest-sha256 HASH --approve-cny 8. The key is entered hidden, never saved.
No retries, alternative endpoints, third-party API, server rental or uploads.
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time
import warnings

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_entry as entry
from runtime.deepseek_recovery_capacity import RecoveryCapacity
from runtime.deepseek_text_counter import ReferenceCounter, TOKENIZER_SHA
from runtime.deepseek_official_transport import validate_key, OfficialTransport
from runtime.deepseek_recovery_batch import run_batch
from scripts import prepare_staged_source_bundle as old_bundle
from scripts.fetch_bird_fixed import no_link_ancestors

SOURCE_FILES = old_bundle.FILES + (
    'runtime/deepseek_text_counter.py','runtime/deepseek_trajectory_adapter.py',
    'runtime/deepseek_recovery_capacity.py','runtime/deepseek_official_transport.py',
    'runtime/deepseek_recovery_batch.py','scripts/run_deepseek_recovery.py')
DATABASE_SHA = '35ef27ae6bdfda530e125ed369666ac0abb8bc8c0bcc0ad09407f547ecb61a93'


def snapshot(project, destination):
    destination = Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    for name in SOURCE_FILES:
        source = Path(project)/name
        no_link_ancestors(source)
        if not source.is_file() or source.stat().st_size > 4*1024*1024:
            raise ValueError('Missing or oversized source')
        target = destination/name
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream: stream.write(source.read_bytes())
    return {name:audit.hash_file(destination/name) for name in SOURCE_FILES}


def verify_prepared(root, manifest_sha):
    root = Path(root).resolve()
    no_link_ancestors(root)
    if audit.hash_file(root/'prepared_manifest.json') != manifest_sha:
        raise ValueError('Prepared manifest changed')
    manifest = json.loads((root/'prepared_manifest.json').read_text('utf-8'))
    if set(manifest['source_files']) != set(SOURCE_FILES):
        raise ValueError('Unexpected source set')
    if {p.relative_to(root/'source').as_posix() for p in (root/'source').rglob('*') if p.is_file()} != set(SOURCE_FILES):
        raise ValueError('Unmanifested source file')
    for name,h in manifest['source_files'].items():
        no_link_ancestors(root/'source'/name)
        if audit.hash_file(root/'source'/name) != h: raise ValueError('Frozen source changed')
    plan,caches,_ = entry.load_private_inputs(root/'inputs',entry.FIXED_INPUT_SHA,entry.FIXED_PLAN_SHA)
    database = Path(manifest['database'])
    if audit.hash_file(database) != DATABASE_SHA or any(Path(str(database)+s).exists() for s in ('-wal','-shm','-journal')):
        raise ValueError('Database identity/sidecar mismatch')
    for name,h in manifest['tokenizer_package_files'].items():
        if audit.hash_file(Path(manifest['tokenizer_dependencies'])/name) != h:
            raise ValueError('Tokenizer package changed')
    sys.path.insert(0,manifest['tokenizer_dependencies'])
    counter = ReferenceCounter(manifest['tokenizer'])
    capacities = [RecoveryCapacity(j['envelope'],counter).certificate() for j in plan['jobs']]
    bound = sum(c['total_input_bound']*2+c['total_output_bound']*8 for c in capacities)
    if bound != 7_798_952 or bound > 8_000_000:
        raise ValueError('Frozen cost calculation changed')
    return manifest,plan,caches,counter


def communicate_owned(command, packet, *, cwd, timeout):
    env = os.environ.copy()
    for key in ('DEEPSEEK_API_KEY','PYTHONPATH','PYTHONHOME'): env.pop(key,None)
    env['PYTHONDONTWRITEBYTECODE']='1'
    flags = subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    process = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        cwd=cwd,env=env,creationflags=flags,start_new_session=os.name!='nt')
    try:
        stdout,stderr = process.communicate(packet,timeout=timeout)
    except subprocess.TimeoutExpired:
        # Target only the specific worker tree created immediately above.
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags,timeout=15)
        else:
            os.killpg(process.pid,signal.SIGKILL)
        process.wait(timeout=15)
        return {'status':'WORKER_TREE_TIMEOUT','worker_returncode':process.returncode,
                'server_cancellation_verified':False,'billing_unknown':True}
    if process.returncode:
        return {'status':'WORKER_FAILED','worker_returncode':process.returncode,
                'stderr_not_published':True,'automatic_retry':False}
    try:
        result = json.loads(stdout)
        if type(result) is not dict or set(result)-{'status','dispatch_attempts','model_calls'}:
            raise ValueError('Unexpected worker summary')
        return result
    except (ValueError,UnicodeError):
        return {'status':'INVALID_WORKER_SUMMARY','automatic_retry':False}


def worker(packet):
    root = Path(packet['prepared']).resolve()
    manifest,plan,caches,counter = verify_prepared(root,packet['manifest_sha256'])
    source = root/'source'
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('runtime.','scripts.')) and getattr(module,'__file__',None):
            if not Path(module.__file__).resolve().is_relative_to(source):
                raise ValueError('Imported project code is not frozen')
    if packet['mode']=='preflight': return {'status':'FROZEN_PREFLIGHT_PASSED','model_calls':0}
    if packet['mode']!='execute': raise ValueError('Unsupported worker mode')
    claim = json.loads((root/'execution_claim.json').read_text('utf-8'))
    if claim.get('approved_cny') != 8 or claim.get('manifest_sha256') != packet['manifest_sha256']:
        raise ValueError('Missing matching execution claim')
    key = packet['key']; validate_key(key)
    deadline = packet['deadline_monotonic']
    if not 0 < deadline-time.monotonic() <= 1800: raise ValueError('Invalid batch deadline')
    result = run_batch(plan,caches,expected_plan_sha=entry.FIXED_PLAN_SHA,
        database=manifest['database'],output=root/'experiment',counter=counter,
        transport_factory=lambda job,save:OfficialTransport(key,save),
        deadline=deadline,mode='OFFICIAL_UNREVIEWED')
    return {'status':result['status'],'dispatch_attempts':result['dispatch_attempts']}


def prepare(project, output):
    project,root = Path(project).resolve(),Path(output).resolve()
    inputs = project/'runtime/runs/recovery-batch-plan-20261024-002'
    entry.load_private_inputs(inputs,entry.FIXED_INPUT_SHA,entry.FIXED_PLAN_SHA)
    root.mkdir(parents=True,exist_ok=False)
    sources = snapshot(project,root/'source')
    (root/'inputs').mkdir()
    for p in inputs.iterdir():
        with (root/'inputs'/p.name).open('xb') as stream: stream.write(p.read_bytes())
    deps = project/'runtime/runs/tokenizer-deps-0.22.1'
    package = {p.relative_to(deps).as_posix():audit.hash_file(p)
        for p in (deps/'tokenizers').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if not package: raise ValueError('Missing installed tokenizer package')
    manifest = {'purpose':'DEVELOPMENT_BATCH_PREPARED_NOT_AUTHORIZED','source_files':sources,
        'database':str(project/'runtime/runs/datasets/bird-mini-fixed-20261005-002/databases/toxicology/toxicology.sqlite'),
        'database_sha256':DATABASE_SHA,
        'tokenizer':str(project/'runtime/runs/deepseek-tokenizer-reference-20261024-002/tokenizer.json'),
        'tokenizer_sha256':TOKENIZER_SHA,'tokenizer_dependencies':str(deps),
        'tokenizer_package_files':package,'maximum_calls':276,'maximum_seconds':1800,
        'proposed_budget_cny':8,'conditional_peak_cost_cny':7.798952,
        'raw_logs_public':False,'new_confirmatory_tasks':0,'independent_development_targets':2,
        'price_checked_date':'2026-10-24','created_utc':datetime.now(timezone.utc).isoformat()}
    audit.save_new(root/'prepared_manifest.json',manifest)
    sha = audit.hash_file(root/'prepared_manifest.json')
    packet = {'mode':'preflight','prepared':str(root),'manifest_sha256':sha}
    outcome = communicate_owned([sys.executable,'-B','-m','scripts.run_deepseek_recovery','--worker'],
        json.dumps(packet).encode(),cwd=root/'source',timeout=120)
    audit.save_new(root/'preflight_result.json',outcome)
    return {'status':outcome['status'],'prepared':str(root),'manifest_sha256':sha,'model_calls':0}


def execute(root, sha, approved_cny):
    if approved_cny != 8: raise ValueError('Explicit approval for this fixed 8 CNY batch required')
    root = Path(root).resolve()
    manifest,_,_,_ = verify_prepared(root,sha)
    if json.loads((root/'preflight_result.json').read_text('utf-8')).get('status')!='FROZEN_PREFLIGHT_PASSED':
        raise ValueError('Preflight not passed')
    # Do not silently use a previously checked price on a later date.
    if datetime.now().date().isoformat() != manifest['price_checked_date']:
        raise ValueError('Price check is stale; recheck official rates before a new preparation')
    if (root/'execution_claim.json').exists():
        raise ValueError('This prepared batch was already claimed; no automatic retry')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key = getpass.getpass('Official DeepSeek API key (hidden, not saved): ')
    validate_key(key)
    audit.save_new(root/'execution_claim.json',{'manifest_sha256':sha,'approved_cny':8,
        'max_seconds':1800,'automatic_retry':False,'utc':datetime.now(timezone.utc).isoformat()})
    packet = {'mode':'execute','prepared':str(root),'manifest_sha256':sha,'key':key,
              'deadline_monotonic':time.monotonic()+1800}
    outcome = communicate_owned([sys.executable,'-B','-m','scripts.run_deepseek_recovery','--worker'],
        json.dumps(packet).encode(),cwd=root/'source',timeout=1800)
    audit.save_new(root/'execution_outcome.json',outcome)
    audit.save_new(root/'completed_file_hashes.json',{p.relative_to(root).as_posix():audit.hash_file(p)
        for p in sorted(root.rglob('*')) if p.is_file()})
    archive = root.with_name(root.name+'.tar.gz')
    with tarfile.open(archive,'x:gz') as tar: tar.add(root,arcname=root.name)
    verified_files = verify_archive(root, archive)
    audit.save_new(root.with_name(root.name+'.archive-receipt.json'),
        {'archive_sha256':audit.hash_file(archive),'archive_bytes':archive.stat().st_size,
         'raw_private_archive':True,'status':outcome['status'],
         'archive_member_hashes_verified':True,'verified_files':verified_files})
    return {'status':outcome['status'],'output':str(root),'archive':str(archive)}


def verify_archive(root, archive):
    """Compare archive bytes with the sealed snapshot without extracting files."""
    root = Path(root)
    expected = json.loads((root/'completed_file_hashes.json').read_text('utf-8'))
    expected['completed_file_hashes.json'] = audit.hash_file(root/'completed_file_hashes.json')
    expected = {root.name+'/'+name:value for name,value in expected.items()}
    seen = set()
    with tarfile.open(archive,'r:gz') as tar:
        for member in tar:
            if member.isdir(): continue
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise ValueError('Unexpected archive member')
            digest = hashlib.sha256()
            with tar.extractfile(member) as stream:
                for chunk in iter(lambda:stream.read(65536), b''): digest.update(chunk)
            if digest.hexdigest() != expected[member.name]:
                raise ValueError('Archive content hash mismatch')
            seen.add(member.name)
    if seen != set(expected): raise ValueError('Incomplete archive')
    return len(seen)


def main():
    if sys.argv[1:]==['--worker']:
        try: result = worker(json.loads(sys.stdin.buffer.read(16384)))
        except Exception as exc:
            # No error repr/tracebacks which could expose credentials or task data.
            print(json.dumps({'status':'WORKER_'+type(exc).__name__})); raise SystemExit(1)
        print(json.dumps(result)); return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--prepared',type=Path)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--approve-cny',type=int)
    args = parser.parse_args()
    if args.prepare and args.execute: parser.error('Choose preparation or execution')
    project = Path(__file__).resolve().parents[1]
    if args.execute:
        if args.prepared is None or not args.manifest_sha256: parser.error('Frozen preparation required')
        result = execute(args.prepared,args.manifest_sha256,args.approve_cny)
    else:
        output = project/'runtime/runs'/('deepseek-recovery-prepared-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        result = prepare(project,output)
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
