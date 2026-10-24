"""Offline finished-trajectory replay for the factorial protocol.

Caller must verify the whole sealed batch, frozen sources and input provenance
BEFORE loading answers. Never feed a running/partial batch or select successes.
This component performs no SQL/network, semantic scoring or human annotation.
"""
from copy import deepcopy
import json
import math
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime.cache_action_trace_audit import SavedTools, same, require, elapsed, verify_tree, read_json
from runtime.cache_action_factorial_capacity import FactorialCapacity, validate_cell
from runtime.cache_action_factorial_loop import initial_state, run
from runtime.cache_action_adapter import ActionAdapter
from runtime.cache_intent_tools import ToolSession, valid_result, VERSION, SHORT_RECOVERY_PROFILE
from runtime.cache_intent_journal import verify as verify_journal
from runtime.deepseek_trajectory_adapter import CostGuard


class SavedFactorialTools(SavedTools):
    """Reuse pure admission/response checks, replacing only old input binding."""
    def __init__(self, job, records, cache):
        self.records,self.cache=records,cache
        packet=validate_cell(job['envelope']); valid_result(cache)
        same(cache['sql_sha256'],audit.digest(packet['history']['sql']),'History SQL/cache mismatch')
        settings=packet['context']['cache']
        # Never call ToolSession.__init__, call or close: those access a DB.
        self.state=object.__new__(ToolSession)
        for key,value in dict(available=settings['materialized_available'],cache_cost=settings['read_cost'],
                initial_budget=30,balance=30,attempts=0,sql_calls=0,budget_profile=SHORT_RECOVERY_PROFILE,
                max_attempts=3,nonbinding_for_configured_horizon=True,
                expected_db=packet['context']['database_sha256']).items():
            setattr(self.state,key,value)
        same(self.describe(),initial_state(job['envelope']),'Replay initial state mismatch')
        self.used={'started','summary'}
        start=records['started']
        same(start['version'],VERSION,'Tool version differs')
        same(start['configuration'],self.describe(),'Initial tool state differs')
        same(start['history_sql_sha256'],cache['sql_sha256'],'Tool history differs')
        same(start['cache_rows_sha256'],cache['sequence_sha256'],'Tool rows differ')
        self.counts=dict(successful_cache_reads=0,failed_cache_reads=0,SQL_attempts=0,successful_SQL_results=0)


def replay(job,records,tools,cache,counter,guard,*,mode):
    require(mode in ('OFFICIAL_DEVELOPMENT','SCRIPTED_NOT_MODEL'),'Explicit backend mode required')
    same(records['backend'],dict(kind=mode,provider_invoice_verified=False),'Backend mode differs')
    session=SavedFactorialTools(job,tools,cache)
    used={'backend'}; capacity=FactorialCapacity(job['envelope'],counter)
    transport_count=0; wall=0

    def save(name,value):
        require(name in records,'Missing replay record: '+name)
        expected=deepcopy(records[name]); actual=deepcopy(value)
        if 'wall_seconds' in actual:
            elapsed(expected['wall_seconds']); actual.pop('wall_seconds'); expected.pop('wall_seconds')
        if name.startswith('api-') and name.endswith('.pending'):
            timeout=expected['timeout_seconds']
            require(type(timeout) in (int,float) and math.isfinite(timeout) and 0<timeout<=90,'Invalid timeout')
            actual.pop('timeout_seconds'); expected.pop('timeout_seconds')
        same(expected,actual,'Replay record differs: '+name)
        require(name not in used,'Duplicate replay emission')
        used.add(name)

    def transport(payload,timeout):
        nonlocal transport_count,wall
        transport_count+=1
        provider=records[f'api-g{transport_count:03d}.provider']
        elapsed(provider['wall_seconds']); wall+=provider['wall_seconds']
        raw=provider['raw']
        if mode=='OFFICIAL_DEVELOPMENT':
            name=f'transport-{transport_count:03d}'; record=records[name]
            same(sorted(record),['response_text','transport_status'],'Unexpected transport record')
            require(record['transport_status']=='RESPONSE_RECEIVED','Transport failed; failure audit required')
            same(json.loads(record['response_text']),raw,'Provider/transport differ'); used.add(name)
        return deepcopy(raw)

    adapter=ActionAdapter(counter=counter,capacity=capacity,transport=transport,save=save,
                          guard=guard,deadline=600,clock=lambda:0)
    result=run(job['envelope'],session,adapter,save,capacity.observation_bound(),deadline=600,clock=lambda:0)
    session.finish()
    require(set(records)==used,'Extra or missing trace records')
    final_answer=None; first_valid=None; actions=[]
    for number in range(1,result['generation_calls']+1):
        decision=records[f'g{number:03d}.decision']
        action=decision.get('action')
        if action is not None:
            if first_valid is None: first_valid=action['kind']
            actions.append(dict(generation=number,action=deepcopy(action),status=decision['status'],
                                dispatched_to_tool=decision['dispatch_tool']))
    if result['status']=='ANSWERED':
        final_answer=records[f"g{result['generation_calls']:03d}.decision"]['action']['text']
    return dict(final_status=result['status'],final_answer=final_answer,first_valid_action=first_valid,
                actions=actions,generation_calls=result['generation_calls'],tool_attempts=result['tool_attempts'],
                dispatch_attempts=transport_count,summed_provider_wall_seconds=wall,
                spent_declared_units=30-session.state.balance,**session.counts,
                semantic_labels=0,C_drift_assessed=False,new_API_calls=0,new_SQL_executions=0)


