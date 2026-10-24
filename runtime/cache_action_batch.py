"""Fixed development batch with injected transport, never a live CLI.

The separately approved launcher owns credentials and full source/input admission.
No retries, replacement samples, interim-effect adaptation, or semantic grading.
"""
from pathlib import Path
import time

from runtime import bird_readonly_audit as audit
from runtime import cache_action_preview as preview
from runtime.cache_action_adapter import ActionAdapter
from runtime.cache_action_capacity import ActionCapacity
from runtime.cache_action_loop import run as run_loop
from runtime.cache_intent_tools import ToolSession, SHORT_RECOVERY_PROFILE, valid_result
from runtime.cache_intent_journal import Journal, verify
from runtime.deepseek_trajectory_adapter import CostGuard

CEILING = 500000


def validate(plan, caches, expected_sha):
    if audit.digest(plan) != expected_sha:
        raise ValueError('Frozen plan changed')
    if (set(plan) != {'version','seed','jobs','reused_development_tasks','maximum_calls',
                     'maximum_tool_attempts','authorization'} or plan['version'] != preview.VERSION
            or plan['seed'] != 'cache-action-availability-20261024-v1'
            or plan['reused_development_tasks'] != 2 or plan['maximum_calls'] != 48
            or plan['maximum_tool_attempts'] != 36 or plan['authorization'] is not False
            or len(plan['jobs']) != 12 or set(caches) != set(preview.HISTORIES)):
        raise ValueError('Unexpected fixed design')
    expected_ids = {f'{h}-available{a}' for h in preview.HISTORIES for a in (0,1)}
    if {j['id'] for j in plan['jobs']} != expected_ids:
        raise ValueError('Missing, duplicate or unsafe job ID')
    if plan['jobs'] != sorted(plan['jobs'],key=lambda j:audit.digest(plan['seed']+j['id'])):
        raise ValueError('Execution order changed')
    packets={}
    for job in plan['jobs']:
        if (set(job) != {'id','history_id','available','envelope','cached_result_sha256'}
                or type(job['available']) is not bool or job['history_id'] not in preview.HISTORIES
                or job['id'] != f"{job['history_id']}-available{int(job['available'])}"):
            raise ValueError('Unexpected job schema')
        packet = preview.validate(job['envelope'])
        cache = caches[job['history_id']]
        valid_result(cache)
        if (audit.digest(cache) != job['cached_result_sha256']
                or cache.get('sql_sha256') != audit.digest(packet['history']['sql'])
                or packet['context']['cache']['materialized_available'] is not job['available']):
            raise ValueError('Cache-to-history binding mismatch')
        packets[job['id']]=packet
    if len({p['context']['database_sha256'] for p in packets.values()}) != 1:
        raise ValueError('Expected one database snapshot')
    for hid in preview.HISTORIES:
        a, b = packets[hid+'-available0'], packets[hid+'-available1']
        a['context']['cache']['materialized_available']=True
        if a != b: raise ValueError('More than availability differs')


class DeadlineSession:
    def __init__(self, session, check): self.session, self.check = session, check
    def describe(self): return self.session.describe()
    def call(self, name, arguments):
        self.check()
        result = self.session.call(name, arguments)
        self.check()
        return result


