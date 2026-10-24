"""Prepare or verify offline; frozen execution needs fresh 0.10 CNY approval."""
import argparse
from datetime import datetime, timezone, timedelta
import getpass
import json
from pathlib import Path, PurePosixPath
import sys
import warnings

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from runtime import bird_readonly_audit as audit
from runtime.cache_documentation_runner import run, validate
from runtime.deepseek_official_transport import OfficialTransport, validate_key
from scripts.run_cache_workflow import counter_for, FILES as BASE_FILES
from scripts.fetch_bird_fixed import no_link_ancestors

INPUT_SHA='9b81cd75f0b15d57887075da803ffa13ba3b3fc2bdc9431f63ed4670815b0210'
PRIOR_SHA='fb77253437f46a3b7875298b02bbe1061b50cd5e8605ce582d7cd48a2c339ce4'
FILES=(*BASE_FILES,'runtime/cache_documentation_inputs.py',
       'runtime/cache_documentation_runner.py','scripts/run_cache_documentation.py')
PRICE_DATE='2026-10-24'


def read(path): return json.loads(path.read_text('utf-8'))


def verify_archive(root,sha):
    no_link_ancestors(root)
    if audit.hash_file(root/'artifact_hashes.json')!=sha: raise ValueError('Input seal changed')
    hashes=read(root/'artifact_hashes.json')
    for name in hashes:
        p=PurePosixPath(name)
        if p.is_absolute() or p.as_posix()!=name or any(x in ('.','..') or ':' in x or '\\' in x for x in p.parts):
            raise ValueError('Unsafe manifest entry')
    entries=list(root.rglob('*'))
    for p in entries: no_link_ancestors(p)
    actual={p.relative_to(root).as_posix():audit.hash_file(p) for p in entries
            if p.is_file() and p!=root/'artifact_hashes.json'}
    if hashes!=actual: raise ValueError('Input inventory or bytes changed')


def verify(root,sha):
    root=Path(root).resolve(); no_link_ancestors(root)
    if audit.hash_file(root/'manifest.json')!=sha: raise ValueError('Manifest changed')
    manifest=read(root/'manifest.json')
    verify_archive(root/'inputs',INPUT_SHA)
    entries=list((root/'source').rglob('*'))
    for p in entries: no_link_ancestors(p)
    actual={p.relative_to(root/'source').as_posix():audit.hash_file(p) for p in entries if p.is_file()}
    if actual!=manifest['source_files'] or set(actual)!=set(FILES): raise ValueError('Source changed')
    if (manifest['maximum_calls']!=12 or manifest['maximum_seconds']!=600 or
            manifest['ceiling_micro_cny']!=100000 or manifest['max_input']!=1200 or
            manifest['max_output']!=512 or manifest['rates_micro_cny_per_token']!=[2,8]):
        raise ValueError('Limits changed')
    plan=read(root/'inputs/PRIVATE_payloads.json'); validate(plan)
    counter=counter_for(manifest)
    counts=[counter.chat(j['payload']['messages']) for j in plan['jobs']]
    if counts!=manifest['input_tokens'] or any(not 0<n<=1200 for n in counts): raise ValueError('Count mismatch')
    if sum(n*2+512*8 for n in counts)!=manifest['conditional_cost_micro_cny']:
        raise ValueError('Conditional cost mismatch')
    return manifest,plan,counter


