"""Whole-batch offline gate: replay FINISHED or inventory STOPPED, never resume.

The owning launcher/auditor must verify termination, upstream task/reference
admission, frozen code and dependencies before calling. Local hashes establish
integrity/consistency, not independent provider authenticity. No SQL/network.
Stopped inventories deliberately contain no answer ratings or prefix effects.
"""
from copy import deepcopy
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_tools as base_tools
from runtime import cache_route_symmetric_tools as symmetric_tools
from runtime.cache_action_trace_audit import require, same, read_json, verify_tree
from runtime.cache_intent_journal import verify as verify_journal
from runtime.cache_route_symmetric_batch import validate
from runtime.cache_route_symmetric_capacity import SymmetricCapacity
from runtime.cache_route_symmetric_replay import replay
from runtime.deepseek_trajectory_adapter import CostGuard

COMPLETED = {'ANSWERED', 'ABSTAINED', 'INVALID_ACTION', 'INVALID_ACTION_RESPONSE', 'TOOL_HORIZON_VIOLATION'}
FAILED = {'FAILED', 'INTERRUPTED', 'ENVIRONMENT_INVALID', 'FINALIZATION_FAILED'}


def _layout(root, plan, cache, counter, *, expected_plan_sha, mode, ceiling_micro_cny, input_rate, output_rate):
    require(mode in ('SCRIPTED_NOT_MODEL', 'OFFICIAL_DEVELOPMENT'), 'Explicit mode required')
    require(all(type(x) is int and x > 0 for x in (ceiling_micro_cny,input_rate,output_rate)),
            'Positive integer accounting parameters required')
    root = Path(root)
    require((root/'artifact_hashes.json').is_file(), 'Unsealed batch; cannot inspect effects')
    hashes = verify_tree(root)
    validate(plan, cache, expected_plan_sha)
    verify_journal(root/'batch')
    summary = read_json(root/'summary.json')
    same(summary, read_json(root/'batch/summary.json'), 'Summary copies differ')
    same(plan, read_json(root/'batch/plan.json'), 'Executed plan differs')
    stopped = summary['status'] == mode+'_STOPPED'
    require(stopped or summary['status'] == mode+'_FINISHED', 'Unknown batch status')
    require((type(summary['stop_reason']) is str and bool(summary['stop_reason'])) if stopped
            else summary['stop_reason'] is None, 'Stop reason/status inconsistent')
    certificates = [SymmetricCapacity(j['envelope'],plan['common'],counter).certificate() for j in plan['jobs']]
    conditional = sum(c['total_input_bound']*input_rate+c['total_output_bound']*output_rate for c in certificates)
    require(conditional <= ceiling_micro_cny, 'Full allocation exceeded admitted cap')
    same(read_json(root/'batch/settings.json'), dict(mode=mode,max_calls=16,max_tool_attempts=12,max_seconds=600,
         max_output_tokens=512,ceiling_micro_cny=ceiling_micro_cny,
         rates_micro_cny_per_token=[input_rate,output_rate],conditional_cost_micro_cny=conditional,
         current_prices_verified_here=False,automatic_retry=False,confirmation_study=False), 'Settings differ')
    rows = summary['rows']
    require(type(rows) is list and len(rows) == 4, 'All four planned slots required')
    expected_batch = {'plan.json','settings.json','summary.json','artifact_hashes.json'}
    expected_top = {'batch','summary.json','artifact_hashes.json'}
    failure_seen = False
    for index,(job,row) in enumerate(zip(plan['jobs'], rows)):
        same(row['id'],job['id'],'Cell order/identity differs')
        require(type(row['dispatch_attempts']) is int and 0 <= row['dispatch_attempts'] <= 4, 'Bad dispatch count')
        if row['status'] == 'NOT_STARTED':
            require(stopped and failure_seen, 'Unstarted cell before a recorded failure')
            same(row,dict(id=job['id'],status='NOT_STARTED',dispatch_attempts=0),'Unstarted cell has extra payload')
            require(not (root/job['id']).exists(), 'Unstarted cell has artifacts')
            continue
        require(not failure_seen, 'Later work started after terminal failure')
        require(row['status'] in COMPLETED | FAILED, 'Unexpected trajectory outcome')
        if row['status'] in FAILED: failure_seen = True
        for suffix in ('pending','outcome'): expected_batch.add(f'job-{index:02d}.{suffix}.json')
        same(read_json(root/'batch'/f'job-{index:02d}.pending.json'),
             dict(id=job['id'],automatic_retry=False),'Dispatch declaration differs')
        same(read_json(root/'batch'/f'job-{index:02d}.outcome.json'),row,'Outcome copies differ')
        if (root/job['id']).exists(): expected_top.add(job['id'])
        elif row['status'] in COMPLETED: raise ValueError('Completed cell has no artifacts')
    require(failure_seen == stopped, 'Finished/stopped batch contradicts outcomes')
    same(sorted(p.name for p in (root/'batch').iterdir()),sorted(expected_batch),'Batch inventory differs')
    same(sorted(p.name for p in root.iterdir()),sorted(expected_top),'Unexpected experiment entries')
    for key,value in dict(planned_trajectories=4,independent_task_pairs=1,
            dispatch_attempts=sum(r['dispatch_attempts'] for r in rows),
            model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None,semantic_labels=0,answers_graded=0,
            confirmation_study=False,invoice_verified=False,automatic_retry=False).items():
        same(summary.get(key),value,'Accounting differs: '+key)
    return root,hashes,summary


