"""Offline preparation by default. Frozen execution requires fresh 0.30 CNY approval.

Execute the copied script inside prepared/source, not mutable project code.
Key input is hidden and memory-only. No SQL or tool execution, retries or resume.
"""
import argparse
from datetime import datetime, timezone, timedelta
import getpass
import json
from pathlib import Path
import sys
import warnings

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from runtime import bird_readonly_audit as audit
from runtime import cache_workflow_diagnostic as workflow
from runtime.deepseek_text_counter import ReferenceCounter
from runtime.deepseek_official_transport import OfficialTransport,validate_key
from scripts.fetch_bird_fixed import no_link_ancestors

FILES=('runtime/__init__.py','scripts/__init__.py','runtime/bird_readonly_audit.py',
       'runtime/cache_intent_prompts.py','runtime/cache_intent_prompts_v2.py',
       'runtime/cache_intent_tools.py','runtime/cache_intent_staged_preview.py',
       'runtime/cache_intent_staged_loop.py','runtime/cache_intent_journal.py',
       'runtime/deepseek_text_counter.py','runtime/deepseek_trajectory_adapter.py',
       'runtime/deepseek_official_transport.py','runtime/cache_workflow_diagnostic.py',
       'scripts/fetch_bird_fixed.py','scripts/run_cache_workflow.py')
OLD_MANIFEST_SHA='fb77253437f46a3b7875298b02bbe1061b50cd5e8605ce582d7cd48a2c339ce4'
OLD_PLAN_SHA='3666fbbcbe8e16c22bc76eca6b827fd2e111905ebd35c21ca41de0999dfccdab'
PRICE_DATE='2026-10-24'


def day():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def counter_for(manifest):
    deps=Path(manifest['tokenizer_dependencies'])
    no_link_ancestors(deps)
    for name,sha in manifest['tokenizer_package_files'].items():
        path=deps/name; no_link_ancestors(path)
        if audit.hash_file(path)!=sha: raise ValueError('Changed tokenizer dependencies')
    sys.path.insert(0,str(deps))
    return ReferenceCounter(manifest['tokenizer'])


def verify(root,sha):
    root=Path(root).resolve(); no_link_ancestors(root)
    if audit.hash_file(root/'manifest.json')!=sha: raise ValueError('Changed manifest')
    m=json.loads((root/'manifest.json').read_text('utf-8'))
    expected=set(FILES)
    if set(m['source_files'])!=expected: raise ValueError('Unexpected source set')
    entries=list((root/'source').rglob('*'))
    for p in entries: no_link_ancestors(p)
    if {p.relative_to(root/'source').as_posix() for p in entries if p.is_file()}!=expected:
        raise ValueError('Unexpected source entry')
    for name,h in m['source_files'].items():
        if audit.hash_file(root/'source'/name)!=h: raise ValueError('Changed source')
    if (audit.hash_file(root/'prior_plan.json')!=OLD_PLAN_SHA or
            audit.hash_file(root/'plan.json')!=m['plan_sha256']):
        raise ValueError('Changed input')
    plan=json.loads((root/'plan.json').read_text('utf-8'))
    old=json.loads((root/'prior_plan.json').read_text('utf-8'))
    workflow.validate_plan(plan,old)
    if (m['maximum_calls']!=12 or m['maximum_seconds']!=600 or m['ceiling_micro_cny']!=300000 or
            m['conditional_upper_micro_cny']!=workflow.MAX_CONDITIONAL_MICRO_CNY):
        raise ValueError('Changed limits')
    counter=counter_for(m)
    for job in plan['jobs']:
        n=counter.chat(workflow.payload(job)['messages'])
        if not 0<n<=workflow.MAX_INPUT: raise ValueError('Initial input cap exceeded')
    return m,plan,counter


