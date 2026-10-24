"""Four-cell development batch with injected transport, not a paid launcher.

Source/review admission and current provider pricing/authorization belong to a
separate launcher. Rates passed here are client accounting bounds, not verified
prices. Invalid model actions remain outcomes; infrastructure errors stop all
remaining jobs. Existing or interrupted directories are never resumed.
"""
from copy import deepcopy
import math
from pathlib import Path
import re
import time

from runtime import bird_readonly_audit as audit
from runtime.cache_action_factorial_preview import audit_cells, SETTINGS
from runtime.cache_action_factorial_capacity import FactorialCapacity, validate_cell
from runtime.cache_action_factorial_loop import run as run_loop
from runtime.cache_action_adapter import ActionAdapter
from runtime.cache_intent_tools import ToolSession, SHORT_RECOVERY_PROFILE, valid_result
from runtime.cache_intent_journal import Journal, verify
from runtime.deepseek_trajectory_adapter import CostGuard
from scripts.fetch_bird_fixed import no_link_ancestors

VERSION = 'ordinary-factorial-batch-v1'
SEED = 'superhero-factorial-feasibility-v1'


def build_plan(cells, cache, input_plan_sha256):
    """Mechanical binding only; hash/review provenance must be checked upstream."""
    audit_cells(cells)
    valid_result(cache)
    if type(input_plan_sha256) is not str or re.fullmatch('[0-9a-f]{64}',input_plan_sha256) is None:
        raise ValueError('Missing sealed input plan hash')
    jobs=[]
    for cell,(available,price) in zip(cells,SETTINGS):
        packet=validate_cell(cell)
        if cache.get('sql_sha256') != audit.digest(packet['history']['sql']):
            raise ValueError('Cache not bound to history SQL')
        jobs.append(dict(id=f'available{int(available)}-price{price}',envelope=deepcopy(cell)))
    jobs.sort(key=lambda j:audit.digest(SEED+j['id']))
    return dict(version=VERSION,seed=SEED,jobs=jobs,input_plan_sha256=input_plan_sha256,
                cached_result_sha256=audit.digest(cache),independent_task_pairs=1,
                max_model_calls=16,max_tool_attempts=12,development_only=True,
                inference_authorized=False)


def validate(plan, cache, expected_sha):
    if audit.digest(plan) != expected_sha:
        raise ValueError('Frozen batch plan changed')
    try:
        jobs={j['id']:j for j in plan['jobs']}
        cells=[jobs[f'available{int(a)}-price{p}']['envelope'] for a,p in SETTINGS]
        expected=build_plan(cells,cache,plan['input_plan_sha256'])
    except (KeyError,TypeError) as exc:
        raise ValueError('Missing factorial cell or input binding') from exc
    if audit.encoded(plan) != audit.encoded(expected):
        raise ValueError('Batch schema, cell set/order or cache binding changed')


def run(plan, cache, *, expected_plan_sha, database, output, counter, transport_factory,
        deadline, mode, ceiling_micro_cny, input_rate, output_rate, clock=time.monotonic):
    if mode not in ('SCRIPTED_NOT_MODEL','OFFICIAL_DEVELOPMENT'):
        raise ValueError('Explicit backend mode required')
    if (type(deadline) not in (int,float) or not math.isfinite(deadline)
            or not 0 < deadline-clock() <= 600):
        raise ValueError('Finite deadline within ten minutes required')
    if any(type(v) is not int or v<=0 for v in (ceiling_micro_cny,input_rate,output_rate)):
        raise ValueError('Positive integer client cost ceiling and rates required')
    validate(plan,cache,expected_plan_sha)
    capacities={j['id']:FactorialCapacity(j['envelope'],counter) for j in plan['jobs']}
    conditional=sum(c.certificate()['total_input_bound']*input_rate+
                    c.certificate()['total_output_bound']*output_rate for c in capacities.values())
    if conditional>ceiling_micro_cny:
        raise ValueError('Full conditional allocation exceeds client cap')
    no_link_ancestors(output)
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    batch=Journal(output/'batch')
    batch('plan',plan)
    batch('settings',dict(mode=mode,max_calls=16,max_tool_attempts=12,max_seconds=600,
        max_output_tokens=512,ceiling_micro_cny=ceiling_micro_cny,
        rates_micro_cny_per_token=[input_rate,output_rate],conditional_cost_micro_cny=conditional,
        current_prices_verified_here=False,automatic_retry=False,confirmation_study=False))
    guard=CostGuard(ceiling_micro_cny=ceiling_micro_cny,max_calls=16,
                    input_rate=input_rate,output_rate=output_rate)
    rows=[dict(id=j['id'],status='NOT_STARTED',dispatch_attempts=0) for j in plan['jobs']]
    stop_reason=None

    def check():
        if clock()>=deadline: raise TimeoutError('Global batch deadline reached')

    for index,(job,row) in enumerate(zip(plan['jobs'],rows)):
        if stop_reason: break
        trace=session=adapter=None
        batch(f'job-{index:02d}.pending',dict(id=job['id'],automatic_retry=False))
        try:
            check()
            packet=validate_cell(job['envelope']); settings=packet['context']['cache']
            root=output/job['id']; trace=Journal(root/'trace')
            trace('backend',dict(kind=mode,provider_invoice_verified=False))
            session=ToolSession(database_path=database,
                database_sha256=packet['context']['database_sha256'],history_sql=packet['history']['sql'],
                cached_result=cache,input_receipt=dict(plan_sha256=expected_plan_sha,job_id=job['id'],backend=mode),
                output=root/'tools',materialized_available=settings['materialized_available'],
                cache_read_cost=settings['read_cost'],budget_profile=SHORT_RECOVERY_PROFILE)
            adapter=ActionAdapter(counter=counter,capacity=capacities[job['id']],
                transport=transport_factory(job['id'],trace),save=trace,guard=guard,
                deadline=deadline,clock=clock)
            result=run_loop(job['envelope'],session,adapter,trace,
                capacities[job['id']].observation_bound(),deadline=deadline,clock=clock)
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
                    closed=session.close(); verify(session.output)
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
    summary=dict(status=mode+('_STOPPED' if stop_reason else '_FINISHED'),stop_reason=stop_reason,
        rows=rows,planned_trajectories=4,independent_task_pairs=1,
        dispatch_attempts=sum(r['dispatch_attempts'] for r in rows),
        model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None,semantic_labels=0,answers_graded=0,
        confirmation_study=False,cost_guard=guard.snapshot(),invoice_verified=False,automatic_retry=False)
    batch('summary',summary); batch.seal()
    audit.save_new(output/'summary.json',summary)
    audit.save_new(output/'artifact_hashes.json',{
        p.relative_to(output).as_posix():audit.hash_file(p) for p in sorted(output.rglob('*')) if p.is_file()})
    return summary
