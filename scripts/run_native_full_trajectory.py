"""Offline prepare by default; frozen execution requires fresh 0.70 CNY approval.

Two reviewed development pairs, all eight cells, at most 32 model calls and 24
read-only tool attempts. No retry/resume/training. Hidden key, owned worker,
600-second call deadline / 630-second worker limit; cleanup additional.
"""
import argparse
from datetime import datetime, timezone
import getpass
from pathlib import Path
import sqlite3
import sys
import time
import warnings
import json

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from runtime import bird_readonly_audit as audit
from runtime import native_symmetric_batch as batch
from runtime.native_symmetric_batch_audit import inspect
from runtime.native_action_interface import _load
from runtime.native_trajectory_counter import NativeTrajectoryCounter
from runtime.native_action_counter import SOURCE_HASHES
from runtime.deepseek_native_trajectory_transport import NativeTrajectoryTransport, validate_key
from scripts import run_positive_symmetric as positive
from scripts import run_strength_symmetric as strength
from scripts.run_native_context import FILES as NATIVE_FILES
from scripts.run_superhero_factorial import owned_worker, day
from scripts.run_cache_action import verify_archive
from scripts.run_cache_workflow import counter_for
from scripts.fetch_bird_fixed import no_link_ancestors

SELF='scripts/run_native_full_trajectory.py'
POLICY='configs/native_full_trajectory_policy_v1.json'
PRICE_DATE='2026-10-26'
STRENGTH_ADMISSION_SHA='3db336667f8029d3e30c521a97dded94ec9d234864fc13cae1b5b7189e63672d'
EXTRA=('runtime/native_action_history.py','runtime/native_trajectory_counter.py',
    'runtime/native_trajectory_adapter.py','runtime/native_symmetric_loop.py',
    'runtime/deepseek_native_trajectory_transport.py','runtime/native_trajectory_replay.py',
    'runtime/native_accounting_replay.py','runtime/native_symmetric_batch.py',
    'runtime/native_symmetric_batch_audit.py')
FILES=tuple(dict.fromkeys((*NATIVE_FILES,*positive.FILES,*strength.FILES,*EXTRA,SELF,POLICY)))
LIMITS=dict(max_calls=32,max_tool_attempts=24,max_seconds=600,outer_worker_seconds=630,
    max_reference_input=8192,max_output=512,ceiling_micro_cny=700000,
    conditional_micro_cny=655360,rates_micro_cny_per_token=[2,8],price_checked_date=PRICE_DATE)
MODE='OFFICIAL_NATIVE_DEVELOPMENT'


def read(path):return _load(Path(path).read_text('utf-8'))


def inventory(root):
    result={}
    no_link_ancestors(root)
    for p in Path(root).rglob('*'):
        no_link_ancestors(p)
        if p.is_file():result[p.relative_to(root).as_posix()]=audit.hash_file(p)
    return result


def frozen(root):
    if Path(__file__).resolve()!=root/'source'/SELF:raise ValueError('Use frozen copied launcher')
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('runtime.','scripts.')) and getattr(module,'__file__',None):
            location=Path(module.__file__).resolve()
            if not location.is_relative_to(root/'source') or location.relative_to(root/'source').as_posix() not in FILES:
                raise ValueError('Unfrozen project import')