def run(plan, caches, *, expected_plan_sha, database, output, counter,
        transport_factory, deadline, mode, clock=time.monotonic):
    if mode not in ('SCRIPTED_NOT_MODEL','OFFICIAL_DEVELOPMENT'):
        raise ValueError('Explicit execution mode required')
    if not 0 < deadline-clock() <= 600:
        raise ValueError('Deadline must be within ten minutes')
    validate(plan,caches,expected_plan_sha)
    capacities={j['id']:ActionCapacity(j['envelope'],counter) for j in plan['jobs']}
    maximum_cost=sum(c.certificate()['total_input_bound']*2+
                     c.certificate()['total_output_bound']*8 for c in capacities.values())
    if maximum_cost > CEILING:
        raise ValueError('Full conditional allocation exceeds fixed client cap')
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    batch=Journal(output/'batch')
    batch('plan',plan)
    batch('settings',dict(mode=mode,ceiling_micro_cny=CEILING,max_calls=48,
        max_seconds=600,max_output_tokens=512,conditional_cost_micro_cny=maximum_cost,
        rates_micro_cny_per_token=[2,8],automatic_retry=False,confirmation_study=False))
    guard=CostGuard(ceiling_micro_cny=CEILING,max_calls=48,input_rate=2,output_rate=8)
    rows=[dict(id=j['id'],status='NOT_STARTED',dispatch_attempts=0) for j in plan['jobs']]
    stop_reason=None
    def check():
        if clock() >= deadline: raise TimeoutError('Global batch deadline reached')
    for index,(job,row) in enumerate(zip(plan['jobs'],rows)):
        if stop_reason: break
        trace=session=adapter=None
        batch(f'job-{index:02d}.pending',dict(id=job['id'],automatic_retry=False))
        try:
            check()
            packet=preview.validate(job['envelope'])
            root=output/job['id']
            trace=Journal(root/'trace')
            trace('backend',dict(kind=mode,provider_invoice_verified=False))
            session=ToolSession(database_path=database,
                database_sha256=packet['context']['database_sha256'],
                history_sql=packet['history']['sql'],cached_result=caches[job['history_id']],
                input_receipt=dict(plan_sha256=expected_plan_sha,job_id=job['id'],backend=mode),
                output=root/'tools',materialized_available=job['available'],cache_read_cost=1,
                budget_profile=SHORT_RECOVERY_PROFILE)
            adapter=ActionAdapter(counter=counter,capacity=capacities[job['id']],
                transport=transport_factory(job['id'],trace),save=trace,guard=guard,
                deadline=deadline,clock=clock)
            result=run_loop(job['envelope'],DeadlineSession(session,check),adapter,trace,
                            capacities[job['id']].observation_bound())
            row.update(status=result['status'],loop_generation_calls=result['generation_calls'])
            check()
        except BaseException as exc:
            row.update(status='INTERRUPTED' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'FAILED',
                       error_type=type(exc).__name__)
            stop_reason=type(exc).__name__
        finally:
            row['dispatch_attempts']=0 if adapter is None else adapter.count
            if session is not None:
                try:
                    closed=session.close()
                    verify(session.output)
                    row['tools']=closed
                    if closed['status'] != 'TOOL_SESSION_CLOSED':
                        row['status']='ENVIRONMENT_INVALID'; stop_reason=closed['status']
                except BaseException as exc:
                    row.update(status='FINALIZATION_FAILED',finalization_error=type(exc).__name__)
                    stop_reason='FINALIZATION_FAILED'
            if trace is not None:
                try: trace.seal()
                except BaseException as exc:
                    row.update(status='FINALIZATION_FAILED',trace_seal_error=type(exc).__name__)
                    stop_reason='FINALIZATION_FAILED'
        batch(f'job-{index:02d}.outcome',row)
    summary=dict(status=mode+('_STOPPED' if stop_reason else '_FINISHED'),
        stop_reason=stop_reason,rows=rows,planned_trajectories=12,reused_development_tasks=2,
        dispatch_attempts=sum(r['dispatch_attempts'] for r in rows),
        model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None,
        semantic_labels=0,answers_graded=0,confirmation_study=False,cost_guard=guard.snapshot(),
        invoice_verified=False,automatic_retry=False)
    batch('summary',summary); batch.seal()
    audit.save_new(output/'summary.json',summary)
    audit.save_new(output/'artifact_hashes.json',{
        p.relative_to(output).as_posix():audit.hash_file(p) for p in sorted(output.rglob('*')) if p.is_file()})
    return summary
