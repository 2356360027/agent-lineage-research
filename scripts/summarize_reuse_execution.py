"""Hash-verified E3 scoring and cost accounting; no model calls or repair.

Re-executes only the scorer's frozen same-SQL counterworld checks. Recomputed
timing is excluded from comparison; actual execution cost stays as recorded.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_local_separation import save,sha
from scripts.run_reuse_development import verify_prepared,verify_index
from runtime.reuse_execution_protocol import score_execution,build_cells


def score_signature(score):
    return {k:v for k,v in score.items() if k!='finite_worlds'}|{
        'finite_worlds':[{'name':w['name'],'answer_matches':w['answer_matches'],
            'execution':{k:w['execution'].get(k) for k in ('status','columns','rows')}}
            for w in score['finite_worlds']]}


def totals(measurements):
    out={'calls':len(measurements),'input_tokens':0,'output_tokens':0,'http_seconds':0.0}
    for m in measurements:
        u=m['usage']
        if u['total_tokens']!=u['prompt_tokens']+u['completion_tokens']:
            raise ValueError('Token accounting mismatch')
        out['input_tokens']+=u['prompt_tokens'];out['output_tokens']+=u['completion_tokens']
        out['http_seconds']+=m['seconds']
    return out


def summarize(prepared,manifest,output):
    prepared=prepared.resolve();verify_prepared(prepared,manifest)
    if ROOT!=prepared/'source':raise ValueError('Use the frozen source for E3 rescore')
    execution=prepared/'execution';verify_index(execution)
    terminal=json.loads((execution/'terminal.json').read_bytes())
    if terminal['status']!='COMPLETED' or terminal['cells_completed']!=432:
        raise ValueError('No final scoring before all432 cells are terminal and complete')
    conf=json.loads((prepared/'config.json').read_bytes())
    cases={c['case_id']:c for c in json.loads((prepared/'cases.private.json').read_bytes())}
    cells=json.loads((prepared/'cells.json').read_bytes())
    prior=json.loads((prepared/'prior_resource_record.json').read_bytes())
    dm=json.loads((prepared/'decision_measurements.json').read_bytes())
    jobs={j['job_id']:j for j in json.loads((prepared/'decision_jobs.json').read_bytes())}
    ds=json.loads((prepared/'decision_service_accounting.json').read_bytes())
    planners={r['case_id']:r for r in json.loads((prepared/'classical_baseline.json').read_bytes())['rows']}
    construction=json.loads((execution/'cache_costs.json').read_bytes())
    chunks=json.loads((prepared/'batches.json').read_bytes());terminals=[];transform_measurements=[]
    for i,chunk in enumerate(chunks):
        path=prepared/f'batch-{i:02d}';verify_index(path)
        t=json.loads((path/'terminal.json').read_bytes());terminals.append(t)
        if t['status']!='COMPLETED' or not t['owned_server_stopped'] or t['dispatch_attempts']!=len(chunk):
            raise ValueError('Incomplete generation matrix')
        transform_measurements.extend(json.loads((path/f'{j:03d}.measurement.json').read_bytes()) for j in range(len(chunk)))
    transform_count=len(transform_measurements)
    if transform_count!=conf['planned_calls']:raise ValueError('Transformation count mismatch')
    transform_cold=sum(t['cold_start_seconds'] for t in terminals)
    decision_cold_per_call=ds['whole_matrix_cold_start_seconds']/ds['physical_calls_whole_matrix']
    rows=[]
    for i,cell in enumerate(cells):
        record=json.loads((execution/f'{i:03d}.json').read_bytes())
        if record['cell']!=cell:raise ValueError('Execution-to-plan mismatch')
        case=cases[cell['case_id']];e=record['execution'];s=record['score']
        if score_signature(s)!=score_signature(score_execution(case,e)):
            raise ValueError('Frozen score reproduction differs')
        required=[]
        if cell['decision_job_id']:
            required.append(cell['decision_job_id'])
            if cell['method']=='M4':required.append(jobs[cell['decision_job_id']]['proposal_id'])
        dcost=totals([dm[key] for key in required])
        m=record['transform_measurement'];tcost=totals([] if m is None else [m])
        if cell['requires_generation']!=(m is not None):raise ValueError('Missing transform measurement')
        plan=planners[cell['case_id']]['wall_seconds'] if cell['method']=='M6' else 0.0
        # execution_seconds includes parsing, gate and actual fallback, but not
        # scoring/counterworld oracle queries. Worker overhead is separate.
        latency=dcost['http_seconds']+tcost['http_seconds']+plan+e['execution_seconds']
        cold=dcost['calls']*decision_cold_per_call+(transform_cold/transform_count if m is not None else 0)
        history=0.0 if cell['method']=='M5' else construction[cell['case_id']]['total_seconds']
        row={'cell_id':cell['cell_id'],'base_id':case['base_id'],'template':case['template'],
            'family':case['family'],'method':cell['method'],'price':cell['price'],
            'sufficient':case['audit']['sufficient'],'action':cell['action'],
            'first_status':e['first_status'],'fallback':e['fallback_used'],
            'has_final_answer':e['final_result'] is not None,
            'final_correct':s['snapshot_final_correct'],'cache_first_correct':s['snapshot_cache_first_correct'],
            'both_test_worlds_correct':len(s['finite_worlds'])==2 and all(w['answer_matches'] for w in s['finite_worlds']),
            'decision_cost':dcost,'transform_cost':tcost,'planner_seconds':plan,
            'execution_seconds':e['execution_seconds'],'worker_wall_seconds':record['worker_wall_seconds'],
            'component_latency_seconds_excludes_cold_and_cache':latency,
            'allocated_cold_seconds':cold,'cache_construction_seconds':history,
            'modeled_total_seconds_by_cache_uses':{str(n):latency+cold+history/n for n in (1,10,100)}}
        rows.append(row)
    groups={}
    for method in ('M1','M2','M3','M4','M5','M6'):
        groups[method]={}
        for price in (1,10,20):
            part=[r for r in rows if r['method']==method and r['price']==price]
            if len(part)!=24 or sum(r['sufficient'] for r in part)!=12:raise ValueError('Incorrect fixed denominator')
            groups[method][str(price)]={
                'n':24,'positive_n':12,'negative_n':12,
                'first_statuses':dict(Counter(r['first_status'] for r in part)),
                'reuse_selected':sum(r['action']=='reuse' for r in part),
                'negative_reuse_selected':sum(r['action']=='reuse' and not r['sufficient'] for r in part),
                'final_correct':sum(r['final_correct'] for r in part),
                'no_final_answer':sum(not r['has_final_answer'] for r in part),
                'fallbacks':sum(r['fallback'] for r in part),
                'cache_first_correct':sum(r['cache_first_correct'] for r in part),
                'positive_cache_first_correct':sum(r['cache_first_correct'] and r['sufficient'] for r in part),
                'negative_snapshot_lucky_cache_matches':sum(r['cache_first_correct'] and not r['sufficient'] for r in part),
                'same_SQL_correct_in_both_test_worlds':sum(r['both_test_worlds_correct'] for r in part),
                'logical_calls':sum(r['decision_cost']['calls']+r['transform_cost']['calls'] for r in part),
                'logical_input_tokens':sum(r['decision_cost']['input_tokens']+r['transform_cost']['input_tokens'] for r in part),
                'logical_output_tokens':sum(r['decision_cost']['output_tokens']+r['transform_cost']['output_tokens'] for r in part),
                'mean_component_latency_seconds':sum(r['component_latency_seconds_excludes_cold_and_cache'] for r in part)/24,
                'mean_modeled_total_seconds_by_cache_uses':{str(n):sum(r['modeled_total_seconds_by_cache_uses'][str(n)] for r in part)/24 for n in (1,10,100)}}
    result={'status':'E3_COMPLETED_FROZEN_SCORE_REPRODUCED','model_family':conf.get('model_family','qwen'),
        'prepared':str(prepared),'manifest_sha256':manifest,'logical_cells':432,'bases':12,'templates':12,
        'groups':groups,'rows':rows,'physical_new_transform_usage':totals(transform_measurements),
        'physical_reused_decision_usage':totals(list(dm.values())),
        'decision_service_accounting':ds,'new_transform_cold_seconds':transform_cold,
        'new_owned_service_seconds':sum(t['owned_server_seconds'] for t in terminals),
        'cpu_phase_seconds':terminal['seconds'],'all_services_closed':True,
        'goal_new_attempts_total':prior['goal_new_attempts_total']+sum(t['dispatch_attempts'] for t in terminals),
        'goal_service_seconds_total':prior['goal_service_seconds_total']+sum(t['owned_server_seconds'] for t in terminals),
        'actual_API_cny':0,'energy_measured':False,
        'cost_warning':'Logical price workflows charge full decision incl M4 proposal; M3 shared physical calls not independent. Cold allocated uniformly, not directly observed cell latency. JVM startup included in classical wall time. Tiny fixture timings are not production speedups. Scoring/counterworld CPU excluded from method cost; actual CPU phase reported separately. M5 not charged unused cache. Others charged cache construction even when recomputing; amortization1/10/100. Wrong cheap answers are not efficiency gains.',
        'scope_warning':'Fixed instance0 of12 selected held templates; 432cells are not432 independent tasks. Correct public SQL supplied to all. Frozen negative information-loss witnesses override lucky snapshot matches. No model repair or oracle-triggered fallback. Finite-world correctness is not a universal proof.'}
    output.mkdir(parents=True,exist_ok=False)
    save(output/'summary.json',result)
    save(output/'input_hashes.json',{'manifest':manifest,'execution_index':sha(execution/'sha256.json'),
        'batch_indices':{str(i):sha(prepared/f'batch-{i:02d}'/'sha256.json') for i in range(len(chunks))}})
    save(output/'sha256.json',{p.name:sha(p) for p in output.iterdir() if p.is_file()})
    print(json.dumps({k:result[k] for k in ('status','model_family','logical_cells','physical_new_transform_usage','goal_new_attempts_total')},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared',type=Path,required=True);p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    summarize(a.prepared,a.manifest_sha256,a.output)
