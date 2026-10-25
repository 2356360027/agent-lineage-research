"""Offline prepare by default. Frozen eight-call batch needs fresh .10 approval.

No SQL/tool execution, repair, retry or resumption. Hidden credential input,
600s call horizon, 630s owned worker limit, cleanup additional. Client accounting
is conditional on reference usage, not an installed provider spending cap.
"""
import argparse
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import sys
import warnings

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime import bird_readonly_audit as audit
from runtime import native_context_runner as batch
from runtime.deepseek_native_transport import NativeTransport, validate_key
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime.native_action_counter import NativeFirstCounter, SOURCE_HASHES
from runtime.native_action_interface import _load
from runtime.native_first_response_adapter import score_native
from scripts.run_research_context_interface import FILES as BASE_FILES, INPUT_SEAL
from scripts.run_superhero_factorial import owned_worker, day
from scripts.run_cache_action import verify_archive
from scripts.run_cache_workflow import counter_for, OLD_MANIFEST_SHA
from scripts.fetch_bird_fixed import no_link_ancestors

SELF = 'scripts/run_native_context.py'
POLICY = 'configs/native_context_policy_v1.json'
PRICE_DATE = '2026-10-25'
FILES = tuple(dict.fromkeys((*BASE_FILES, SELF, POLICY,
    'runtime/native_action_interface.py', 'runtime/native_action_counter.py',
    'runtime/deepseek_native_transport.py', 'runtime/native_first_response_adapter.py',
    'runtime/native_context_runner.py')))
LIMITS = dict(max_calls=8, max_seconds=600, outer_worker_seconds=630,
              max_input=8192, max_output=512, ceiling_micro_cny=100000,
              rates_micro_cny_per_token=[2,8], price_checked_date=PRICE_DATE)


def read(path): return json.loads(Path(path).read_text('utf-8'))


def frozen(root):
    if Path(__file__).resolve()!=root/'source'/SELF:
        raise ValueError('Use frozen copied launcher')
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('runtime.', 'scripts.')) and getattr(module,'__file__',None):
            location=Path(module.__file__).resolve()
            if not location.is_relative_to(root/'source') or location.relative_to(root/'source').as_posix() not in FILES:
                raise ValueError('Unfrozen project import')


def native_counter(root, manifest):
    counter_for(manifest)  # Pins tokenizer package files before importing the backend.
    return NativeFirstCounter(root/'reference', manifest['tokenizer'])


def verify(root, sha):
    root=Path(root).resolve(); no_link_ancestors(root)
    if audit.hash_file(root/'manifest.json')!=sha: raise ValueError('Manifest changed')
    m=read(root/'manifest.json')
    for p in (root/'source').rglob('*'): no_link_ancestors(p)
    actual={p.relative_to(root/'source').as_posix():audit.hash_file(p)
            for p in (root/'source').rglob('*') if p.is_file()}
    if actual!=m['source_files'] or set(actual)!=set(FILES): raise ValueError('Source inventory changed')
    if (any(m.get(k)!=v or type(m.get(k)) is not type(v) for k,v in LIMITS.items())
            or m['python']!=sys.version): raise ValueError('Limits/runtime changed')
    verify_archive(root/'inputs',INPUT_SEAL)
    if m['input_manifest_sha256']!=INPUT_SEAL: raise ValueError('Input binding changed')
    refs={}
    for p in (root/'reference').rglob('*'):
        no_link_ancestors(p)
        if p.is_file(): refs[p.relative_to(root/'reference').as_posix()]=audit.hash_file(p)
    if refs!=SOURCE_HASHES or m['reference_hashes']!=SOURCE_HASHES: raise ValueError('Reference changed')
    plan=read(root/'plan.json'); batch.validate(plan)
    if plan!=batch.build_plan(read(root/'inputs/PRIVATE_requests.json')):
        raise ValueError('Plan does not match sealed input')
    if (audit.hash_file(root/'plan.json')!=m['plan_file_sha256']
            or audit.hash_file(root/'source'/POLICY)!=m['policy_sha256']):
        raise ValueError('Plan/policy changed')
    counter=native_counter(root,m)
    receipts=[counter.inspect(j['payload']) for j in plan['jobs']]
    counts=[r['reference_input_tokens'] for r in receipts]
    conditional=sum(n*2+512*8 for n in counts)
    if (receipts!=m['count_receipts'] or counts!=m['input_tokens']
            or any(not 0<n<=8192 for n in counts)
            or conditional!=m['conditional_micro_cny'] or conditional>100000):
        raise ValueError('Capacity or conditional cost changed')
    return m,plan,counter