def inputs(root):
    p=root/'inputs/positive';s=root/'inputs/strength';pre=root/'inputs/positive-preflight'
    verify_archive(p,positive.INPUT_SEAL);verify_archive(s,strength.INPUT_SEAL)
    verify_archive(pre,positive.PREFLIGHT_SEAL)
    binding=read(p/'binding.json');checked=read(pre/'binding.json');sp=read(s/'inputs.json')
    if (binding['source_plan_sha256']!=positive.SOURCE_PLAN_SHA
            or binding['admission_sha256']!=positive.ADMISSION_SHA
            or checked['input_seal']!=positive.INPUT_SEAL or sp['plan_sha256']!=strength.SOURCE_PLAN_SHA):
        raise ValueError('Reviewed input/preflight binding differs')
    report=read(pre/'summary.json')
    if report['status']!='LOCAL_POSITIVE_SYMMETRIC_PREFLIGHT_PASSED' or report['model_calls']!=0:
        raise ValueError('Missing positive local admission')
    for name,sha in ((positive.ADMISSION,positive.ADMISSION_SHA),(strength.ADMISSION,STRENGTH_ADMISSION_SHA),
                     (strength.DRAFT,strength.DRAFT_SHA)):
        if audit.hash_file(root/'source'/name)!=sha:raise ValueError('Human task admission changed')
    pa=read(root/'source'/positive.ADMISSION);sa=read(root/'source'/strength.ADMISSION)
    if (pa['status']!='SEMANTIC_GATE_PASSED_FOR_ADAPTED_DEVELOPMENT_PAIR'
            or sa['status']!='ADAPTED_DEVELOPMENT_PAIR_READONLY_ADMISSION_AND_BINDING_COMPLETE'
            or pa['technical_basis']['database_sha256']!=positive.DATABASE_SHA
            or sa['binding']['database_sha256']!=positive.DATABASE_SHA):
        raise ValueError('Invalid task admission')
    groups={}
    for family,directory,module,admission in (
            ('positive',p,positive,positive.ADMISSION_SHA),('strength',s,strength,STRENGTH_ADMISSION_SHA)):
        groups[family]=dict(common=read(directory/'PRIVATE_common.json'),cache=read(directory/'PRIVATE_cache.json'),
            source_plan_sha256=module.SOURCE_PLAN_SHA,input_manifest_sha256=module.INPUT_SEAL,admission_sha256=admission)
        if groups[family]['common']['database_sha256']!=positive.DATABASE_SHA:
            raise ValueError('Task snapshot differs')
    keys=('tokenizer','tokenizer_dependencies','tokenizer_package_files')
    tokens={k:checked['tokenizer_files'][k] for k in keys}
    if tokens!={k:sp['tokenizer_files'][k] for k in keys}:raise ValueError('Tokenizer source bindings differ')
    return groups,tokens


def verify(root,sha):
    root=Path(root).resolve();no_link_ancestors(root)
    if audit.hash_file(root/'manifest.json')!=sha:raise ValueError('Manifest changed')
    m=read(root/'manifest.json')
    actual=inventory(root/'source')
    if actual!=m['source_files'] or set(actual)!=set(FILES):raise ValueError('Frozen source inventory differs')
    for k,v in LIMITS.items():
        if type(m.get(k)) is not type(v) or m[k]!=v:raise ValueError('Limits or rates differ')
    if m['python']!=sys.version or m['sqlite_version']!=sqlite3.sqlite_version:
        raise ValueError('Runtime differs')
    if (inventory(root/'reference')!=SOURCE_HASHES or m['reference_hashes']!=SOURCE_HASHES
            or audit.hash_file(root/'source'/POLICY)!=m['policy_sha256']):raise ValueError('Reference/policy differs')
    groups,tokens=inputs(root)
    if any(m[k]!=v for k,v in tokens.items()):raise ValueError('Tokenizer provenance differs')
    plain=counter_for(m);native=NativeTrajectoryCounter(root/'reference',m['tokenizer'])
    plan=read(root/'PRIVATE_plan.json');batch.validate(plan,groups,m['plan_sha256'])
    if audit.hash_file(root/'PRIVATE_plan.json')!=m['plan_file_sha256']:raise ValueError('Plan file changed')
    db=Path(m['database']);no_link_ancestors(db)
    if (m['database_sha256']!=positive.DATABASE_SHA or not db.is_file()
            or audit.hash_file(db)!=positive.DATABASE_SHA
            or any(Path(str(db)+suffix).exists() for suffix in ('-wal','-shm','-journal'))):
        raise ValueError('Fixed database changed or has sidecars')
    receipts=[native.inspect(batch.initial_payload(j['envelope'])) for j in plan['jobs']]
    if receipts!=m['first_request_receipts'] or any(not 0<r['reference_input_tokens']<=8192 for r in receipts):
        raise ValueError('Initial token admission differs')
    if batch.CONDITIONAL!=LIMITS['conditional_micro_cny'] or batch.CEILING!=LIMITS['ceiling_micro_cny']:
        raise ValueError('Runner fee configuration differs')
    return m,plan,groups,plain,native


def copy_file(source,target):
    no_link_ancestors(source);target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as handle:handle.write(source.read_bytes())