def finished_batch(root,plan,cache,counter,*,expected_plan_sha,mode,ceiling_micro_cny,input_rate,output_rate):
    """Reconstruct ALL four finished slots and shared accounting before scoring."""
    root,hashes,summary = _layout(root,plan,cache,counter,expected_plan_sha=expected_plan_sha,mode=mode,
        ceiling_micro_cny=ceiling_micro_cny,input_rate=input_rate,output_rate=output_rate)
    require(summary['status'] == mode+'_FINISHED', 'Stopped batch: inventory only, no prefix scoring')
    guard = CostGuard(ceiling_micro_cny=ceiling_micro_cny,max_calls=16,input_rate=input_rate,output_rate=output_rate)
    results = []; inputs = outputs = 0
    for job,row in zip(plan['jobs'],summary['rows']):
        directory = root/job['id']
        same(sorted(p.name for p in directory.iterdir()), ['tools','trace'], 'Unexpected job entries')
        for folder in ('trace','tools'): verify_journal(directory/folder)
        def records(folder):
            return {p.stem:read_json(p) for p in (directory/folder).glob('*.json') if p.name!='artifact_hashes.json'}
        trace,tools = records('trace'),records('tools')
        expected_tool_files = {name+'.json' for name in tools} | {'artifact_hashes.json',
            'tool_source.py','base_tool_source.py','readonly_source.py'}
        same(sorted(p.name for p in (directory/'tools').iterdir()),sorted(expected_tool_files),'Extra tool artifacts')
        same(sorted(p.name for p in (directory/'trace').iterdir()),
             sorted({name+'.json' for name in trace} | {'artifact_hashes.json'}),'Extra trace artifacts')
        start = tools['started']
        for filename,module,field in (('tool_source.py',symmetric_tools,'source_sha256'),
                ('base_tool_source.py',base_tools,'base_source_sha256'),('readonly_source.py',audit,'readonly_source_sha256')):
            digest = audit.hash_file(directory/'tools'/filename)
            same(digest,audit.hash_file(module.__file__),'Copied source differs from loaded frozen source')
            same(start[field],digest,'Source receipt differs')
        same(start['input_receipt'],dict(plan_sha256=expected_plan_sha,job_id=job['id'],backend=mode),'Input receipt differs')
        same(start['limits'],audit.LIMITS,'Readonly limits differ')
        result = replay(job,plan['common'],trace,tools,cache,counter,guard,mode=mode)
        same(row,dict(id=job['id'],status=result['final_status'],loop_generation_calls=result['generation_calls'],
             dispatch_attempts=result['dispatch_attempts'],tools=tools['summary']),'Reconstructed row differs')
        for n in range(1,result['dispatch_attempts']+1):
            usage = trace[f'api-g{n:03d}.provider']['raw']['usage']
            inputs += usage['prompt_tokens']; outputs += usage['completion_tokens']
        results.append(dict(trace_sha256=audit.hash_file(directory/'trace/artifact_hashes.json'),**result))
    same(summary['cost_guard'],guard.snapshot(),'Shared cost accounting differs')
    same(verify_tree(root),hashes,'Artifacts changed during audit')
    return dict(status='SEALED_SYMMETRIC_FINISHED_REPLAY_VERIFIED',mode=mode,rows=results,
        experiment_manifest_sha256=audit.hash_file(root/'artifact_hashes.json'),verified_files=len(hashes),
        planned_trajectories=4,independent_task_pairs=1,prompt_tokens=inputs,completion_tokens=outputs,
        cost_guard=guard.snapshot(),new_API_calls=0,new_SQL_executions=0,semantic_labels=0,C_drift_assessed=False)


def stopped_inventory(root,plan,cache,counter,*,expected_plan_sha,mode,ceiling_micro_cny,input_rate,output_rate):
    """Closed failure inventory, NOT a scoring input or verified fee replay.

All four slots remain visible; no answers/actions from successful prefixes are
loaded for reporting. Nested seal errors remain explicit. Hard-killed/unsealed
outputs require a parent-owned terminal/failure archive, not this admission.
"""
    root,hashes,summary = _layout(root,plan,cache,counter,expected_plan_sha=expected_plan_sha,mode=mode,
        ceiling_micro_cny=ceiling_micro_cny,input_rate=input_rate,output_rate=output_rate)
    require(summary['status'] == mode+'_STOPPED','Failure inventory requires stopped batch')
    rows = []
    for row in summary['rows']:
        item = dict(id=row['id'],recorded_status=row['status'],recorded_dispatch_attempts=row['dispatch_attempts'])
        for key in ('error_type','finalization_error','trace_seal_error'):
            if key in row: item[key]=row[key]
        item['journals'] = {}
        for folder in ('trace','tools'):
            path = root/row['id']/folder
            if not path.exists():state='ABSENT'
            else:
                try:verify_journal(path);state='INTEGRITY_ONLY_VERIFIED'
                except (OSError,ValueError,KeyError,TypeError):state='UNSEALED_OR_INCONSISTENT'
            item['journals'][folder]=state
        rows.append(item)
    same(verify_tree(root),hashes,'Artifacts changed during inventory')
    return dict(status='SEALED_STOPPED_INVENTORY_ONLY_NOT_SCORED',mode=mode,rows=rows,
        stop_reason=summary['stop_reason'],planned_trajectories=4,independent_task_pairs=1,
        recorded_cost_guard=deepcopy(summary['cost_guard']),fee_reconstruction_verified=False,
        experiment_manifest_sha256=audit.hash_file(root/'artifact_hashes.json'),verified_files=len(hashes),
        completed_prefix_scored=False,answers_graded=0,new_API_calls=0,new_SQL_executions=0,
        warning='Recorded failure statuses and fees are retained, not independently reconstructed or imputed. No effect estimate from a stopped prefix.')
