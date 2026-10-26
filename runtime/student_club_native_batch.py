"""Fixed twelve-condition development driver; no credentials or live launcher.

Official use additionally requires pinned input admission, source freeze, fresh
budget approval and an owned hard-timeout worker. Injected scripted tests are
software checks, not evidence about model behavior. No retries or resumption.
"""
from copy import deepcopy
import math
from pathlib import Path
import time

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_journal import Journal, verify
from runtime.cache_intent_tools import valid_result
from runtime.cache_route_symmetric_loop import ObservationBound
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime.student_club_definition_history import DefinitionHistory
from runtime.student_club_diagnostic_design import build
from runtime.student_club_diagnostic_loop import run as run_loop
from runtime.student_club_native_adapter import Adapter, MAX_INPUT
from runtime.student_club_native_wire import MAX_OUTPUT, payload
from runtime.student_club_readonly import ToolSession
from scripts.fetch_bird_fixed import no_link_ancestors

VERSION='student-club-native-twelve-cell-v1'
MAX_CALLS,MAX_TOOLS,MAX_SECONDS=56,36,600
CEILING=1100000
INPUT_RATE,OUTPUT_RATE=2,8
MODES=('SCRIPTED_NOT_MODEL','OFFICIAL_NATIVE_DEVELOPMENT')


def build_plan(common,cache):
    design=build(common)
    valid_result(cache)
    if (cache.get('sql_sha256')!=audit.digest(common['history_sql'])
            or len(cache['columns'])!=1 or len(cache['rows'])!=1
            or cache['rows'][0][0][0]!='integer' or int(cache['rows'][0][0][1])<0):
        raise ValueError('Historical nonnegative count cache binding required')
    return dict(version=VERSION,design=design,cache_sha256=audit.digest(cache),
        common_sha256=audit.digest(common),task_pairs=1,dependent_conditions=12,
        independent_confirmation=False,inference_authorized=False,
        max_model_calls=MAX_CALLS,max_tool_attempts=MAX_TOOLS,
        ceiling_micro_cny=CEILING,rates_micro_cny_per_token=[INPUT_RATE,OUTPUT_RATE])


