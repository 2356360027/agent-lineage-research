"""Fixed 12-trajectory development orchestrator; no credentials or default HTTP.

Only the separate explicitly approved launcher supplies a live transport factory.
All rows remain in the denominator, including invalid outputs and unstarted rows.
No model-effect inspection, retries, sample replacement or semantic labels.
"""
from pathlib import Path
import time

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_recovery_preview as preview
from runtime.cache_intent_batch import validate_plan, DeadlineSession
from runtime.cache_intent_journal import Journal, verify
from runtime.cache_intent_tools import ToolSession, RECOVERY_PROFILE
from runtime.cache_intent_staged_loop import run_recovery_trajectory
from runtime.deepseek_recovery_capacity import RecoveryCapacity
from runtime.deepseek_trajectory_adapter import Adapter, CostGuard


def run_batch(plan, caches, *, expected_plan_sha, database, output, counter,
              transport_factory, deadline, mode, clock=time.monotonic):
    if mode not in ('SCRIPTED_NOT_MODEL','OFFICIAL_UNREVIEWED'):
        raise ValueError('Explicit backend kind required')
    validate_plan(plan,caches)
    if audit.digest(plan) != expected_plan_sha:
        raise ValueError('Frozen plan changed')
    capacities = {j['id']:RecoveryCapacity(j['envelope'],counter) for j in plan['jobs']}
    maximum_cost = sum(c.certificate()['total_input_bound']*2+
                       c.certificate()['total_output_bound']*8 for c in capacities.values())
    if maximum_cost > 8_000_000:
        raise ValueError('Fixed full allocation exceeds 8 CNY conditional budget')
    output = Path(output)
    output.mkdir(parents=True,exist_ok=False)
    batch = Journal(output/'batch')
    batch('plan',plan)
    batch('settings',{'backend':mode,'max_calls':276,'max_output_tokens':512,
        'ceiling_micro_cny':8_000_000,'conditional_bound_micro_cny':maximum_cost,
        'rate_input_micro_cny_per_token':2,'rate_output_micro_cny_per_token':8,
        'automatic_retry':False,'confirmation_study':False,'independent_targets':2})
    guard = CostGuard(ceiling_micro_cny=8_000_000,max_calls=276,input_rate=2,output_rate=8)
    rows = [{'id':j['id'],'status':'NOT_STARTED','dispatch_attempts':0} for j in plan['jobs']]
    stop_reason = None
    def check():
        if clock() >= deadline:
            raise TimeoutError('Global batch deadline reached')
    for i, job in enumerate(plan['jobs']):
        if stop_reason: break
        trace = session = adapter = None
        batch(f'job-{i:02d}.pending',{'id':job['id'],'retry':False})
        try:
            check()
            packet = preview.validate_preview(job['envelope'])
            root = output/job['id']
            trace = Journal(root/'trace')
            trace('backend',{'kind':mode,'provider_usage_verified':False})
            session = ToolSession(database_path=database,
                database_sha256=packet['context']['database_sha256'],
                history_sql=packet['history']['sql'],cached_result=caches[job['history_id']],
                input_receipt={'plan_sha256':expected_plan_sha,'job_id':job['id'],'backend':mode},
                output=root/'tools',materialized_available=True,cache_read_cost=job['read_price'],
                budget_profile=RECOVERY_PROFILE)
            remaining = int(deadline-clock())
            if remaining < 1: raise TimeoutError('Deadline before adapter creation')
            transport = transport_factory(job['id'],trace)
            adapter = Adapter(counter=counter,transport=transport,save=trace,guard=guard,
                capacity=capacities[job['id']],max_seconds=min(1800,remaining),clock=clock)
            result = run_recovery_trajectory(job['envelope'],DeadlineSession(session,check),
                adapter,trace,capacities[job['id']].observation_bound())
            rows[i].update(status=result['status'],loop_generation_calls=result['generation_calls'])
            if result['status'] == 'ENVIRONMENT_INVALID': stop_reason='ENVIRONMENT_INVALID'
            check()
        except Exception as exc:
            rows[i].update(status='FAILED',error_type=type(exc).__name__)
            stop_reason = type(exc).__name__
        finally:
            rows[i]['dispatch_attempts'] = 0 if adapter is None else adapter.count
            if session is not None:
                try:
                    closed = session.close()
                    verify(session.output)
                    rows[i]['tools'] = closed
                    if closed['status'] != 'TOOL_SESSION_CLOSED':
                        rows[i]['status']='ENVIRONMENT_INVALID'; stop_reason=closed['status']
                except Exception as exc:
                    rows[i].update(status='FINALIZATION_FAILED',finalization_error=type(exc).__name__)
                    stop_reason='FINALIZATION_FAILED'
            if trace is not None:
                try: trace.seal()
                except Exception as exc:
                    rows[i].update(status='FINALIZATION_FAILED',seal_error=type(exc).__name__)
                    stop_reason='FINALIZATION_FAILED'
        batch(f'job-{i:02d}.outcome',rows[i])
    summary = {'status':mode+('_STOPPED' if stop_reason else '_FINISHED'),
        'stop_reason':stop_reason,'rows':rows,'planned_trajectories':12,'independent_targets':2,
        'dispatch_attempts':sum(r['dispatch_attempts'] for r in rows),
        'model_calls':0 if mode=='SCRIPTED_NOT_MODEL' else None,
        'model_call_note':'Dispatch attempts are not verified completions; inspect raw records.',
        'semantic_judgments':0,'confirmation_study':False,'cost_guard':guard.snapshot(),
        'provider_invoice_verified':False}
    batch('summary',summary); batch.seal()
    audit.save_new(output/'summary.json',summary)
    audit.save_new(output/'artifact_hashes.json',{p.relative_to(output).as_posix():audit.hash_file(p)
        for p in sorted(output.rglob('*')) if p.is_file()})
    return summary
