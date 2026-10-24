"""Offline prepare/verify; execute only after fresh fixed-batch 0.50 CNY approval.

Frozen copied launcher only. Hidden memory-only key input, official endpoint,
no automatic retry/resume. Does not grade final answers or upload raw records.
"""
import argparse
from datetime import datetime, timezone, timedelta
import getpass
import json
from pathlib import Path, PurePosixPath
import re
import sys
import time
import warnings

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from runtime import bird_readonly_audit as audit
from runtime.cache_action_batch import run, validate, CEILING
from runtime.cache_action_capacity import ActionCapacity
from runtime.deepseek_official_transport import OfficialTransport, validate_key
from scripts.run_cache_workflow import counter_for, FILES as BASE_FILES
from scripts.fetch_bird_fixed import no_link_ancestors

INPUT_SHA='5602f3453cfebfb8a09f9ec5e98b722eb7f159eda4c32981cc11d879e513ec85'
PLAN_SHA='0bbaa88e9628488ab2be25ec25de8f54c0b0b7c3a48c4533736dbbf4de14432a'
PRIOR_SHA='fb77253437f46a3b7875298b02bbe1061b50cd5e8605ce582d7cd48a2c339ce4'
DATABASE_SHA='35ef27ae6bdfda530e125ed369666ac0abb8bc8c0bcc0ad09407f547ecb61a93'
POLICY='configs/cache_action_review_policy_v1.json'
FILES=(*BASE_FILES,'runtime/cache_documentation_inputs.py','runtime/cache_intent_observation_bound.py',
       'runtime/cache_action_horizon.py','runtime/cache_action_preview.py','runtime/cache_action_loop.py',
       'runtime/cache_action_capacity.py','runtime/cache_action_adapter.py','runtime/cache_action_batch.py',
       'scripts/run_cache_action.py',POLICY)
PRICE_DATE='2026-10-24'


def read(path): return json.loads(Path(path).read_text('utf-8'))


def verify_archive(root, expected_sha):
    root=Path(root); no_link_ancestors(root)
    if audit.hash_file(root/'artifact_hashes.json') != expected_sha:
        raise ValueError('Input seal changed')
    hashes=read(root/'artifact_hashes.json')
    if type(hashes) is not dict or not hashes: raise ValueError('Empty archive inventory')
    for name, sha in hashes.items():
        if type(name) is not str or type(sha) is not str: raise ValueError('Invalid manifest entry')
        path=PurePosixPath(name)
        if (path.is_absolute() or path.as_posix()!=name or not re.fullmatch(r'[0-9a-f]{64}',sha)
                or any(x in ('.','..') or ':' in x or '\\' in x for x in path.parts)):
            raise ValueError('Unsafe manifest entry')
    entries=list(root.rglob('*'))
    for p in entries: no_link_ancestors(p)
    actual={p.relative_to(root).as_posix():audit.hash_file(p) for p in entries
            if p.is_file() and p != root/'artifact_hashes.json'}
    if hashes != actual: raise ValueError('Input inventory or bytes changed')


def verify(root, sha):
    root=Path(root).resolve(); no_link_ancestors(root)
    if audit.hash_file(root/'manifest.json') != sha: raise ValueError('Manifest changed')
    m=read(root/'manifest.json'); verify_archive(root/'inputs',INPUT_SHA)
    entries=list((root/'source').rglob('*'))
    for p in entries: no_link_ancestors(p)
    actual={p.relative_to(root/'source').as_posix():audit.hash_file(p) for p in entries if p.is_file()}
    if actual != m['source_files'] or set(actual)!=set(FILES): raise ValueError('Source changed')
    if (m['max_calls']!=48 or m['max_seconds']!=600 or m['ceiling_micro_cny']!=CEILING
            or m['rates_micro_cny_per_token']!=[2,8] or m['plan_sha256']!=PLAN_SHA
            or m['database_sha256']!=DATABASE_SHA or m['price_checked_date']!=PRICE_DATE
            or m['max_tool_attempts']!=36 or m['max_output_tokens']!=512):
        raise ValueError('Frozen design or limits changed')
    database=Path(m['database']); no_link_ancestors(database)
    if (audit.hash_file(database)!=DATABASE_SHA
            or any(Path(str(database)+s).exists() for s in ('-wal','-shm','-journal'))):
        raise ValueError('Database fingerprint or sidecar mismatch')
    plan=read(root/'inputs/PRIVATE_plan.json'); caches=read(root/'inputs/PRIVATE_caches.json')
    validate(plan,caches,PLAN_SHA)
    counter=counter_for(m)
    certificates=[dict(id=j['id'],**ActionCapacity(j['envelope'],counter).certificate()) for j in plan['jobs']]
    bound=sum(c['total_input_bound']*2+c['total_output_bound']*8 for c in certificates)
    if certificates!=m['capacities'] or bound!=452992 or bound!=m['conditional_micro_cny'] or bound>CEILING:
        raise ValueError('Conditional capacity or cost mismatch')
    return m,plan,caches,counter