def finished_batch(root,plan,cache,counter,*,expected_plan_sha,mode,ceiling_micro_cny,input_rate,output_rate):
    """All four sealed FINISHED cells only. Partial batches need failure audit.

The independent caller must verify frozen code and upstream task/reference
admission. This proves local consistency, not independent provider attestation.
"""
    from runtime.cache_action_factorial_batch import validate
    from runtime import cache_intent_tools as tools_module
    require(mode in ('SCRIPTED_NOT_MODEL','OFFICIAL_DEVELOPMENT'),'Explicit mode required')
    root=Path(root)
    require((root/'artifact_hashes.json').is_file(),'Unsealed batch; do not inspect effects')
    hashes=verify_tree(root); summary=read_json(root/'summary.json')
    require(summary['status']==mode+'_FINISHED' and summary['stop_reason'] is None,
            'Not finished; preserve entire batch for failure audit')
    validate(plan,cache,expected_plan_sha); verify_journal(root/'batch')
    same(read_json(root/'batch/summary.json'),summary,'Batch summary copies differ')
    same(read_json(root/'batch/plan.json'),plan,'Executed plan differs')
    guard=CostGuard(ceiling_micro_cny=ceiling_micro_cny,max_calls=16,input_rate=input_rate,output_rate=output_rate)
    capacities=[FactorialCapacity(j['envelope'],counter).certificate() for j in plan['jobs']]
    conditional=sum(c['total_input_bound']*input_rate+c['total_output_bound']*output_rate for c in capacities)
    same(read_json(root/'batch/settings.json'),dict(mode=mode,max_calls=16,max_tool_attempts=12,max_seconds=600,
         max_output_tokens=512,ceiling_micro_cny=ceiling_micro_cny,
         rates_micro_cny_per_token=[input_rate,output_rate],conditional_cost_micro_cny=conditional,
         current_prices_verified_here=False,automatic_retry=False,confirmation_study=False),'Batch settings differ')
    expected_batch={'plan.json','settings.json','summary.json','artifact_hashes.json'}
    for i in range(4): expected_batch.update((f'job-{i:02d}.pending.json',f'job-{i:02d}.outcome.json'))
    require({p.name for p in (root/'batch').iterdir()}==expected_batch,'Unexpected batch entries')
    require({p.name for p in root.iterdir()}=={'batch','summary.json','artifact_hashes.json'}|{j['id'] for j in plan['jobs']},
            'Unexpected experiment entries')
    require(len(summary['rows'])==4,'Missing planned slots')
    results=[]; inputs=outputs=0
    for index,(job,row) in enumerate(zip(plan['jobs'],summary['rows'])):
        same(row['id'],job['id'],'Row order differs')
        same(read_json(root/'batch'/f'job-{index:02d}.pending.json'),dict(id=job['id'],automatic_retry=False),'Dispatch identity differs')
        same(read_json(root/'batch'/f'job-{index:02d}.outcome.json'),row,'Outcome copies differ')
        directory=root/job['id']
        require({p.name for p in directory.iterdir()}=={'trace','tools'},'Unexpected job entries')
        for folder in ('trace','tools'): verify_journal(directory/folder)
        def records(folder):
            return {p.stem:read_json(p) for p in (directory/folder).glob('*.json') if p.name!='artifact_hashes.json'}
        trace,tools=records('trace'),records('tools')
        for filename,module in (('tool_source.py',tools_module),('readonly_source.py',audit)):
            same(audit.hash_file(directory/'tools'/filename),audit.hash_file(module.__file__),'Copied tool code differs')
        start=tools['started']
        same(start['input_receipt'],dict(plan_sha256=expected_plan_sha,job_id=job['id'],backend=mode),'Input receipt differs')
        same(start['source_sha256'],audit.hash_file(directory/'tools/tool_source.py'),'Tool source receipt differs')
        same(start['readonly_source_sha256'],audit.hash_file(directory/'tools/readonly_source.py'),'SQL source receipt differs')
        same(start['limits'],audit.LIMITS,'SQL limits differ')
        result=replay(job,trace,tools,cache,counter,guard,mode=mode)
        same(row,dict(id=job['id'],status=result['final_status'],loop_generation_calls=result['generation_calls'],
                      dispatch_attempts=result['dispatch_attempts'],tools=tools['summary']),'Reconstructed row differs')
        for n in range(1,result['dispatch_attempts']+1):
            usage=trace[f'api-g{n:03d}.provider']['raw']['usage']
            inputs+=usage['prompt_tokens']; outputs+=usage['completion_tokens']
        results.append(dict(id=job['id'],trace_sha256=audit.hash_file(directory/'trace/artifact_hashes.json'),**result))
    same(summary['cost_guard'],guard.snapshot(),'Shared cost accounting differs')
    for key,value in dict(planned_trajectories=4,independent_task_pairs=1,
            dispatch_attempts=sum(r['dispatch_attempts'] for r in results),
            model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None,semantic_labels=0,answers_graded=0,
            confirmation_study=False,invoice_verified=False,automatic_retry=False).items():
        same(summary.get(key),value,'Batch accounting differs: '+key)
    same(verify_tree(root),hashes,'Artifacts changed during replay')
    return dict(status='SEALED_FACTORIAL_FINISHED_REPLAY_VERIFIED',mode=mode,rows=results,
                experiment_manifest_sha256=audit.hash_file(root/'artifact_hashes.json'),
                verified_files=len(hashes),planned_trajectories=4,independent_task_pairs=1,
                prompt_tokens=inputs,completion_tokens=outputs,cost_guard=guard.snapshot(),
                new_API_calls=0,new_SQL_executions=0,semantic_labels=0,C_drift_assessed=False)