def prepare(project,output):
    project=Path(project).resolve();output=Path(output).resolve();no_link_ancestors(output)
    if output.exists() or not output.is_relative_to(project/'runtime/runs'):
        raise ValueError('New private runtime/runs directory required')
    origins=(('positive','positive-symmetric-inputs-20261025-001',positive.INPUT_SEAL),
             ('strength','strength-symmetric-preflight-20261025-001',strength.INPUT_SEAL),
             ('positive-preflight','positive-symmetric-preflight-20261025-001',positive.PREFLIGHT_SEAL))
    for _,name,seal in origins:verify_archive(project/'runtime/runs'/name,seal)
    output.mkdir(parents=True,exist_ok=False)
    for label,name,_ in origins:
        origin=project/'runtime/runs'/name
        for path in origin.rglob('*'):
            no_link_ancestors(path)
            if path.is_file():copy_file(path,output/'inputs'/label/path.relative_to(origin))
    for name in FILES:copy_file(project/name,output/'source'/name)
    reference=project/'runtime/runs/native-counter-reference-20261025-002'
    for name in SOURCE_HASHES:copy_file(reference/name,output/'reference'/name)
    groups,tokens=inputs(output);counter_for(tokens)
    native=NativeTrajectoryCounter(output/'reference',tokens['tokenizer'])
    plan=batch.build_plan(groups);audit.save_new(output/'PRIVATE_plan.json',plan)
    db=project/'runtime/runs/datasets/superhero-fixed-20261025-002/databases/superhero/superhero.sqlite'
    manifest=dict(status='PREPARED_NOT_AUTHORIZED',**LIMITS,**tokens,
        source_files=inventory(output/'source'),reference_hashes=SOURCE_HASHES,
        policy_sha256=audit.hash_file(output/'source'/POLICY),
        plan_sha256=audit.digest(plan),plan_file_sha256=audit.hash_file(output/'PRIVATE_plan.json'),
        database=str(db),database_sha256=positive.DATABASE_SHA,python=sys.version,sqlite_version=sqlite3.sqlite_version,
        first_request_receipts=[native.inspect(batch.initial_payload(j['envelope'])) for j in plan['jobs']],
        pricing_source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
        model='deepseek-flash',advertised_version='DeepSeek-V4.1-Flash-1017',
        endpoint='https://api.deepseek.com/beta/chat/completions',actual_remote_weights_verified=False,
        provider_multiturn_count_parity_verified=False,provider_spending_cap_installed=False,
        task_pairs=2,shared_database=True,independent_confirmation=False,automatic_retry=False,
        created_utc=datetime.now(timezone.utc).isoformat())
    audit.save_new(output/'manifest.json',manifest)
    sha=audit.hash_file(output/'manifest.json');verify(output,sha)
    return dict(status='PREPARED_NOT_AUTHORIZED',prepared=str(output),manifest_sha256=sha,
        max_model_calls=32,max_tool_attempts=24,conditional_upper_cny=.65536,proposed_ceiling_cny=.70,model_calls=0)


def worker(packet):
    if type(packet) is not dict or set(packet)!={'prepared','sha','key'}:raise ValueError('Worker packet schema')
    root=Path(packet['prepared']).resolve();frozen(root)
    m,plan,groups,plain,native=verify(root,packet['sha'])
    claim=read(root/'execution_claim.json')
    if (claim.get('manifest_sha256')!=packet['sha'] or claim.get('approved_cny')!=.70
            or claim.get('automatic_retry') is not False or day()!=PRICE_DATE):
        raise ValueError('No matching current approval')
    validate_key(packet['key']);frozen(root)
    result=batch.run(plan,groups,expected_plan_sha=m['plan_sha256'],database=m['database'],
        output=root/'experiment',native_counter=native,plain_counter=plain,
        transport_factory=lambda ident,log:NativeTrajectoryTransport(packet['key'],log),
        deadline=time.monotonic()+600,mode=MODE)
    return dict(status=result['status'],dispatch_attempts=result['dispatch_attempts'])