def prepare(project, output):
    project,output=Path(project).resolve(),Path(output).resolve(); no_link_ancestors(output)
    if output.exists() or not output.is_relative_to(project/'runtime/runs'):
        raise ValueError('New private runtime/runs directory required')
    inputs=project/'runtime/runs/research-context-interface-draft-20261025-001'
    verify_archive(inputs,INPUT_SEAL)
    prior=project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450/prepared_manifest.json'
    if audit.hash_file(prior)!=OLD_MANIFEST_SHA: raise ValueError('Tokenizer provenance changed')
    old=read(prior); counter_for(old)
    reference=project/'runtime/runs/native-counter-reference-20261025-002'
    counter=NativeFirstCounter(reference,old['tokenizer'])
    plan=batch.build_plan(read(inputs/'PRIVATE_requests.json'))
    receipts=[counter.inspect(j['payload']) for j in plan['jobs']]
    counts=[r['reference_input_tokens'] for r in receipts]
    cost=sum(n*2+512*8 for n in counts)
    if any(not 0<n<=8192 for n in counts) or cost>100000: raise ValueError('Capacity failed')
    output.mkdir(parents=True,exist_ok=False)
    copies=[(p,output/'inputs'/p.name) for p in inputs.iterdir()]
    copies += [(project/name,output/'source'/name) for name in FILES]
    copies += [(reference/name,output/'reference'/name) for name in SOURCE_HASHES]
    for source,target in copies:
        no_link_ancestors(source); target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as handle: handle.write(source.read_bytes())
    audit.save_new(output/'plan.json',plan)
    manifest=dict(status='PREPARED_NOT_AUTHORIZED',**LIMITS,
        source_files={n:audit.hash_file(output/'source'/n) for n in FILES},
        reference_hashes=SOURCE_HASHES, input_manifest_sha256=INPUT_SEAL,
        plan_file_sha256=audit.hash_file(output/'plan.json'),
        policy_sha256=audit.hash_file(output/'source'/POLICY),python=sys.version,
        count_receipts=receipts,input_tokens=counts,conditional_micro_cny=cost,
        model='deepseek-flash',advertised_version='DeepSeek-V4.1-Flash-1017',
        endpoint='https://api.deepseek.com/beta/chat/completions',
        provider_prompt_parity_verified=False,actual_remote_weights_verified=False,
        provider_spending_cap_installed=False,automatic_retry=False,
        tokenizer_origin_manifest_sha256=OLD_MANIFEST_SHA,
        pricing_source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
        pricing_basis='Peak cache-miss rates; conditional reference counts, not invoice',
        created_utc=datetime.now(timezone.utc).isoformat(),
        **{k:old[k] for k in ('tokenizer','tokenizer_dependencies','tokenizer_package_files')})
    audit.save_new(output/'manifest.json',manifest)
    sha=audit.hash_file(output/'manifest.json'); verify(output,sha)
    return dict(status='PREPARED_NOT_AUTHORIZED',prepared=str(output),manifest_sha256=sha,
                conditional_cny=cost/1e6,proposed_ceiling_cny=.10,model_calls=0)


def worker(packet):
    if type(packet) is not dict or set(packet)!={'prepared','sha','key'}:
        raise ValueError('Invalid worker packet')
    root=Path(packet['prepared']).resolve(); frozen(root)
    m,plan,counter=verify(root,packet['sha'])
    claim=read(root/'execution_claim.json')
    if (claim.get('manifest_sha256')!=packet['sha'] or claim.get('approved_cny')!=.10
            or day()!=PRICE_DATE): raise ValueError('Missing current approval')
    validate_key(packet['key'])
    result=batch.run(plan,counter=counter,transport_factory=lambda ident,log:NativeTransport(packet['key'],log),
                     output=root/'experiment',mode='OFFICIAL_NATIVE_CONTEXT')
    return dict(status=result['status'],dispatch_attempts=result['dispatch_reservations'])


def execute(root, sha, approved):
    if type(approved) not in (int,float) or approved!=.10:
        raise ValueError('Fresh fixed-batch .10-CNY approval required')
    root=Path(root).resolve(); frozen(root); verify(root,sha)
    if day()!=PRICE_DATE: raise ValueError('Recheck pricing and prepare new version on another day')
    if (root/'execution_claim.json').exists() or (root/'experiment').exists():
        raise ValueError('Already claimed; no resumption')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=getpass.getpass('Official DeepSeek API key (hidden, not saved): ')
    validate_key(key)
    audit.save_new(root/'execution_claim.json',dict(manifest_sha256=sha,approved_cny=.10,
                   utc=datetime.now(timezone.utc).isoformat(),automatic_retry=False))
    packet=json.dumps(dict(prepared=str(root),sha=sha,key=key)).encode('utf-8')
    try:
        outcome=owned_worker([sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),'--worker'],
                             packet,root/'source',timeout=630)
    except BaseException as exc:
        audit.save_new(root/'launch_failure.json',dict(error_type=type(exc).__name__,automatic_retry=False))
        raise
    audit.save_new(root/'launch_outcome.json',outcome)
    return dict(outcome,output=str(root/'experiment'))


