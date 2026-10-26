"""Frozen E3 followup: one SQL generation per selected reuse, bounded execution.

Preparation consumes already sealed E1/E2 decisions. No action reselection,
model repair, adaptive examples or hidden-answer-triggered fallback.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_local_separation import save,sha,utc
from scripts.run_reuse_development import (SOURCES as MATRIX_SOURCES,verify_prepared,
    verify_index,transport_messages)
from runtime.reuse_protocol import schedule
from runtime.reuse_execution_protocol import (select_cases,build_cells,transform_messages,
    execute_cell,score_execution,measure_cache_construction)
from runtime.reuse_local_transport import OwnedLocal,verify_assets,validate_usage
from runtime.reuse_mistral_transport import verify_mistral_assets,verify_rendered_instructions
from runtime.reuse_scenarios import digest

SOURCES=(*MATRIX_SOURCES,'runtime/reuse_execution_protocol.py','runtime/reuse_readonly_execution.py',
         'scripts/run_reuse_execution.py','tests/test_reuse_execution_protocol.py',
         'tests/test_reuse_readonly_execution.py','scripts/summarize_reuse_execution.py')


def prepare(e1,e1_manifest,audit,resource,output):
    e1,audit,resource,output=map(lambda p:p.resolve(),(e1,audit,resource,output))
    if not output.is_relative_to(ROOT/'runtime/runs'):raise ValueError('New local run directory required')
    verify_prepared(e1,e1_manifest);verify_index(audit)
    summary=json.loads((audit/'summary.json').read_bytes())
    if (summary['scope']!='SYNTHETIC_CONFIRMATION_E1_E2_ONLY'
        or summary['expected_calls']!=1920 or len(summary['terminals'])!=10
        or any(t['status']!='COMPLETED' for t in summary['terminals'])
        or summary['manifest_sha256']!=e1_manifest or summary['responses_recorded']!=1920
        or summary['missing_calls']!=0 or not summary['all_actual_prompts_match_freeze']
        or not summary['all_started_servers_closed']):
        raise ValueError('Requires a fully sealed matching main matrix')
    inputs=json.loads((audit/'input_hashes.json').read_bytes())
    if inputs['manifest']!=e1_manifest:raise ValueError('Audit manifest mismatch')
    for i,h in inputs['batch_indices'].items():
        if sha(e1/f'batch-{int(i):02d}'/'sha256.json')!=h:raise ValueError('Changed main batch')
        verify_index(e1/f'batch-{int(i):02d}')
    cases=json.loads((e1/'cases.private.json').read_bytes())
    cells=build_cells(cases,summary['decisions']);subset=select_cases(cases)
    jobs=[c for c in cells if c['requires_generation']]
    batches=[jobs[i:i+200] for i in range(0,len(jobs),200)]
    conf=json.loads((e1/'config.json').read_bytes());ledger=json.loads(resource.read_bytes())
    if (len(jobs)>288 or ledger['goal_new_attempts_total']+len(jobs)>10000
        or ledger['goal_service_seconds_total']+600*len(batches)>72000):
        raise ValueError('E3 resource reservation exceeds authorized bounds')
    output.mkdir(parents=True,exist_ok=False)
    for name in SOURCES:
        dest=output/'source'/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,dest)
    for pkg in ('runtime','scripts','tests'):(output/'source'/pkg/'__init__.py').touch(exist_ok=False)
    for name in ('asset_spec.json','model_spec.json','transport_amendment.txt'):
        if (e1/name).exists():shutil.copyfile(e1/name,output/name)
    shutil.copyfile(ROOT/'reports/reuse_separation_14day/E3_EXECUTION_SPEC_V1.txt',output/'protocol.txt')
    shutil.copyfile(resource,output/'prior_resource_record.json')
    save(output/'cases.private.json',subset)
    save(output/'cases.public.json',[{k:v for k,v in c.items() if k!='audit'} for c in subset])
    save(output/'cells.json',cells);save(output/'jobs.json',jobs);save(output/'batches.json',batches)
    save(output/'classical_baseline.json',json.loads((e1/'classical_baseline.json').read_bytes()))
    # All physically shared decision calls are recorded once. Logical workflow
    # mapping includes the M1 proposal for M4 and charges it to that comparator.
    selected={c['case_id'] for c in subset};decision_measurements={}
    for i,chunk in enumerate(json.loads((e1/'batches.json').read_bytes())):
        for j,job in enumerate(chunk):
            if job['case_id'] in selected:
                decision_measurements[job['job_id']]=json.loads((e1/f'batch-{i:02d}'/f'{j:03d}.measurement.json').read_bytes())
    save(output/'decision_measurements.json',decision_measurements)
    save(output/'decision_jobs.json',[j for j in schedule(cases) if j['case_id'] in selected])
    save(output/'decision_service_accounting.json',{
        'physical_calls_whole_matrix':summary['dispatch_attempts'],
        'whole_matrix_owned_service_seconds':summary['server_seconds'],
        'whole_matrix_cold_start_seconds':sum(t['cold_start_seconds'] for t in summary['terminals']),
        'selected_unique_physical_decision_calls':len(decision_measurements),
        'allocation':'Uniform cold-start share per physical call; not directly measured workflow latency'})
    conf.update(scope='E3_SELECTED_HELD_SUBSET',planned_calls=len(jobs),batch_sizes=[len(x) for x in batches],
        max_attempts_new_goal_before=ledger['goal_new_attempts_total'],
        service_seconds_new_goal_before=ledger['goal_service_seconds_total'],
        model_SQL_execution=True,model_SQL_retries=0,worker_seconds_limit=5,
        source_e1_manifest=e1_manifest,source_e1_summary_sha256=sha(audit/'summary.json'),
        logical_cells=432,cpu_phase_seconds_limit=600,cache_amortizations=[1,10,100])
    save(output/'config.json',conf)
    save(output/'manifest.json',{'scope':'E3_SINGLE_TRANSFORM_ATTEMPT',
        'files':{str(p.relative_to(output)).replace('\\','/'):sha(p) for p in output.rglob('*') if p.is_file()}})
    result={'status':'PREPARED_NO_GENERATION','prepared':str(output),'manifest_sha256':sha(output/'manifest.json'),
            'logical_cells':432,'transform_calls':len(jobs),'batch_sizes':conf['batch_sizes']}
    print(json.dumps(result,ensure_ascii=False,indent=2));return result


def generate(prepared,manifest,batch):
    prepared=prepared.resolve();verify_prepared(prepared,manifest)
    if ROOT!=prepared/'source':raise ValueError('Run frozen source')
    conf=json.loads((prepared/'config.json').read_bytes());chunks=json.loads((prepared/'batches.json').read_bytes())
    if batch not in range(len(chunks)) or len(chunks[batch])>200:raise ValueError('Unplanned batch')
    for i in range(batch):
        prev=prepared/f'batch-{i:02d}'/'terminal.json'
        if not prev.exists() or json.loads(prev.read_bytes())['status']!='COMPLETED':
            raise ValueError('No continuation after incomplete or failed batch')
    out=prepared/f'batch-{batch:02d}';out.mkdir(exist_ok=False)
    save(out/'started.json',{'utc':utc(),'manifest':manifest,'batch':batch})
    cases={c['case_id']:c for c in json.loads((prepared/'cases.public.json').read_bytes())}
    local=OwnedLocal(conf,out);attempts=0;responses=0;status='FAILED';started=time.perf_counter()
    try:
        spec=json.loads((prepared/'asset_spec.json').read_bytes())
        if conf.get('model_family')=='mistral':
            verify_mistral_assets(conf,json.loads((prepared/'model_spec.json').read_bytes()),spec,out)
        else:verify_assets(conf,spec,out)
        if time.perf_counter()-started>3600:raise TimeoutError('Asset preflight limit')
        local.start();counts={};prompts={};checks=[]
        for job in chunks[batch]:
            original=transform_messages(cases[job['case_id']]);msgs=transport_messages(conf,original)
            prompt=local.request('/apply-template',{'messages':msgs})['prompt']
            if conf.get('model_family')=='mistral':verify_rendered_instructions(original,prompt)
            elif any(m['content'] not in prompt for m in original):raise ValueError('Instruction rendering changed')
            tokens=local.request('/tokenize',{'content':prompt,'add_special':conf.get('model_family')=='mistral',
                                            'parse_special':True})['tokens']
            if len(tokens)+256+64>4096:raise ValueError('Prompt exceeds frozen context bound')
            counts[job['cell_id']]=len(tokens);prompts[job['cell_id']]=msgs
            checks.append({'cell_id':job['cell_id'],'tokens':len(tokens),'rendered_prompt_sha256':digest(prompt)})
        save(out/'token_preflight.json',checks)
        for i,job in enumerate(chunks[batch]):
            payload={'model':conf['model_alias'],'messages':prompts[job['cell_id']],
                'max_tokens':256,'temperature':0,'seed':20261027,'stream':False,'cache_prompt':False}
            prefix=f'{i:03d}';save(out/(prefix+'.request.json'),payload)
            save(out/(prefix+'.dispatch.json'),{'cell_id':job['cell_id'],'utc':utc(),'input_sha256':digest(payload),
                'preflight_prompt_tokens':counts[job['cell_id']]})
            attempts+=1;tick=time.perf_counter();raw=local.request('/v1/chat/completions',payload)
            elapsed=time.perf_counter()-tick
            save(out/(prefix+'.response.json'),raw);responses+=1
            usage=validate_usage(raw,counts[job['cell_id']],4096,256)
            save(out/(prefix+'.measurement.json'),{'usage':usage,'seconds':elapsed,'timings':raw.get('timings')})
            print(f'E3 batch{batch+1}/{len(chunks)}: {i+1}/{len(chunks[batch])}',flush=True)
        status='COMPLETED'
    except BaseException as exc:save(out/'failure.json',{'type':type(exc).__name__,'detail':str(exc),'utc':utc()})
    finally:
        result={'status':status,'planned_calls':len(chunks[batch]),'dispatch_attempts':attempts,
            'responses_recorded':responses,'utc':utc(),**local.stop(),
            'seconds_including_preflight':time.perf_counter()-started,'effect_scoring_performed':False}
        save(out/'terminal.json',result);save(out/'sha256.json',{p.name:sha(p) for p in out.iterdir() if p.is_file()})
    print(json.dumps(result,indent=2));return result


def cpu_phase(prepared,manifest):
    prepared=prepared.resolve();verify_prepared(prepared,manifest)
    if ROOT!=prepared/'source':raise ValueError('Run frozen source')
    chunks=json.loads((prepared/'batches.json').read_bytes());raws={};measurements={}
    cases={c['case_id']:c for c in json.loads((prepared/'cases.private.json').read_bytes())}
    for i,chunk in enumerate(chunks):
        out=prepared/f'batch-{i:02d}';verify_index(out)
        terminal=json.loads((out/'terminal.json').read_bytes())
        if terminal['status']!='COMPLETED' or not terminal['owned_server_stopped']:
            raise ValueError('No execution/scoring before complete successful transform matrix')
        for j,job in enumerate(chunk):
            prefix=f'{j:03d}';payload=json.loads((out/(prefix+'.request.json')).read_bytes())
            conf=json.loads((prepared/'config.json').read_bytes())
            if payload['messages']!=transport_messages(conf,transform_messages(cases[job['case_id']])):
                raise ValueError('Transform prompt differs from frozen protocol')
            raws[job['cell_id']]=json.loads((out/(prefix+'.response.json')).read_bytes())
            measurements[job['cell_id']]=json.loads((out/(prefix+'.measurement.json')).read_bytes())
    output=prepared/'execution';output.mkdir(exist_ok=False);start=time.perf_counter();status='FAILED';done=0
    save(output/'started.json',{'utc':utc(),'manifest':manifest})
    cells=json.loads((prepared/'cells.json').read_bytes())
    planner={r['case_id']:r for r in json.loads((prepared/'classical_baseline.json').read_bytes())['rows']}
    try:
        save(output/'cache_costs.json',{key:measure_cache_construction(c) for key,c in cases.items()})
        for i,cell in enumerate(cells):
            if time.perf_counter()-start>600:raise TimeoutError('CPU phase deadline')
            packet={'case':cases[cell['case_id']],'cell':cell,'raw':raws.get(cell['cell_id']),
                    'planner':planner[cell['case_id']]}
            tick=time.perf_counter()
            try:
                proc=subprocess.run([sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),'--worker'],
                    input=json.dumps(packet).encode(),capture_output=True,timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                if proc.returncode:raise RuntimeError('Isolated SQL worker failed: '+proc.stderr.decode(errors='replace')[:1000])
                result=json.loads(proc.stdout)
            except subprocess.TimeoutExpired:
                # Do not retry the potentially hostile SQL. The recorded failure
                # gets the same one public-Q fallback, without consulting gold.
                safe=execute_cell(cases[cell['case_id']],dict(cell,action='recompute'))
                safe.update(first_status='WORKER_TIMEOUT',action=cell['action'],fallback_used=True,
                            fallback_reason='WORKER_TIMEOUT',transform_sql=None)
                result={'execution':safe,'score':score_execution(cases[cell['case_id']],safe)}
            result.update(cell=cell,worker_wall_seconds=time.perf_counter()-tick,
                          transform_measurement=measurements.get(cell['cell_id']))
            save(output/f'{i:03d}.json',result);done+=1
        status='COMPLETED'
    except BaseException as exc:save(output/'failure.json',{'type':type(exc).__name__,'detail':str(exc),'utc':utc()})
    finally:
        terminal={'status':status,'cells_completed':done,'cells_planned':432,
                  'utc':utc(),'seconds':time.perf_counter()-start,'new_model_generations':0}
        save(output/'terminal.json',terminal)
        save(output/'sha256.json',{p.name:sha(p) for p in output.iterdir() if p.is_file()})
    print(json.dumps(terminal,indent=2));return terminal


def main():
    p=argparse.ArgumentParser(description=__doc__);m=p.add_mutually_exclusive_group(required=True)
    for flag in ('prepare','generate','execute-sql','worker'):m.add_argument('--'+flag,action='store_true')
    for name in ('e1','audit','resource','output','prepared'):p.add_argument('--'+name,type=Path)
    p.add_argument('--e1-manifest-sha256');p.add_argument('--manifest-sha256');p.add_argument('--batch',type=int)
    a=p.parse_args()
    if a.worker:
        packet=json.load(sys.stdin)
        record=execute_cell(packet['case'],packet['cell'],packet['raw'],packet['planner'])
        print(json.dumps({'execution':record,'score':score_execution(packet['case'],record)}));return
    if a.prepare:prepare(a.e1,a.e1_manifest_sha256,a.audit,a.resource,a.output)
    elif a.generate:
        if generate(a.prepared,a.manifest_sha256,a.batch)['status']!='COMPLETED':raise SystemExit(1)
    elif cpu_phase(a.prepared,a.manifest_sha256)['status']!='COMPLETED':raise SystemExit(1)

if __name__=='__main__':main()