def prepare(project,output):
    project,output=Path(project).resolve(),Path(output).resolve(); no_link_ancestors(output)
    if output.exists(): raise ValueError('Never overwrite')
    source_inputs=project/'runtime/runs/cache-documentation-inputs-20261024-001'
    verify_archive(source_inputs,INPUT_SHA)
    prior=project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450/prepared_manifest.json'
    if audit.hash_file(prior)!=PRIOR_SHA: raise ValueError('Tokenizer manifest changed')
    old=read(prior); counter=counter_for(old)
    plan=read(source_inputs/'PRIVATE_payloads.json'); validate(plan)
    counts=[counter.chat(j['payload']['messages']) for j in plan['jobs']]
    if any(not 0<n<=1200 for n in counts): raise ValueError('Full input cap')
    output.mkdir(parents=True,exist_ok=False)
    for p in source_inputs.rglob('*'):
        if p.is_file():
            target=output/'inputs'/p.relative_to(source_inputs); target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as f: f.write(p.read_bytes())
    for name in FILES:
        target=output/'source'/name; target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f: f.write((project/name).read_bytes())
    manifest=dict(status='PREPARED_NOT_AUTHORIZED',source_files={n:audit.hash_file(output/'source'/n) for n in FILES},
        input_manifest_sha256=INPUT_SHA,maximum_calls=12,maximum_seconds=600,ceiling_micro_cny=100000,
        max_input=1200,max_output=512,input_tokens=counts,conditional_cost_micro_cny=sum(n*2+512*8 for n in counts),
        rates_micro_cny_per_token=[2,8],price_checked_date=PRICE_DATE,
        pricing_source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
        advertised_model_version='DeepSeek-V4.1-Flash-1017',remote_weights_verified=False,
        automatic_retry=False,tools_executed=0,python=sys.version,
        created_utc=datetime.now(timezone.utc).isoformat(),
        **{k:old[k] for k in ('tokenizer','tokenizer_dependencies','tokenizer_package_files')})
    audit.save_new(output/'manifest.json',manifest)
    sha=audit.hash_file(output/'manifest.json'); verify(output,sha)
    return dict(status='PREPARED_NOT_AUTHORIZED',prepared=str(output),manifest_sha256=sha,
                conditional_cny=manifest['conditional_cost_micro_cny']/1e6,proposed_ceiling_cny=.10,model_calls=0)


def execute(root,sha,approved):
    if approved!=.10: raise ValueError('Fresh fixed-batch 0.10 CNY approval required')
    root=Path(root).resolve()
    if Path(__file__).resolve()!=root/'source/scripts/run_cache_documentation.py':
        raise ValueError('Use the frozen launcher')
    manifest,plan,counter=verify(root,sha)
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('runtime.','scripts.')) and getattr(module,'__file__',None):
            if not Path(module.__file__).resolve().is_relative_to(root/'source'):
                raise ValueError('Unfrozen import')
    day=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    if day!=manifest['price_checked_date']: raise ValueError('Recheck pricing and prepare a new batch on another day')
    if (root/'execution_claim.json').exists() or (root/'experiment').exists():
        raise ValueError('Already claimed or partially run; no resume')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=getpass.getpass('Official DeepSeek API key (hidden, not saved): ')
    validate_key(key)
    audit.save_new(root/'execution_claim.json',dict(manifest_sha256=sha,approved_cny=.10,
        utc=datetime.now(timezone.utc).isoformat(),automatic_retry=False))
    result=run(plan,counter=counter,transport_factory=lambda ident,log:OfficialTransport(key,log),
               output=root/'experiment',mode='OFFICIAL_DEVELOPMENT')
    return dict(status=result['status'],output=str(root/'experiment'),
                dispatch_attempts=result['dispatch_attempts'],cost_guard=result['cost_guard'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true'); parser.add_argument('--verify',action='store_true')
    parser.add_argument('--prepared',type=Path); parser.add_argument('--manifest-sha256')
    parser.add_argument('--approve-cny',type=float); parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.execute and args.verify: parser.error('Choose one mode')
    if args.execute or args.verify:
        if not args.prepared or not args.manifest_sha256: parser.error('Prepared path and hash required')
        if args.verify:
            verify(args.prepared,args.manifest_sha256)
            result=dict(status='FROZEN_PREFLIGHT_PASSED',model_calls=0)
        else: result=execute(args.prepared,args.manifest_sha256,args.approve_cny)
    else:
        if not args.output: parser.error('New output path required for preparation')
        result=prepare(Path(__file__).resolve().parents[1],args.output)
    print(json.dumps(result,indent=2,ensure_ascii=True))


if __name__=='__main__': main()