def execute(root,sha,approved):
    if type(approved) not in (int,float) or approved!=.70:raise ValueError('Fresh 0.70 CNY approval required')
    root=Path(root).resolve();frozen(root);verify(root,sha);frozen(root)
    if day()!=PRICE_DATE:raise ValueError('Recheck prices and prepare a new dated version')
    if (root/'execution_claim.json').exists() or (root/'experiment').exists():raise ValueError('No resume or retry')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=getpass.getpass('Official DeepSeek API key (hidden, not saved): ')
    validate_key(key)
    audit.save_new(root/'execution_claim.json',dict(manifest_sha256=sha,approved_cny=.70,
        utc=datetime.now(timezone.utc).isoformat(),automatic_retry=False))
    packet=json.dumps(dict(prepared=str(root),sha=sha,key=key)).encode('utf-8')
    try:
        outcome=owned_worker([sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),'--worker'],
                             packet,root/'source',timeout=630)
    except BaseException as exc:
        audit.save_new(root/'launch_failure.json',dict(error_type=type(exc).__name__,automatic_retry=False));raise
    audit.save_new(root/'launch_outcome.json',outcome)
    if outcome.get('status') in (MODE+'_FINISHED',MODE+'_STOPPED'):
        audit.save_new(root/'archive_receipt.json',dict(manifest_sha256=sha,
            archive_sha256=audit.hash_file(root/'experiment/artifact_hashes.json'),worker_returned=True))
    return dict(outcome,output=str(root/'experiment'))


def analyze(root,sha):
    root=Path(root).resolve();frozen(root);m,plan,groups,plain,native=verify(root,sha);frozen(root)
    claim=read(root/'execution_claim.json');outcome=read(root/'launch_outcome.json')
    if (claim.get('manifest_sha256')!=sha or claim.get('approved_cny')!=.70
            or claim.get('automatic_retry') is not False):raise ValueError('Claim differs')
    if outcome.get('status') not in (MODE+'_FINISHED',MODE+'_STOPPED'):
        raise ValueError('Worker not normally closed; preserve partial archive, do not reconstruct')
    summary=read(root/'experiment/summary.json')
    if outcome!={k:summary[k] for k in ('status','dispatch_attempts')}:raise ValueError('Worker/parent closure differs')
    receipt=read(root/'archive_receipt.json')
    if receipt.get('manifest_sha256')!=sha or receipt.get('worker_returned') is not True:
        raise ValueError('Missing external archive receipt')
    sources={name:audit.hash_file(root/'source'/file) for name,file in (
        ('tool_source.py','runtime/cache_route_symmetric_tools.py'),
        ('base_tool_source.py','runtime/cache_intent_tools.py'),('readonly_source.py','runtime/bird_readonly_audit.py'))}
    result=inspect(root/'experiment',plan,groups,expected_plan_sha=m['plan_sha256'],
        expected_archive_sha=receipt['archive_sha256'],mode=MODE,native_counter=native,
        plain_counter=plain,frozen_tool_sources=sources)
    output=root/'analysis';output.mkdir(exist_ok=False)
    audit.save_new(output/'audit.json',result)
    audit.save_new(output/'artifact_hashes.json',{'audit.json':audit.hash_file(output/'audit.json')})
    return dict(status=result['status'],output=str(output),model_calls=0)


def main():
    if sys.argv[1:]==['--worker']:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise ValueError('Worker packet too large')
        result=worker(_load(raw.decode('utf-8')))
    else:
        p=argparse.ArgumentParser(description=__doc__);modes=p.add_mutually_exclusive_group()
        for name in ('execute','verify','audit'):modes.add_argument('--'+name,action='store_true')
        p.add_argument('--prepared',type=Path);p.add_argument('--manifest-sha256');p.add_argument('--approve-cny',type=float)
        p.add_argument('--output',type=Path);args=p.parse_args()
        if args.execute or args.verify or args.audit:
            if not args.prepared or not args.manifest_sha256:p.error('Prepared path and hash required')
            if args.execute:result=execute(args.prepared,args.manifest_sha256,args.approve_cny)
            elif args.audit:result=analyze(args.prepared,args.manifest_sha256)
            else:
                verify(args.prepared,args.manifest_sha256);frozen(args.prepared.resolve())
                result=dict(status='FROZEN_NATIVE_FULL_PREFLIGHT_PASSED',model_calls=0)
        else:
            if not args.output:p.error('New output directory required')
            result=prepare(Path(__file__).resolve().parents[1],args.output)
    print(json.dumps(result,indent=2,ensure_ascii=True))


if __name__=='__main__':main()
