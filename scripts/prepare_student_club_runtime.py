"""Offline evidence/source bundle. No execute mode, credentials or HTTP calls.

Static local imports plus explicit subprocess entrypoints are included. The
frozen verifier also rejects loaded project modules outside the copied source.
This is a staging bundle, not a paid authorization or a finished live launcher.
"""
import argparse
import ast
from datetime import datetime,timezone
from pathlib import Path
import sqlite3
import sys

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from runtime import bird_readonly_audit as audit
from runtime.student_club_input_admission import load,PINS
from runtime.student_club_native_batch import build_plan,CEILING,MAX_CALLS,MAX_INPUT,MAX_OUTPUT,INPUT_RATE,OUTPUT_RATE
from runtime.student_club_native_counter import Counter
from runtime.student_club_definition_history import DefinitionHistory
from runtime.native_action_counter import SOURCE_HASHES
from scripts.audit_cache_action import snapshot,no_links
from scripts.check_student_club_native_tokens import ASSET_MANIFEST,ASSET_PIN
from scripts.fetch_bird_fixed import read_json
from scripts.run_cache_workflow import counter_for

SELF='scripts/prepare_student_club_runtime.py'
POLICY='configs/student_club_diagnostic_policy_v1.json'
SEEDS=(SELF,'runtime/student_club_native_batch.py','runtime/student_club_native_transport.py',
       'runtime/student_club_readonly.py')
VERSION='student-club-offline-runtime-bundle-v1'


def closure(project,seeds=SEEDS):
    """Conservative static imports, never executes candidate source files."""
    project=Path(project);pending=list(seeds);seen=set()
    def add_module(module,required=True):
        if not module or module.split('.')[0] not in ('runtime','scripts'):return
        base=project.joinpath(*module.split('.'))
        paths=(base.with_suffix('.py'),base/'__init__.py')
        target=next((p for p in paths if p.is_file()),None)
        if target is None:
            if required:raise ValueError('Missing local module: '+module)
            return
        pending.append(target.relative_to(project).as_posix())
    while pending:
        name=pending.pop()
        if name in seen:continue
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] not in ('runtime','scripts'):
            raise ValueError('Unsafe source name')
        p=project/name;no_links(p)
        if not p.is_file() or p.suffix!='.py' or not p.absolute().is_relative_to(project.absolute()):
            raise ValueError('Source must be a project Python file')
        seen.add(name)
        for parent in p.relative_to(project).parents:
            if str(parent)=='.':continue
            init=project/parent/'__init__.py'
            if not init.is_file():raise ValueError('Explicit project package initializer required')
            pending.append(init.relative_to(project).as_posix())
        for node in ast.walk(ast.parse(p.read_text('utf-8-sig'))):
            if isinstance(node,ast.Import):
                for alias in node.names:add_module(alias.name)
            elif isinstance(node,ast.ImportFrom):
                if node.level:raise ValueError('Relative import requires explicit resolution')
                add_module(node.module)
                for alias in node.names:
                    if node.module:add_module(node.module+'.'+alias.name,False)
    return sorted(seen)


def copy_file(source,target):
    no_links(source);no_links(target);target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as handle:handle.write(Path(source).read_bytes())


def counters(root,m):
    plain=counter_for(m)
    native=Counter(Path(root)/'reference',m['tokenizer'])
    return plain,native


def receipts(inputs,native):
    return [native.inspect(DefinitionHistory(inputs['common'],j,lambda *a:None).begin())
            for j in inputs['design']['jobs']]