def run(plan,common,cache,*,expected_plan_sha,database,output,native_counter,
        plain_counter,transport_factory,deadline,mode,clock=time.monotonic):
    if mode not in MODES:raise ValueError('Explicit backend mode required')
    # Defensive copies: caller callbacks cannot mutate the remaining design.
    plan,common,cache=deepcopy((plan,common,cache))
    if (audit.digest(plan)!=expected_plan_sha
            or audit.encoded(plan)!=audit.encoded(build_plan(common,cache))):
        raise ValueError('Frozen plan/input mismatch')
    if (type(deadline) not in (int,float) or not math.isfinite(deadline)
            or not 0<deadline-clock()<=MAX_SECONDS):
        raise ValueError('Ten-minute deadline required')
    jobs=plan['design']['jobs']
    counts=[]
    for job in jobs:
        request=DefinitionHistory(common,job,lambda *args:None).begin()
        receipt=native_counter.inspect(request)
        n=receipt['reference_input_tokens']
        if (type(n) is not int or not 0<n<=MAX_INPUT
                or receipt['wire_payload_sha256']!=audit.digest(payload(request))):
            raise ValueError('Initial counted-wire binding/cap mismatch')
        counts.append(n)
    bound=(sum(counts)+(MAX_CALLS-len(jobs))*MAX_INPUT)*INPUT_RATE+MAX_CALLS*MAX_OUTPUT*OUTPUT_RATE
    if bound>CEILING:raise ValueError('Conditional full allocation exceeds ceiling')
    database=Path(database);no_link_ancestors(database)
    if (not database.is_file() or audit.hash_file(database)!=common['database_sha256']
            or any(Path(str(database)+s).exists() for s in ('-wal','-shm','-journal'))):
        raise ValueError('Fixed database absent, changed or has sidecars')
    output=Path(output);no_link_ancestors(output);output.mkdir(parents=True,exist_ok=False)
    batch=Journal(output/'batch')
    batch('plan',plan)
    batch('settings',dict(mode=mode,first_counts=counts,conditional_micro_cny=bound,
        max_seconds=MAX_SECONDS,full_horizon_fit_guaranteed=False,
        approved_here=False,automatic_retry=False))
    guard=CostGuard(ceiling_micro_cny=CEILING,max_calls=MAX_CALLS,
        input_rate=INPUT_RATE,output_rate=OUTPUT_RATE)
    observation_bound=ObservationBound(plain_counter.chat)
    rows=[dict(id=j['id'],status='NOT_STARTED',dispatch_attempts=0,tool_attempts=0) for j in jobs]
    stop=None
    for index,(job,row) in enumerate(zip(jobs,rows)):
        if stop:break
        logs=[];session=adapter=None
        batch(f'job-{index:02d}.pending',dict(id=job['id'],automatic_retry=False))
        try:
            if clock()>=deadline:raise TimeoutError('Batch deadline')
            root=output/job['id']
            loop_log=Journal(root/'loop');logs.append(loop_log)
            api_log=Journal(root/'api');logs.append(api_log)
            session=ToolSession(database_path=database,database_sha256=common['database_sha256'],
                history_sql=common['history_sql'],cached_result=cache,
                input_receipt=dict(plan_sha256=expected_plan_sha,job_id=job['id'],backend=mode),
                output=root/'tools',materialized_available=job['available'],cache_read_cost=job['price'])
            adapter=Adapter(common=common,job=job,counter=native_counter,
                transport=transport_factory(job['id'],api_log),save=api_log,guard=guard,
                deadline=deadline,clock=clock)
            result=run_loop(common,job,session,adapter,loop_log,observation_bound,
                deadline=deadline,clock=clock)
            row.update(status=result['status'],generation_callback_attempts=result['generation_callback_attempts'])
            if clock()>=deadline:raise TimeoutError('Batch deadline')
        except BaseException as error:
            row.update(status='INTERRUPTED' if isinstance(error,(KeyboardInterrupt,SystemExit)) else 'FAILED',
                error_type=type(error).__name__)
            stop=type(error).__name__
        finally:
            row['dispatch_attempts']=0 if adapter is None else adapter.count
            if session is not None:
                row['tool_attempts']=session.attempts
                try:
                    closed=session.close();verify(session.output);row['tools']=closed
                    if closed['status']!='TOOL_SESSION_CLOSED':
                        row['status']='ENVIRONMENT_INVALID';stop=closed['status']
                except BaseException as error:
                    row.update(status='FINALIZATION_FAILED',finalization_error=type(error).__name__)
                    stop='FINALIZATION_FAILED'
            for log in logs:
                try:log.seal()
                except BaseException as error:
                    row.update(status='FINALIZATION_FAILED',journal_error=type(error).__name__)
                    stop='FINALIZATION_FAILED'
        batch(f'job-{index:02d}.outcome',row)
    summary=dict(status=mode+('_STOPPED' if stop else '_FINISHED'),stop_reason=stop,rows=rows,
        planned_trajectories=12,task_pairs=1,independent_confirmation=False,
        dispatch_attempts=sum(r['dispatch_attempts'] for r in rows),
        tool_attempts=sum(r['tool_attempts'] for r in rows),
        real_model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None,
        semantic_labels=0,answers_graded=0,ordinary_definition_drift='NOT_ASSESSED',
        cost_guard=guard.snapshot(),invoice_verified=False,automatic_retry=False)
    batch('summary',summary);batch.seal()
    audit.save_new(output/'summary.json',summary)
    audit.save_new(output/'artifact_hashes.json',{
        p.relative_to(output).as_posix():audit.hash_file(p) for p in sorted(output.rglob('*')) if p.is_file()})
    return summary
