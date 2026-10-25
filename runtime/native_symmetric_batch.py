"""Fixed eight-cell native development runner, injected transport only.

No credentials or live launcher. Caller must verify semantic admissions, sealed
inputs, current prices, frozen dependencies, fresh payment approval and enforce
the owned worker timeout. This module never retries or resumes any output.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import time

from runtime import bird_readonly_audit as audit
from runtime.cache_route_symmetric_design import build
from runtime.cache_route_symmetric_loop import initial_state, ObservationBound
from runtime.cache_route_symmetric_tools import ToolSession
from runtime.cache_intent_tools import valid_result
from runtime.cache_intent_journal import Journal, verify
from runtime.cache_action_preview import payload
from runtime.native_action_interface import build_candidate
from runtime.native_symmetric_loop import run as run_loop
from runtime.native_trajectory_adapter import NativeTrajectoryAdapter
from runtime.deepseek_trajectory_adapter import CostGuard
from scripts.fetch_bird_fixed import no_link_ancestors

VERSION='native-symmetric-eight-cell-v1'
SEED='native-full-trajectory-development-v1'
FAMILIES=('positive','strength')
MAX_CALLS=32
MAX_SECONDS=600
CEILING=700000
INPUT_RATE,OUTPUT_RATE=2,8
MAX_INPUT,MAX_OUTPUT=8192,512
CONDITIONAL=MAX_CALLS*(MAX_INPUT*INPUT_RATE+MAX_OUTPUT*OUTPUT_RATE)


def initial_payload(envelope):
    return json.loads(audit.encoded(build_candidate(payload(envelope['messages']))['payload']))


def build_plan(groups):
    if type(groups) is not dict or set(groups)!=set(FAMILIES):
        raise ValueError('Both fixed reviewed development families required')
    sources={};jobs=[]
    for family in FAMILIES:
        item=groups[family]
        fields={'common','cache','source_plan_sha256','input_manifest_sha256','admission_sha256'}
        if type(item) is not dict or set(item)!=fields: raise ValueError('Exact source fields required')
        for key in fields-{'common','cache'}:
            if type(item[key]) is not str or re.fullmatch('[0-9a-f]{64}',item[key]) is None:
                raise ValueError('Source fingerprint required')
        valid_result(item['cache'])
        if item['cache'].get('sql_sha256')!=audit.digest(item['common']['history_sql']):
            raise ValueError('History/cache SQL mismatch')
        cells=build(item['common'])
        sources[family]={key:deepcopy(item[key]) for key in fields-{'cache'}}
        sources[family]['cache_sha256']=audit.digest(item['cache'])
        jobs.extend(dict(id=f'{family}-cell-{i}',family=family,cell_index=i,envelope=cell)
                    for i,cell in enumerate(cells))
    if len({groups[f]['common']['database_sha256'] for f in FAMILIES})!=1:
        raise ValueError('This fixed development batch uses one shared snapshot')
    jobs.sort(key=lambda j:hashlib.sha256((SEED+j['id']).encode()).hexdigest())
    return json.loads(audit.encoded(dict(version=VERSION,seed=SEED,sources=sources,jobs=jobs,
        task_pairs=2,dependent_conditions=8,shared_database=True,independent_confirmation=False,
        max_model_calls=MAX_CALLS,max_tool_attempts=24,development_only=True,inference_authorized=False)))


def validate(plan,groups,expected_sha):
    if audit.digest(plan)!=expected_sha or audit.encoded(plan)!=audit.encoded(build_plan(groups)):
        raise ValueError('Frozen plan, source input or condition order differs')


def settings(mode):
    if mode not in ('SCRIPTED_NOT_MODEL','OFFICIAL_NATIVE_DEVELOPMENT'):
        raise ValueError('Explicit backend mode required')
    return dict(mode=mode,max_calls=MAX_CALLS,max_tool_attempts=24,max_seconds=MAX_SECONDS,
        max_reference_input_tokens=MAX_INPUT,max_output_tokens=MAX_OUTPUT,
        ceiling_micro_cny=CEILING,rates_micro_cny_per_token=[INPUT_RATE,OUTPUT_RATE],
        conditional_micro_cny=CONDITIONAL,all_histories_guaranteed_to_fit=False,
        current_prices_verified_here=False,automatic_retry=False,confirmation_study=False)


def input_receipt(plan_sha,job,mode):
    return dict(plan_sha256=plan_sha,job_id=job['id'],family=job['family'],backend=mode)


def new_guard():
    return CostGuard(ceiling_micro_cny=CEILING,max_calls=MAX_CALLS,
                     input_rate=INPUT_RATE,output_rate=OUTPUT_RATE)


def run(plan,groups,*,expected_plan_sha,database,output,native_counter,plain_counter,
        transport_factory,deadline,mode,clock=time.monotonic):
    config=settings(mode)
    validate(plan,groups,expected_plan_sha)
    if (type(deadline) not in (int,float) or not math.isfinite(deadline)
            or not 0<deadline-clock()<=MAX_SECONDS): raise ValueError('Ten-minute deadline required')
    if CONDITIONAL>CEILING: raise ValueError('Worst admitted allocation exceeds budget')
    # No sampled future output is used as a worst-case input certificate.
    counts=[native_counter.inspect(initial_payload(j['envelope']))['reference_input_tokens'] for j in plan['jobs']]
    if any(type(n) is not int or not 0<n<=MAX_INPUT for n in counts):
        raise ValueError('Initial request exceeds admission cap')
    database=Path(database);no_link_ancestors(database)
    expected_db=groups['positive']['common']['database_sha256']
    if (not database.is_file() or audit.hash_file(database)!=expected_db
            or any(Path(str(database)+suffix).exists() for suffix in ('-wal','-shm','-journal'))):
        raise ValueError('Fixed database missing or changed')
    output=Path(output);no_link_ancestors(output);output.mkdir(parents=True,exist_ok=False)
    batch=Journal(output/'batch');batch('plan',plan);batch('settings',config)
    guard=new_guard();bound=ObservationBound(plain_counter.chat)
    rows=[dict(id=j['id'],status='NOT_STARTED',dispatch_attempts=0) for j in plan['jobs']]
    stop=None
    for index,(job,row) in enumerate(zip(plan['jobs'],rows)):
        if stop:break
        logs=[];session=adapter=None
        batch(f'job-{index:02d}.pending',dict(id=job['id'],automatic_retry=False))
        try:
            if clock()>=deadline:raise TimeoutError('Batch deadline')
            root=output/job['id'];loop_log=Journal(root/'loop');logs.append(loop_log)
            api_log=Journal(root/'api');logs.append(api_log)
            item=groups[job['family']];state=initial_state(job['envelope'],item['common'])
            session=ToolSession(database_path=database,database_sha256=state['database_sha256'],
                history_sql=item['common']['history_sql'],cached_result=item['cache'],
                input_receipt=input_receipt(expected_plan_sha,job,mode),output=root/'tools',
                materialized_available=state['materialized_available'],cache_read_cost=state['read_cost'])
            adapter=NativeTrajectoryAdapter(counter=native_counter,
                transport=transport_factory(job['id'],api_log),save=api_log,guard=guard,
                deadline=deadline,clock=clock)
            result=run_loop(job['envelope'],item['common'],session,adapter,loop_log,bound,
                            deadline=deadline,clock=clock)
            row.update(status=result['status'],loop_generation_calls=result['generation_calls'])
            if clock()>=deadline:raise TimeoutError('Batch deadline')
        except BaseException as exc:
            row.update(status='INTERRUPTED' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'FAILED',
                       error_type=type(exc).__name__)
            stop=type(exc).__name__
        finally:
            row['dispatch_attempts']=0 if adapter is None else adapter.count
            if session is not None:
                try:
                    closed=session.close();verify(session.output);row['tools']=closed
                    if closed['status']!='TOOL_SESSION_CLOSED':
                        row['status']='ENVIRONMENT_INVALID';stop=closed['status']
                except BaseException as exc:
                    row.update(status='FINALIZATION_FAILED',finalization_error=type(exc).__name__)
                    stop='FINALIZATION_FAILED'
            for log in logs:
                try:log.seal()
                except BaseException as exc:
                    row.update(status='FINALIZATION_FAILED',journal_error=type(exc).__name__)
                    stop='FINALIZATION_FAILED'
        batch(f'job-{index:02d}.outcome',row)
    summary=dict(status=mode+('_STOPPED' if stop else '_FINISHED'),stop_reason=stop,rows=rows,
        planned_trajectories=8,task_pairs=2,shared_database=True,independent_confirmation=False,
        dispatch_attempts=sum(r['dispatch_attempts'] for r in rows),
        model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None,semantic_labels=0,answers_graded=0,
        cost_guard=guard.snapshot(),invoice_verified=False,automatic_retry=False)
    batch('summary',summary);batch.seal();audit.save_new(output/'summary.json',summary)
    audit.save_new(output/'artifact_hashes.json',{
        p.relative_to(output).as_posix():audit.hash_file(p) for p in sorted(output.rglob('*')) if p.is_file()})
    return summary