def prepare(project, output):
    project,output=Path(project).resolve(),Path(output).resolve(); no_link_ancestors(output)
    if output.exists(): raise ValueError('Never overwrite a preparation')
    inputs=project/'runtime/runs/cache-action-inputs-20261024-001'
    verify_archive(inputs,INPUT_SHA)
    prior=project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450/prepared_manifest.json'
    if audit.hash_file(prior)!=PRIOR_SHA: raise ValueError('Prior tokenizer/database record changed')
    old=read(prior); counter=counter_for(old)
    plan=read(inputs/'PRIVATE_plan.json'); caches=read(inputs/'PRIVATE_caches.json')
    validate(plan,caches,PLAN_SHA)
    capacities=[dict(id=j['id'],**ActionCapacity(j['envelope'],counter).certificate()) for j in plan['jobs']]
    bound=sum(c['total_input_bound']*2+c['total_output_bound']*8 for c in capacities)
    if bound!=452992 or bound>CEILING: raise ValueError('Fixed cost bound changed')
    output.mkdir(parents=True,exist_ok=False)
    for p in inputs.rglob('*'):
        if p.is_file():
            target=output/'inputs'/p.relative_to(inputs); target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as f: f.write(p.read_bytes())
    for name in FILES:
        source=project/name; no_link_ancestors(source)
        target=output/'source'/name; target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f: f.write(source.read_bytes())
    manifest=dict(status='PREPARED_NOT_AUTHORIZED',source_files={n:audit.hash_file(output/'source'/n) for n in FILES},
        input_manifest_sha256=INPUT_SHA,plan_sha256=PLAN_SHA,max_calls=48,max_seconds=600,
        max_tool_attempts=36,max_output_tokens=512,ceiling_micro_cny=CEILING,
        rates_micro_cny_per_token=[2,8],conditional_micro_cny=bound,capacities=capacities,
        price_checked_date=PRICE_DATE,pricing_source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
        advertised_version='DeepSeek-V4.1-Flash-1017',remote_weights_verified=False,
        automatic_retry=False,review_policy_sha256=audit.hash_file(output/'source'/POLICY),
        python=sys.version,created_utc=datetime.now(timezone.utc).isoformat(),
        **{k:old[k] for k in ('database','database_sha256','tokenizer','tokenizer_dependencies','tokenizer_package_files')})
    audit.save_new(output/'manifest.json',manifest)
    sha=audit.hash_file(output/'manifest.json'); verify(output,sha)
    return dict(status='PREPARED_NOT_AUTHORIZED',prepared=str(output),manifest_sha256=sha,
                proposed_client_ceiling_cny=.50,conditional_upper_cny=bound/1e6,model_calls=0)


def execute(root, sha, approved):
    if type(approved) not in (float,int) or approved!=.50:
        raise ValueError('Fresh fixed-batch 0.50 CNY approval required')
    root=Path(root).resolve()
    if Path(__file__).resolve()!=root/'source/scripts/run_cache_action.py':
        raise ValueError('Use the frozen copied launcher')
    m,plan,caches,counter=verify(root,sha)
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('runtime.','scripts.')) and getattr(module,'__file__',None):
            location=Path(module.__file__).resolve()
            if not location.is_relative_to(root/'source') or location.relative_to(root/'source').as_posix() not in FILES:
                raise ValueError('Unfrozen project import')
    day=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    if day!=m['price_checked_date']: raise ValueError('Recheck pricing and prepare a new batch on another date')
    if (root/'execution_claim.json').exists() or (root/'experiment').exists():
        raise ValueError('Already claimed or partially run; no automatic resume')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=getpass.getpass('Official DeepSeek API key (hidden, not saved): ')
    validate_key(key)
    audit.save_new(root/'execution_claim.json',dict(manifest_sha256=sha,approved_cny=.50,
        utc=datetime.now(timezone.utc).isoformat(),automatic_retry=False))
    result=run(plan,caches,expected_plan_sha=PLAN_SHA,database=m['database'],output=root/'experiment',
        counter=counter,transport_factory=lambda ident,log:OfficialTransport(key,log),
        deadline=time.monotonic()+600,mode='OFFICIAL_DEVELOPMENT')
    return dict(status=result['status'],output=str(root/'experiment'),
                dispatch_attempts=result['dispatch_attempts'],cost_guard=result['cost_guard'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(); group.add_argument('--execute',action='store_true'); group.add_argument('--verify',action='store_true')
    p.add_argument('--prepared',type=Path); p.add_argument('--manifest-sha256'); p.add_argument('--approve-cny',type=float)
    p.add_argument('--output',type=Path); args=p.parse_args()
    if args.execute or args.verify:
        if not args.prepared or not args.manifest_sha256: p.error('Prepared path and hash required')
        if args.verify:
            verify(args.prepared,args.manifest_sha256); result=dict(status='FROZEN_PREFLIGHT_PASSED',model_calls=0)
        else: result=execute(args.prepared,args.manifest_sha256,args.approve_cny)
    else:
        if not args.output: p.error('New output required')
        result=prepare(Path(__file__).resolve().parents[1],args.output)
    print(json.dumps(result,indent=2,ensure_ascii=True))


if __name__=='__main__': main()
