"""Offline prepare by default; frozen execution needs fresh 0.12-CNY approval.

Sixteen synthetic encoding responses; no SQL/tools, repair, retry or resumption.
Hidden key, owned worker stdin, 600s call deadline / 630s worker limit. Cleanup
is additional. Client accounting is not an installed provider spending cap.
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
from runtime import action_encoding_calibration as calibration
from runtime.deepseek_official_transport import OfficialTransport, validate_key
from runtime.deepseek_trajectory_adapter import CostGuard
from scripts.run_superhero_factorial import owned_worker, FILES as BASE_FILES, day
from scripts.run_cache_action import verify_archive
from scripts.run_cache_workflow import counter_for, OLD_MANIFEST_SHA
from scripts.fetch_bird_fixed import no_link_ancestors

SELF = 'scripts/run_action_encoding_calibration.py'
POLICY = 'configs/action_encoding_calibration_policy_v1.json'
PRICE_DATE = '2026-10-25'
FILES = tuple(dict.fromkeys((*BASE_FILES, SELF, POLICY, 'runtime/action_encoding_calibration.py')))
LIMITS = dict(max_calls=16, max_seconds=600, outer_worker_seconds=630,
              max_input=1500, max_output=512, ceiling_micro_cny=120000,
              rates_micro_cny_per_token=[2,8], price_checked_date=PRICE_DATE)


def read(path): return json.loads(Path(path).read_text('utf-8'))


def frozen(root):
    if Path(__file__).resolve() != root/'source'/SELF:
        raise ValueError('Use the frozen copied launcher')
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('runtime.', 'scripts.')) and getattr(module, '__file__', None):
            p = Path(module.__file__).resolve()
            if not p.is_relative_to(root/'source') or p.relative_to(root/'source').as_posix() not in FILES:
                raise ValueError('Unfrozen project import')


def verify(root, sha):
    root = Path(root).resolve(); no_link_ancestors(root)
    if audit.hash_file(root/'manifest.json') != sha: raise ValueError('Manifest changed')
    m = read(root/'manifest.json')
    for p in (root/'source').rglob('*'): no_link_ancestors(p)
    actual = {p.relative_to(root/'source').as_posix(): audit.hash_file(p)
              for p in (root/'source').rglob('*') if p.is_file()}
    if actual != m['source_files'] or set(actual) != set(FILES):
        raise ValueError('Frozen source inventory changed')
    if (any(m.get(k)!=v or type(m.get(k)) is not type(v) for k,v in LIMITS.items())
            or m['python'] != sys.version): raise ValueError('Limits or runtime changed')
    plan = read(root/'plan.json'); calibration.validate(plan)
    if audit.hash_file(root/'plan.json') != m['plan_file_sha256']:
        raise ValueError('Plan file changed')
    if audit.hash_file(root/'source'/POLICY) != m['policy_sha256']:
        raise ValueError('Readout policy changed')
    counter = counter_for(m)
    counts = [counter.chat(j['payload']['messages']) for j in plan['jobs']]
    if (counts != m['input_tokens'] or any(not 0 < n <= 1500 for n in counts)
            or sum(n*2+512*8 for n in counts) != m['conditional_micro_cny']
            or m['conditional_micro_cny'] > 120000):
        raise ValueError('Capacity or fees changed')
    return m,plan,counter


def prepare(project, output):
    project,output = Path(project).resolve(),Path(output).resolve(); no_link_ancestors(output)
    if output.exists(): raise ValueError('Never overwrite a prepared batch')
    prior = project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450/prepared_manifest.json'
    if audit.hash_file(prior) != OLD_MANIFEST_SHA: raise ValueError('Tokenizer provenance changed')
    old = read(prior); counter = counter_for(old)
    plan = calibration.build_plan(); calibration.validate(plan)
    counts = [counter.chat(j['payload']['messages']) for j in plan['jobs']]
    if any(not 0 < n <= 1500 for n in counts): raise ValueError('Capacity failed')
    cost = sum(n*2+512*8 for n in counts)
    if cost > 120000: raise ValueError('Fees exceed proposed ceiling')
    output.mkdir(parents=True, exist_ok=False)
    for name in FILES:
        source = project/name; no_link_ancestors(source)
        target = output/'source'/name; target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as handle: handle.write(source.read_bytes())
    audit.save_new(output/'plan.json',plan)
    m = dict(status='PREPARED_NOT_AUTHORIZED', **LIMITS,
        plan_file_sha256=audit.hash_file(output/'plan.json'), policy_sha256=audit.hash_file(output/'source'/POLICY),
        source_files={n:audit.hash_file(output/'source'/n) for n in FILES},
        python=sys.version, input_tokens=counts, conditional_micro_cny=cost,
        tokenizer_origin_manifest_sha256=OLD_MANIFEST_SHA,
        model='deepseek-flash', advertised_version='DeepSeek-V4.1-Flash-1017',
        actual_remote_weights_verified=False, endpoint='https://api.deepseek.com/v1/chat/completions',
        pricing_source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
        pricing_basis='Conservative peak cache-miss rates; conditional reference-token bound, not invoice',
        created_utc=datetime.now(timezone.utc).isoformat(),
        **{k:old[k] for k in ('tokenizer','tokenizer_dependencies','tokenizer_package_files')})
    audit.save_new(output/'manifest.json',m)
    sha = audit.hash_file(output/'manifest.json'); verify(output,sha)
    return dict(status=m['status'], prepared=str(output), manifest_sha256=sha,
                conditional_cny=cost/1e6, proposed_ceiling_cny=.12, model_calls=0)


def worker(packet):
    if type(packet) is not dict or set(packet) != {'prepared','sha','key'}:
        raise ValueError('Invalid worker packet')
    root = Path(packet['prepared']).resolve(); frozen(root)
    m,plan,counter = verify(root,packet['sha'])
    claim = read(root/'execution_claim.json')
    if (claim.get('manifest_sha256') != packet['sha'] or claim.get('approved_cny') != .12
            or day() != PRICE_DATE): raise ValueError('Missing current batch approval')
    validate_key(packet['key'])
    result = calibration.run(plan, counter=counter,
        transport_factory=lambda ident,log: OfficialTransport(packet['key'],log),
        output=root/'experiment', mode='OFFICIAL_INTERFACE_CALIBRATION')
    return {k:result[k] for k in ('status','dispatch_attempts')}


def execute(root, sha, approved):
    if type(approved) not in (int,float) or approved != .12:
        raise ValueError('Fresh fixed-batch 0.12-CNY approval required')
    root = Path(root).resolve(); frozen(root); verify(root,sha)
    if day() != PRICE_DATE: raise ValueError('Recheck pricing and prepare a new version on another day')
    if (root/'execution_claim.json').exists() or (root/'experiment').exists():
        raise ValueError('Already claimed; do not resume')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key = getpass.getpass('Official DeepSeek API key (hidden, not saved): ')
    validate_key(key)
    audit.save_new(root/'execution_claim.json', dict(manifest_sha256=sha, approved_cny=.12,
        utc=datetime.now(timezone.utc).isoformat(), automatic_retry=False))
    packet = json.dumps(dict(prepared=str(root),sha=sha,key=key)).encode('utf-8')
    try:
        outcome = owned_worker([sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),'--worker'],
                               packet,root/'source')
    except BaseException as exc:
        audit.save_new(root/'launch_failure.json',dict(error_type=type(exc).__name__,automatic_retry=False))
        raise
    audit.save_new(root/'launch_outcome.json',outcome)
    return dict(outcome,output=str(root/'experiment'))


def analyze(root, sha):
    root = Path(root).resolve(); frozen(root)
    m,plan,counter = verify(root,sha)
    claim,outcome = read(root/'execution_claim.json'),read(root/'launch_outcome.json')
    if claim.get('manifest_sha256')!=sha or claim.get('approved_cny')!=.12:
        raise ValueError('Claim mismatch')
    experiment = root/'experiment'
    verify_archive(experiment,audit.hash_file(experiment/'artifact_hashes.json'))
    result = read(experiment/'summary.json')
    if {k:result[k] for k in ('status','dispatch_attempts')} != outcome:
        raise ValueError('Parent/worker closure mismatch')
    if result != read(experiment/'batch/summary.json') or plan != read(experiment/'batch/plan.json'):
        raise ValueError('Batch journal mismatch')
    if result['status'] != 'OFFICIAL_INTERFACE_CALIBRATION_FINISHED':
        # Do not manufacture completed observations or effects after failures.
        return dict(status='STOPPED_INVENTORY_ONLY', model_calls=0, effects_computed=False,
                    inventory_files=len(read(experiment/'artifact_hashes.json')))
    guard = CostGuard(ceiling_micro_cny=120000,max_calls=16,input_rate=2,output_rate=8)
    rows = []
    for job,n in zip(plan['jobs'],m['input_tokens']):
        trace = experiment/job['id']; pending = read(trace/'action.pending.json')
        if pending['payload']!=job['payload'] or pending['input_tokens']!=n:
            raise ValueError('Observed request differs from plan')
        ticket = guard.reserve(n,512)
        if pending['ticket']!=ticket: raise ValueError('Reservation order mismatch')
        transport = read(trace/'transport-001.json')
        if transport['transport_status']!='RESPONSE_RECEIVED': raise ValueError('Incomplete transport')
        raw = json.loads(transport['response_text'])
        if raw != read(trace/'action.provider.json')['raw']: raise ValueError('Transport/provider mismatch')
        usage = raw['usage']; guard.settle(ticket,usage)
        if guard.snapshot()!=read(trace/'action.cost.json'): raise ValueError('Cost mismatch')
        score = calibration.evaluate_provider(raw,n,counter,job['expected'])
        row = dict(id=job['id'],status='OBSERVED',dispatches=1,score=score)
        if read(trace/'outcome.json')!=row: raise ValueError('Per-slot score mismatch')
        rows.append(row)
    if (rows!=result['rows'] or guard.snapshot()!=result['cost_guard']
            or calibration.summarize(rows)!=result['readout'] or result['dispatch_attempts']!=16):
        raise ValueError('Full batch summary mismatch')
    destination = root/'analysis'; destination.mkdir(exist_ok=False)
    report = dict(status='SEALED_ENCODING_CALIBRATION_REPLAY_VERIFIED',model_calls=0,
                  readout=result['readout'],cost_guard=result['cost_guard'],
                  experiment_manifest_sha256=audit.hash_file(experiment/'artifact_hashes.json'))
    audit.save_new(destination/'audit.json',report)
    audit.save_new(destination/'artifact_hashes.json',{'audit.json':audit.hash_file(destination/'audit.json')})
    return report


def main():
    if sys.argv[1:] == ['--worker']:
        raw = sys.stdin.buffer.read(65537)
        if len(raw)>65536: raise ValueError('Worker packet too large')
        result = worker(json.loads(raw))
    else:
        p = argparse.ArgumentParser(description=__doc__); modes=p.add_mutually_exclusive_group()
        for name in ('execute','verify','audit'): modes.add_argument('--'+name,action='store_true')
        p.add_argument('--prepared',type=Path); p.add_argument('--manifest-sha256'); p.add_argument('--approve-cny',type=float)
        p.add_argument('--output',type=Path); args=p.parse_args()
        if args.execute or args.verify or args.audit:
            if not args.prepared or not args.manifest_sha256: p.error('Prepared path and hash required')
            if args.execute: result=execute(args.prepared,args.manifest_sha256,args.approve_cny)
            elif args.audit: result=analyze(args.prepared,args.manifest_sha256)
            else:
                verify(args.prepared,args.manifest_sha256); frozen(args.prepared.resolve())
                result=dict(status='FROZEN_PREFLIGHT_PASSED',model_calls=0)
        else:
            if not args.output: p.error('New output path required')
            result=prepare(Path(__file__).resolve().parents[1],args.output)
    print(json.dumps(result,indent=2,ensure_ascii=True))


if __name__ == '__main__': main()
