"""Freeze a fixed local matrix: development 480 or held-structure confirmation 1920.

Existing pinned Qwen or Mistral assets. No downloads, API, training or model SQL.
Each attempted batch is exclusive: failed starts may not be retried/resumed.
Effects are scored only once every batch is terminal (or explicit abort).
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_local_separation import sha,save,utc
from runtime.reuse_scenarios import build_suite,canonical,digest
from runtime.reuse_protocol import schedule,batches,messages,audit_matrix
from runtime.reuse_separation import parse_response
from runtime.reuse_local_transport import OwnedLocal,verify_assets,validate_usage
from runtime.reuse_evaluation import evaluate
from runtime.reuse_mistral_transport import (adapt_messages,verify_mistral_assets,
    verify_rendered_instructions)

SOURCES=('runtime/reuse_scenarios.py','runtime/reuse_protocol.py',
    'runtime/reuse_statistics.py','runtime/reuse_evaluation.py',
    'runtime/reuse_separation.py','runtime/reuse_local_transport.py',
    'scripts/run_local_separation.py','scripts/run_reuse_development.py','scripts/prepare_reuse_suite.py',
    'tests/test_reuse_scenarios.py','tests/test_reuse_protocol.py',
    'tests/test_reuse_statistics.py','tests/test_reuse_development.py',
    'configs/local_gguf_assets_20261024.json','runtime/reuse_mistral_transport.py',
    'scripts/inspect_reuse_gguf.py','tests/test_reuse_mistral_transport.py',
    'configs/second_model_candidate_20261027.json')


def verify_index(directory,name='sha256.json'):
    index=json.loads((directory/name).read_bytes())
    for relative,expected in index.items():
        path=(directory/relative).resolve()
        if not path.is_relative_to(directory) or sha(path)!=expected:
            raise ValueError('Archive content mismatch: '+relative)


def verify_prepared(prepared,expected):
    if sha(prepared/'manifest.json')!=expected:raise ValueError('Manifest mismatch')
    manifest=json.loads((prepared/'manifest.json').read_bytes())
    for name,value in manifest['files'].items():
        path=(prepared/name).resolve()
        if not path.is_relative_to(prepared) or sha(path)!=value:
            raise ValueError('Frozen input/source changed: '+name)
    return manifest


def validate_config(conf):
    sizes={'DEVELOPMENT_ONLY':[200,200,80],'CONFIRMATION_ONLY':[200]*9+[120],
           'DEVELOPMENT_INTERFACE_ONLY':[40]}
    if (conf['scope'] not in sizes or conf['planned_calls']!=sum(sizes[conf['scope']])
        or conf['batch_sizes']!=sizes[conf['scope']] or conf['server_seconds_limit']!=600
        or conf['max_tokens']!=256 or conf['context_size']!=4096
        or not 0<=conf['max_attempts_new_goal_before']<=10000-conf['planned_calls']
        or not 0<=conf['service_seconds_new_goal_before']<=72000-600*len(conf['batch_sizes'])
        or conf['port']!=18090 or conf['seed']!=20261027):
        raise ValueError('Frozen matrix scope/resource violation')
    if conf.get('model_family','qwen') not in ('qwen','mistral'):
        raise ValueError('Unreviewed model family')
    if conf.get('model_family')=='mistral' and (
        conf['model_file']!='Mistral-7B-Instruct-v0.3-Q5_K_M.gguf'
        or conf['model_alias']!='local-mistral7b-v03-q5' or not conf.get('runtime_assets')):
        raise ValueError('Mistral transport specification mismatch')
    if conf['scope']=='DEVELOPMENT_INTERFACE_ONLY' and conf.get('model_family')!='mistral':
        raise ValueError('This new calibration scope is for the second model only')


def transport_messages(conf,original):
    return adapt_messages(original) if conf.get('model_family')=='mistral' else original


def calibration_cases(cases):
    # Outcome-independent: first two lexicographic DEVELOPMENT base IDs. Never
    # use held-out data or cherry-pick successful previous model examples.
    if cases!=json.loads(canonical(build_suite('development'))):
        raise ValueError('Calibration must use the full unchanged development suite')
    selected=set(sorted({c['base_id'] for c in cases})[:2])
    return [c for c in cases if c['base_id'] in selected]


def prepare(output,candidate,planner,assets,runtime,split='development',resource_record=None,
            model_family='qwen',runtime_assets=None,interface_only=False):
    output,candidate,planner,assets,runtime=[p.resolve() for p in (output,candidate,planner,assets,runtime)]
    if not output.is_relative_to(ROOT/'runtime/runs'):raise ValueError('Use new local run folder')
    verify_index(candidate);verify_index(planner)
    if split not in ('development','confirmation_candidate'):raise ValueError('Unknown split')
    confirmation=split=='confirmation_candidate'
    if interface_only and (confirmation or model_family!='mistral'):
        raise ValueError('Interface calibration is Mistral development-only')
    if model_family=='mistral' and (runtime_assets is None or resource_record is None):
        raise ValueError('Second model requires pinned runtime archives and current resource ledger')
    if confirmation and resource_record is None:raise ValueError('Current resource record required')
    resource=json.loads(resource_record.read_bytes()) if resource_record else None
    cases=json.loads((candidate/(split+'.private.json')).read_bytes())
    if cases!=json.loads(canonical(build_suite(split))):
        raise ValueError('Candidate input differs from current reviewed generator')
    calcite=json.loads((planner/'summary.json').read_bytes())
    expected_status='COMPLETED_CONFIRMATION_PLANNER' if confirmation else 'COMPLETED_DEVELOPMENT_PLANNER'
    if calcite['status']!=expected_status or calcite['completed']!=(192 if confirmation else 48):
        raise ValueError('Complete matching planner record required')
    calcite_cases=json.loads((planner/'private_cases.json').read_bytes())
    if calcite_cases!=cases:raise ValueError('Classical baseline used different cases')
    if interface_only:
        cases=calibration_cases(cases)
        ids={c['case_id'] for c in cases}
        calcite=dict(calcite,rows=[r for r in calcite['rows'] if r['case_id'] in ids],
                     subset_note='Fixed first two lexicographic development base IDs; interface only')
    matrix=audit_matrix(cases);jobs=schedule(cases);chunks=batches(jobs)
    output.mkdir(parents=True,exist_ok=False)
    for name in SOURCES:
        dest=output/'source'/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,dest)
    if confirmation:
        shutil.copyfile(ROOT/'configs/reuse_confirmation_plan_v1.json',output/'confirmation_plan.json')
        shutil.copyfile(ROOT/'reports/reuse_separation_14day/CONFIRMATION_PROTOCOL_V1.txt',output/'protocol.txt')
    if resource_record:
        shutil.copyfile(resource_record,output/'prior_resource_record.json')
    for package in ('runtime','scripts','tests'):
        (output/'source'/package/'__init__.py').touch(exist_ok=False)
    save(output/'cases.private.json',cases)
    save(output/'cases.public.json',[{k:v for k,v in c.items() if k!='audit'} for c in cases])
    save(output/'jobs.json',jobs);save(output/'batches.json',chunks);save(output/'matrix_audit.json',matrix)
    shutil.copyfile(ROOT/'configs/local_gguf_assets_20261024.json',output/'asset_spec.json')
    if model_family=='mistral':
        shutil.copyfile(ROOT/'configs/second_model_candidate_20261027.json',output/'model_spec.json')
        amendment=ROOT/'reports/reuse_separation_14day/MISTRAL_TRANSPORT_AMENDMENT_V1.txt'
        if amendment.exists():shutil.copyfile(amendment,output/'transport_amendment.txt')
    save(output/'classical_baseline.json',calcite)
    conf={'scope':'CONFIRMATION_ONLY' if confirmation else
                'DEVELOPMENT_INTERFACE_ONLY' if interface_only else 'DEVELOPMENT_ONLY',
        'utc':utc(),'assets':str(assets),'runtime':str(runtime),
        'model_file':'qwen2.5-7b-instruct-q5_k_m-00001-of-00002.gguf','model_alias':'local-qwen25-7b-q5',
        'port':18090,'context_size':4096,'max_tokens':256,'seed':20261027,'temperature':0,
        'cache_prompt':False,'parallel':1,'json_grammar_enforced':False,
        'planned_calls':len(jobs),'batch_sizes':[len(c) for c in chunks],'server_seconds_limit':600,
        'max_preflight_seconds':3600,'max_cleanup_seconds':15,'automatic_retry':False,
        'max_attempts_new_goal_before':resource['goal_new_attempts_total'] if resource else 32,
        'service_seconds_new_goal_before':resource['goal_service_seconds_total'] if resource else 17.2614,
        'extra_warmup_generations':0,'first_in_each_batch_includes_warmup':True,
        'candidate_index_sha256':sha(candidate/'sha256.json'),
        'classical_archive_index_sha256':sha(planner/'sha256.json'),
        'actual_API_cny':0,'confirmation_calls_planned':len(jobs) if confirmation else 0,'model_SQL_execution':False}
    conf['model_family']=model_family
    if model_family=='mistral':
        conf.update(model_file='Mistral-7B-Instruct-v0.3-Q5_K_M.gguf',
            model_alias='local-mistral7b-v03-q5',runtime_assets=str(runtime_assets.resolve()),
            role_adaptation='Full system instruction + two newlines + full user content as one user message',
            token_preflight_add_special=True,
            calibration_selection='First two lexicographic development base IDs' if interface_only else None)
    validate_config(conf);save(output/'config.json',conf)
    save(output/'manifest.json',{'files':{str(p.relative_to(output)).replace('\\','/'):sha(p)
         for p in output.rglob('*') if p.is_file()},'scope':conf['scope']+'_FIXED_CALLS'})
    result={'status':'PREPARED_NO_INFERENCE','prepared':str(output),
            'manifest_sha256':sha(output/'manifest.json'),'batches':[len(c) for c in chunks]}
    print(json.dumps(result,ensure_ascii=False,indent=2));return result


def execute(prepared,manifest_sha,batch_number):
    started=time.perf_counter();prepared=prepared.resolve()
    verify_prepared(prepared,manifest_sha)
    if ROOT!=prepared/'source':raise ValueError('Run the frozen source script')
    conf=json.loads((prepared/'config.json').read_bytes());validate_config(conf)
    chunks=json.loads((prepared/'batches.json').read_bytes())
    if batch_number not in range(len(chunks)):raise ValueError('Batch outside frozen schedule')
    for previous in range(batch_number):
        prior=prepared/f'batch-{previous:02d}'/'terminal.json'
        if not prior.exists() or json.loads(prior.read_bytes())['status']!='COMPLETED':
            raise ValueError('Previous batch is not complete; no silent continuation after failure')
    if (prepared/'abort.json').exists():raise ValueError('Study already aborted')
    out=prepared/f'batch-{batch_number:02d}';out.mkdir(exist_ok=False)
    save(out/'started.json',{'utc':utc(),'manifest_sha256':manifest_sha,'batch':batch_number})
    jobs=chunks[batch_number]
    cases={c['case_id']:c for c in json.loads((prepared/'cases.public.json').read_bytes())}
    local=OwnedLocal(conf,out);responses={};attempts=0;status='FAILED'
    try:
        asset_spec=json.loads((prepared/'asset_spec.json').read_bytes())
        if conf.get('model_family')=='mistral':
            verify_mistral_assets(conf,json.loads((prepared/'model_spec.json').read_bytes()),asset_spec,out)
        else:verify_assets(conf,asset_spec,out)
        if time.perf_counter()-started>conf['max_preflight_seconds']:raise TimeoutError('Preflight limit')
        local.start()
        # Size-check all dynamic verifier alternatives BEFORE dispatching a model call.
        # No hidden gold answers or private worlds are used. Tokenization is not generation.
        preflight=[]
        for job in jobs:
            for candidate in ('applicable','inapplicable','unknown','invalid_or_missing') if job['method']=='M4' else (None,):
                original=messages(cases[job['case_id']],job,candidate)
                msgs=transport_messages(conf,original)
                prompt=local.request('/apply-template',{'messages':msgs})['prompt']
                if conf.get('model_family')=='mistral':verify_rendered_instructions(original,prompt)
                elif any(m['content'] not in prompt for m in original):
                    raise ValueError('Rendered template lost or modified task instructions')
                tokens=local.request('/tokenize',{'content':prompt,
                    'add_special':conf.get('model_family')=='mistral','parse_special':True})['tokens']
                if len(tokens)+conf['max_tokens']+64>conf['context_size']:
                    raise ValueError('Planned prompt too long; no truncation or generation')
                preflight.append({'job_id':job['job_id'],'candidate':candidate,'prompt_tokens':len(tokens),
                    'rendered_prompt_sha256':digest(prompt),'full_instruction_rendering_checked':True})
        save(out/'token_preflight.json',preflight)
        sizes={(p['job_id'],p['candidate']):p['prompt_tokens'] for p in preflight}
        for i,job in enumerate(jobs):
            proposal=None
            if 'proposal_id' in job:
                proposal=parse_response(responses[job['proposal_id']],'M1')['admissibility']
                if proposal not in ('applicable','inapplicable','unknown'):proposal='invalid_or_missing'
            msgs=transport_messages(conf,messages(cases[job['case_id']],job,proposal))
            count=sizes[job['job_id'],proposal]
            payload={'model':conf['model_alias'],'messages':msgs,'max_tokens':conf['max_tokens'],
                'temperature':conf['temperature'],'seed':conf['seed'],'stream':False,'cache_prompt':False}
            prefix=f'{i:03d}'
            save(out/(prefix+'.request.json'),payload)
            save(out/(prefix+'.dispatch.json'),{'job_id':job['job_id'],'utc':utc(),
                'input_sha256':digest(payload),'preflight_prompt_tokens':count,
                'includes_first_generation_warmup':i==0})
            attempts+=1;call_started=time.perf_counter()
            raw=local.request('/v1/chat/completions',payload)
            elapsed=time.perf_counter()-call_started
            save(out/(prefix+'.response.json'),raw);responses[job['job_id']]=raw
            usage=validate_usage(raw,count,conf['context_size'],conf['max_tokens'])
            save(out/(prefix+'.measurement.json'),{'job_id':job['job_id'],'usage':usage,
                'seconds':elapsed,'timings':raw.get('timings'),'preflight_prompt_tokens':count})
            print(f'Batch {batch_number+1}/{len(chunks)}: completed {i+1}/{len(jobs)}',flush=True)
        status='COMPLETED'
    except BaseException as exc:
        save(out/'failure.json',{'type':type(exc).__name__,'detail':str(exc),'utc':utc()})
    finally:
        shutdown=local.stop()
        terminal={'status':status,'planned_calls':len(jobs),'dispatch_attempts':attempts,
            'responses_recorded':len(responses),'utc':utc(),**shutdown,
            'seconds_including_preflight':time.perf_counter()-started,
            'effect_scoring_performed':False,'actual_API_cny':0}
        save(out/'terminal.json',terminal)
        save(out/'sha256.json',{p.name:sha(p) for p in out.iterdir() if p.is_file()})
    print(json.dumps(terminal,indent=2));return terminal


def audit(prepared,manifest_sha,output,abort_incomplete=False):
    prepared=prepared.resolve();output=output.resolve()
    verify_prepared(prepared,manifest_sha)
    if ROOT!=prepared/'source':raise ValueError('Audit with frozen code')
    chunks=json.loads((prepared/'batches.json').read_bytes())
    terminal_paths=[prepared/f'batch-{i:02d}'/'terminal.json' for i in range(len(chunks))]
    incomplete=any(not p.exists() for p in terminal_paths)
    if incomplete and not abort_incomplete:raise ValueError('No intermediate effects; all batches must be terminal')
    if incomplete:
        # Abort only when an earlier batch has an actual recorded failure. Never
        # stop a running study to inspect early effects or replace pending cells.
        if not any(p.exists() and json.loads(p.read_bytes())['status']=='FAILED' for p in terminal_paths):
            raise ValueError('An explicit recorded failure is required for abort')
        for i,p in enumerate(terminal_paths):
            if not p.exists() and p.parent.exists():raise ValueError('A batch is still nonterminal')
        save(prepared/'abort.json',{'utc':utc(),'reason':'Recorded batch failure; unstarted cells preserved'})
    responses={};measurements={};terminals=[]
    for i,path in enumerate(terminal_paths):
        if not path.exists():continue
        out=path.parent;verify_index(out);terminal=json.loads(path.read_bytes());terminals.append(terminal)
        for j,job in enumerate(chunks[i]):
            prefix=f'{j:03d}';rp=out/(prefix+'.response.json');dp=out/(prefix+'.dispatch.json')
            if not rp.exists():continue
            if not dp.exists():raise ValueError('Response without dispatch')
            dispatch=json.loads(dp.read_bytes());payload=json.loads((out/(prefix+'.request.json')).read_bytes())
            if dispatch['job_id']!=job['job_id'] or digest(payload)!=dispatch['input_sha256']:
                raise ValueError('Request provenance mismatch')
            responses[job['job_id']]=json.loads(rp.read_bytes())
            mp=out/(prefix+'.measurement.json')
            if mp.exists():measurements[job['job_id']]=json.loads(mp.read_bytes())
    cases=json.loads((prepared/'cases.private.json').read_bytes())
    conf=json.loads((prepared/'config.json').read_bytes())
    # Rebuild each actual prompt, including dynamic M4 proposals, from frozen inputs.
    by_case={c['case_id']:c for c in cases}
    for i,chunk in enumerate(chunks):
        for j,job in enumerate(chunk):
            req=prepared/f'batch-{i:02d}'/f'{j:03d}.request.json'
            if not req.exists():continue
            proposal=None
            if 'proposal_id' in job:
                proposal=parse_response(responses.get(job['proposal_id']),'M1')['admissibility']
            if json.loads(req.read_bytes())['messages']!=transport_messages(conf,messages(by_case[job['case_id']],job,proposal)):
                raise ValueError('Actual prompt differs from frozen method')
    planner=json.loads((prepared/'classical_baseline.json').read_bytes())
    result=evaluate(cases,responses,measurements,planner['rows'],
        scope='confirmation' if conf['scope']=='CONFIRMATION_ONLY' else 'development')
    result.update(manifest_sha256=manifest_sha,terminals=terminals,
        model_family=conf.get('model_family','qwen'),interface_calibration_only=conf['scope']=='DEVELOPMENT_INTERFACE_ONLY',
        dispatch_attempts=sum(t['dispatch_attempts'] for t in terminals),
        server_seconds=sum(t['owned_server_seconds'] for t in terminals),
        all_started_servers_closed=all(t['owned_server_stopped'] for t in terminals),
        all_actual_prompts_match_freeze=True,input_manifest_reverified=True)
    output.mkdir(parents=True,exist_ok=False)
    save(output/'summary.json',result)
    save(output/'input_hashes.json',{'manifest':manifest_sha,'batch_indices':{
        str(i):sha(p.parent/'sha256.json') for i,p in enumerate(terminal_paths) if p.exists()}})
    save(output/'sha256.json',{p.name:sha(p) for p in output.iterdir() if p.is_file()})
    print(json.dumps({k:result[k] for k in ('scope','expected_calls','responses_recorded','missing_calls',
         'methods','dispatch_attempts','server_seconds','all_started_servers_closed')},indent=2));return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare',action='store_true');mode.add_argument('--execute',action='store_true')
    mode.add_argument('--audit',action='store_true')
    for name in ('output','candidate','planner','assets','runtime','prepared','resource-record','runtime-assets'):p.add_argument('--'+name,type=Path)
    p.add_argument('--model-family',choices=['qwen','mistral'],default='qwen')
    p.add_argument('--interface-only',action='store_true')
    p.add_argument('--split',choices=['development','confirmation_candidate'],default='development')
    p.add_argument('--manifest-sha256');p.add_argument('--batch',type=int)
    p.add_argument('--abort-incomplete',action='store_true')
    a=p.parse_args()
    if a.prepare:
        if any(getattr(a,k) is None for k in ('output','candidate','planner','assets','runtime')):p.error('Missing prepare argument')
        prepare(a.output,a.candidate,a.planner,a.assets,a.runtime,a.split,a.resource_record,
                a.model_family,a.runtime_assets,a.interface_only)
    elif a.execute:
        if a.prepared is None or a.manifest_sha256 is None or a.batch is None:p.error('Missing execute argument')
        result=execute(a.prepared,a.manifest_sha256,a.batch)
        if result['status']!='COMPLETED':raise SystemExit(1)
    else:
        if a.prepared is None or a.manifest_sha256 is None or a.output is None:p.error('Missing audit argument')
        audit(a.prepared,a.manifest_sha256,a.output,a.abort_incomplete)


if __name__=='__main__':main()