def prepare(project,root):
    project,root=Path(project).resolve(),Path(root).resolve()
    no_link_ancestors(root)
    if root.exists(): raise ValueError('Preparation never overwrites')
    original=project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450'
    if audit.hash_file(original/'prepared_manifest.json')!=OLD_MANIFEST_SHA:
        raise ValueError('Old preparation changed')
    if audit.hash_file(original/'inputs/PRIVATE_plan.json')!=OLD_PLAN_SHA:
        raise ValueError('Old plan changed')
    oldmanifest=json.loads((original/'prepared_manifest.json').read_text('utf-8'))
    oldbytes=(original/'inputs/PRIVATE_plan.json').read_bytes()
    plan=workflow.build_plan(json.loads(oldbytes))
    counter=counter_for(oldmanifest)
    lengths=[dict(id=j['id'],first_input_tokens=counter.chat(workflow.payload(j)['messages'])) for j in plan['jobs']]
    if any(r['first_input_tokens']>8192 for r in lengths): raise ValueError('Preparation cap')
    root.mkdir(parents=True,exist_ok=False)
    for name in FILES:
        source=project/name; no_link_ancestors(source)
        target=root/'source'/name; target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream: stream.write(source.read_bytes())
    with (root/'prior_plan.json').open('xb') as stream: stream.write(oldbytes)
    audit.save_new(root/'plan.json',plan)
    manifest=dict(status='PREPARED_NOT_AUTHORIZED',price_checked_date=PRICE_DATE,
        source_files={name:audit.hash_file(root/'source'/name) for name in FILES},
        plan_sha256=audit.hash_file(root/'plan.json'),old_prepared_manifest_sha256=OLD_MANIFEST_SHA,
        maximum_calls=12,maximum_seconds=600,ceiling_micro_cny=300000,
        conditional_upper_micro_cny=workflow.MAX_CONDITIONAL_MICRO_CNY,
        per_call_input_cap=8192,per_call_output_cap=512,initial_input_counts=lengths,
        rates_micro_cny_per_token=[2,8],official_advertised_version='DeepSeek-V4.1-Flash-1017',
        pricing_source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
        rate_basis='Peak cache-miss rates; reference-token cap conditional, not provider invoice/cap',
        python=sys.version,endpoint='https://api.deepseek.com/v1/chat/completions',
        model='deepseek-flash',thinking='disabled',temperature=0,
        actual_remote_weights_verified=False,tool_executions=0,new_confirmatory_tasks=0,
        prior_source_hash=OLD_PLAN_SHA,created_utc=datetime.now(timezone.utc).isoformat(),
        **{k:oldmanifest[k] for k in ('tokenizer','tokenizer_dependencies','tokenizer_package_files')})
    audit.save_new(root/'manifest.json',manifest)
    sha=audit.hash_file(root/'manifest.json')
    verify(root,sha)
    return dict(status='PREPARED_NOT_AUTHORIZED',prepared=str(root),manifest_sha256=sha,
                maximum_calls=12,conditional_peak_cost_cny=workflow.MAX_CONDITIONAL_MICRO_CNY/1e6,
                proposed_ceiling_cny=0.30,model_calls=0)


def execute(root,sha,approved):
    if approved!=0.30: raise ValueError('Fresh fixed-batch 0.30 CNY approval required')
    root=Path(root).resolve()
    # Execute only inside the frozen snapshot, including every loaded project module.
    if Path(__file__).resolve()!=root/'source/scripts/run_cache_workflow.py':
        raise ValueError('Run the copied launcher from the prepared source snapshot')
    m,plan,counter=verify(root,sha)
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('runtime.','scripts.')) and getattr(module,'__file__',None):
            if not Path(module.__file__).resolve().is_relative_to(root/'source'):
                raise ValueError('Imported unfrozen project module')
    if day()!=m['price_checked_date']: raise ValueError('Recheck official pricing before another day')
    if (root/'execution_claim.json').exists() or (root/'experiment').exists():
        raise ValueError('Already claimed or partial run; never resume or silently retry')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=getpass.getpass('Official DeepSeek API key (hidden, not saved): ')
    validate_key(key)
    audit.save_new(root/'execution_claim.json',dict(manifest_sha256=sha,approved_cny=0.30,
        utc=datetime.now(timezone.utc).isoformat(),automatic_retry=False))
    result=workflow.run(plan,counter=counter,transport_factory=lambda ident,log:OfficialTransport(key,log),
                        output=root/'experiment',mode='OFFICIAL_DEVELOPMENT')
    return dict(status=result['status'],output=str(root/'experiment'),
                dispatch_attempts=result['dispatch_attempts'],cost_guard=result['cost_guard'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true'); p.add_argument('--prepared',type=Path)
    p.add_argument('--manifest-sha256'); p.add_argument('--approve-cny',type=float)
    p.add_argument('--verify',action='store_true')
    args=p.parse_args()
    if args.execute and args.verify: p.error('Choose execution or verification')
    if args.execute or args.verify:
        if not args.prepared or not args.manifest_sha256: p.error('Prepared path and hash required')
        if args.verify:
            verify(args.prepared,args.manifest_sha256)
            result=dict(status='FROZEN_PREFLIGHT_PASSED',model_calls=0)
        else: result=execute(args.prepared,args.manifest_sha256,args.approve_cny)
    else:
        project=Path(__file__).resolve().parents[1]
        output=project/'runtime/runs'/('workflow-diagnostic-prepared-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        result=prepare(project,output)
    print(json.dumps(result,indent=2,ensure_ascii=True))


if __name__=='__main__': main()