def verify(root,expected_sha,*,require_frozen=False):
    root=Path(root).absolute();no_links(root)
    if audit.hash_file(root/'manifest.json')!=expected_sha:raise ValueError('Manifest changed')
    m=read_json(root/'manifest.json')
    if m['version']!=VERSION or m['status']!='OFFLINE_STAGING_NOT_AUTHORIZED':
        raise ValueError('Wrong package type')
    for directory,field in (('source','source_files'),('evidence','evidence_files'),('reference','reference_files')):
        if snapshot(root/directory)!=m[field]:raise ValueError('Bundle inventory changed: '+directory)
    if set(m['source_files'])!=set(closure(root/'source'))|{POLICY}:
        raise ValueError('Source dependency closure differs')
    if m['reference_files']!=SOURCE_HASHES:raise ValueError('Counter recipe differs')
    if m['python']!=sys.version or m['sqlite_version']!=sqlite3.sqlite_version:
        raise ValueError('Interpreter runtime differs')
    inputs=load(root/'evidence')
    if inputs['receipt']!=m['input_receipt']:raise ValueError('Input admission differs')
    plan=build_plan(inputs['common'],inputs['cached_result'])
    if (audit.hash_file(root/'PRIVATE_plan.json')!=m['plan_file_sha256']
            or read_json(root/'PRIVATE_plan.json')!=plan or audit.digest(plan)!=m['plan_sha256']):
        raise ValueError('Plan differs')
    if audit.hash_file(m['tokenizer'])!=m['tokenizer_sha256']:raise ValueError('Tokenizer differs')
    _,native=counters(root,m)
    r=receipts(inputs,native)
    if r!=m['first_request_receipts']:raise ValueError('Reference input counts differ')
    ns=[v['reference_input_tokens'] for v in r]
    cost=(sum(ns)+(MAX_CALLS-len(ns))*MAX_INPUT)*INPUT_RATE+MAX_CALLS*MAX_OUTPUT*OUTPUT_RATE
    if any(type(n) is not int or not 0<n<=MAX_INPUT for n in ns) or cost>CEILING or cost!=m['conditional_micro_cny']:
        raise ValueError('Cost/count admission differs')
    if require_frozen:
        if Path(__file__).resolve()!=root/'source'/SELF:raise ValueError('Run copied verifier')
        for name,module in tuple(sys.modules.items()):
            if name.split('.')[0] in ('runtime','scripts') and getattr(module,'__file__',None):
                p=Path(module.__file__).resolve()
                if not p.is_relative_to(root/'source') or p.relative_to(root/'source').as_posix() not in m['source_files']:
                    raise ValueError('Unfrozen project import')
    return dict(status='OFFLINE_STAGING_VERIFIED',source_files=len(m['source_files']),
        evidence_files=len(m['evidence_files']),conditions=12,api_calls=0,database_queries=0,
        paid_approval=False,live_launcher_present=False)


def prepare(project,root):
    project,root=Path(project).absolute(),Path(root).absolute();no_links(root)
    if root.exists() or not root.is_relative_to(project/'runtime/runs'):
        raise ValueError('New private runtime/runs output required')
    original=load(project)
    asset=project/ASSET_MANIFEST
    if audit.hash_file(asset)!=ASSET_PIN:raise ValueError('Pinned asset binding differs')
    assets=read_json(asset)
    counter_for(assets)
    files=closure(project)
    evidence_dirs=[v[0] for v in PINS.values()]+[
        'datasets/arcwise-plat-fixed-20261012-001','student-club-review-intake-20261026-001']
    root.mkdir(parents=True,exist_ok=False)
    for name in files+[POLICY]:copy_file(project/name,root/'source'/name)
    for rel in evidence_dirs:
        origin=project/'runtime/runs'/rel
        for name in snapshot(origin):copy_file(origin/name,root/'evidence/runtime/runs'/rel/name)
    reference=project/'runtime/runs/native-counter-reference-20261025-002'
    for name in SOURCE_HASHES:copy_file(reference/name,root/'reference'/name)
    inputs=load(root/'evidence')
    if original['receipt']!=inputs['receipt']:raise ValueError('Copied admission differs')
    token_fields={k:assets[k] for k in ('tokenizer','tokenizer_dependencies','tokenizer_package_files')}
    _,native=counters(root,token_fields)
    plan=build_plan(inputs['common'],inputs['cached_result'])
    audit.save_new(root/'PRIVATE_plan.json',plan)
    first=receipts(inputs,native);ns=[r['reference_input_tokens'] for r in first]
    cost=(sum(ns)+(MAX_CALLS-len(ns))*MAX_INPUT)*INPUT_RATE+MAX_CALLS*MAX_OUTPUT*OUTPUT_RATE
    manifest=dict(version=VERSION,status='OFFLINE_STAGING_NOT_AUTHORIZED',**token_fields,
        tokenizer_sha256=audit.hash_file(assets['tokenizer']),asset_binding_sha256=ASSET_PIN,
        source_files=snapshot(root/'source'),evidence_files=snapshot(root/'evidence'),
        reference_files=snapshot(root/'reference'),input_receipt=inputs['receipt'],
        plan_sha256=audit.digest(plan),plan_file_sha256=audit.hash_file(root/'PRIVATE_plan.json'),
        first_request_receipts=first,conditional_micro_cny=cost,
        python=sys.version,sqlite_version=sqlite3.sqlite_version,
        created_utc=datetime.now(timezone.utc).isoformat(),api_calls=0,
        tokenizer_assets_external_hash_pinned=True,live_launcher_present=False,
        dynamic_entrypoints_explicit=list(SEEDS),static_import_closure_not_universal_execution_proof=True)
    audit.save_new(root/'manifest.json',manifest)
    pin=audit.hash_file(root/'manifest.json');result=verify(root,pin)
    return result|dict(output=str(root),manifest_sha256=pin)


if __name__=='__main__':
    import json
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path);p.add_argument('--verify',type=Path);p.add_argument('--manifest-sha256')
    a=p.parse_args()
    if a.verify:
        if not a.manifest_sha256:p.error('Manifest SHA required')
        result=verify(a.verify,a.manifest_sha256,require_frozen=True)
    else:
        if not a.output:p.error('New private output required')
        result=prepare(Path(__file__).resolve().parents[1],a.output)
    print(json.dumps(result,indent=2))