def analyze(root, sha):
    root=Path(root).resolve(); frozen(root)
    m,plan,counter=verify(root,sha)
    claim,outcome=read(root/'execution_claim.json'),read(root/'launch_outcome.json')
    if claim.get('manifest_sha256')!=sha or claim.get('approved_cny')!=.10: raise ValueError('Claim mismatch')
    experiment=root/'experiment'; seal=audit.hash_file(experiment/'artifact_hashes.json')
    verify_archive(experiment,seal)
    result=read(experiment/'summary.json')
    if dict(status=result['status'],dispatch_attempts=result['dispatch_reservations'])!=outcome:
        raise ValueError('Worker/parent closure mismatch')
    if result!=read(experiment/'batch/summary.json') or plan!=read(experiment/'batch/plan.json'):
        raise ValueError('Journal mismatch')
    if result['status']!='OFFICIAL_NATIVE_CONTEXT_FINISHED':
        return dict(status='STOPPED_INVENTORY_ONLY',model_calls=0,effects_computed=False,
                    experiment_manifest_sha256=seal)
    guard=CostGuard(ceiling_micro_cny=100000,max_calls=8,input_rate=2,output_rate=8)
    rows=[]
    for job,n in zip(plan['jobs'],m['input_tokens']):
        trace=experiment/job['id']; pending=read(trace/'pending.json')
        if (pending['payload']!=job['payload'] or pending['automatic_retry'] is not False
                or not 0<pending['timeout_seconds']<=90): raise ValueError('Request mismatch')
        if read(trace/'context.json')!=counter.inspect(job['payload']): raise ValueError('Counter mismatch')
        ticket=guard.reserve(n,512)
        if pending['ticket']!=ticket: raise ValueError('Reservation order mismatch')
        transport=read(trace/'transport-001.json')
        if transport['transport_status']!='RESPONSE_RECEIVED': raise ValueError('Incomplete transport')
        raw=transport['response_text']
        if read(trace/'provider.json')!={'raw_response_text':raw}: raise ValueError('Raw provider mismatch')
        body=_load(raw)
        if type(body) is not dict or 'error' in body or type(body.get('usage')) is not dict:
            raise ValueError('Invalid completed provider response')
        usage=body['usage']; guard.settle(ticket,usage)
        if body.get('model')!='deepseek-flash' or usage['prompt_tokens']!=n or usage['completion_tokens']>512:
            raise ValueError('Model/usage mismatch')
        if guard.snapshot()!=read(trace/'cost.json'): raise ValueError('Cost mismatch')
        score=score_native(raw)
        if read(trace/'score.json')!=score: raise ValueError('Score mismatch')
        row=dict(id=job['id'],status='OBSERVED',score=score)
        if read(trace/'outcome.json')!=row: raise ValueError('Per-slot mismatch')
        rows.append(row)
    if (rows!=result['rows'] or guard.snapshot()!=result['cost_guard']
            or batch.summarize(rows,plan)!=result['readout'] or result['dispatch_reservations']!=8):
        raise ValueError('Summary mismatch')
    destination=root/'analysis'; destination.mkdir(exist_ok=False)
    report=dict(status='SEALED_NATIVE_CONTEXT_REPLAY_VERIFIED',model_calls=0,
                readout=result['readout'],cost_guard=result['cost_guard'],experiment_manifest_sha256=seal)
    audit.save_new(destination/'audit.json',report)
    audit.save_new(destination/'artifact_hashes.json',{'audit.json':audit.hash_file(destination/'audit.json')})
    return report


def main():
    if sys.argv[1:]==['--worker']:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536: raise ValueError('Worker packet too large')
        result=worker(json.loads(raw))
    else:
        p=argparse.ArgumentParser(description=__doc__); modes=p.add_mutually_exclusive_group()
        for name in ('execute','verify','audit'): modes.add_argument('--'+name,action='store_true')
        p.add_argument('--prepared',type=Path); p.add_argument('--manifest-sha256'); p.add_argument('--approve-cny',type=float)
        p.add_argument('--output',type=Path); args=p.parse_args()
        if args.execute or args.verify or args.audit:
            if not args.prepared or not args.manifest_sha256: p.error('Prepared directory and hash required')
            if args.execute: result=execute(args.prepared,args.manifest_sha256,args.approve_cny)
            elif args.audit: result=analyze(args.prepared,args.manifest_sha256)
            else:
                verify(args.prepared,args.manifest_sha256); frozen(args.prepared.resolve())
                result=dict(status='FROZEN_NATIVE_PREFLIGHT_PASSED',model_calls=0)
        else:
            if not args.output: p.error('New output required')
            result=prepare(Path(__file__).resolve().parents[1],args.output)
    print(json.dumps(result,indent=2,ensure_ascii=True))


if __name__=='__main__': main()
