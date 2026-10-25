"""Fixed eight first responses; injected transport, no CLI or SQL executor."""
from copy import deepcopy
import json
from pathlib import Path
import time

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_journal import Journal
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime.native_action_interface import build_candidate
from runtime.native_first_response_adapter import NativeFirstAdapter
from runtime.research_context_interface import build_plan as text_plan

VERSION = 'native-context-interface-v1'
CEILING, MAX_CALLS, MAX_SECONDS = 100000, 8, 600


def build_plan(requests):
    source = text_plan(requests)
    jobs = [{'id':j['context_id'], 'source_payload':deepcopy(j['payload']),
             'payload':build_candidate(j['payload'])['payload']}
            for j in source['jobs'] if j['arm']=='original']
    plan = dict(version=VERSION, jobs=jobs, maximum_calls=MAX_CALLS,
                development_task_pairs=2, independent_confirmation=False,
                SQL_executions=0, tool_executions=0, inference_authorized=False)
    # Journal serialization sorts keys. Pin that exact nested schema order
    # BEFORE reference counting, so preparing and reloading count the same text.
    return json.loads(audit.encoded(plan))


def validate(plan):
    if type(plan) is not dict or type(plan.get('jobs')) is not list or len(plan['jobs']) != 8:
        raise ValueError('All eight contexts required')
    expected = build_plan({j['id']:j['source_payload'] for j in plan['jobs']})
    if audit.encoded(plan) != audit.encoded(expected):
        raise ValueError('Noncanonical native plan')


def summarize(rows, plan):
    validate(plan)
    if len(rows)!=8 or [r['id'] for r in rows]!=[j['id'] for j in plan['jobs']]:
        raise ValueError('Preserve all planned slots')
    totals = dict(planned=8, observed=0, admitted=0, invalid=0, failed=0, not_started=0)
    for row in rows:
        if row['status']=='OBSERVED':
            totals['observed']+=1
            totals['admitted']+=int(row['score']['admitted'])
            totals['invalid']+=int(not row['score']['admitted'])
        elif row['status']=='FAILED': totals['failed']+=1
        elif row['status']=='NOT_STARTED': totals['not_started']+=1
        else: raise ValueError('Unknown disposition')
    decision = ('STOP_INFRASTRUCTURE_REVIEW' if totals['failed'] or totals['not_started'] else
                'STOP_INTERFACE_REVIEW' if totals['invalid'] else 'INTERFACE_CHECK_PASSED_ONLY')
    return dict(totals=totals, decision=decision, task_correctness_assessed=False,
                C_type_drift_assessed=False, inferential_statistics=False,
                further_paid_calls_authorized=False,
                limitation='Two exposed development task pairs; no independent task confirmation or novel method evidence.')


def run(plan, *, counter, transport_factory, output, mode, clock=time.monotonic):
    if mode not in ('SCRIPTED_NOT_MODEL', 'OFFICIAL_NATIVE_CONTEXT'):
        raise ValueError('Explicit mode required; caller owns separate authorization')
    validate(plan)
    counts = [counter.inspect(j['payload'])['reference_input_tokens'] for j in plan['jobs']]
    if any(type(n) is not int or not 0<n<=8192 for n in counts):
        raise ValueError('Input cap; never truncate')
    conditional = sum(n*2+512*8 for n in counts)
    if conditional>CEILING:
        raise ValueError('Conditional planned cost exceeds client ceiling')
    root = Path(output)
    root.mkdir(parents=True,exist_ok=False)
    batch = Journal(root/'batch'); batch('plan',plan)
    batch('settings',dict(mode=mode, maximum_calls=8, maximum_seconds=600,
                         ceiling_micro_cny=CEILING, conditional_micro_cny=conditional,
                         rates_micro_cny_per_token=[2,8], automatic_retry=False,
                         provider_spending_cap_installed=False, SQL_executions=0,
                         tool_executions=0, input_tokens=counts))
    guard=CostGuard(ceiling_micro_cny=CEILING,max_calls=8,input_rate=2,output_rate=8)
    deadline=clock()+600
    rows=[dict(id=j['id'],status='NOT_STARTED') for j in plan['jobs']]
    failure=None
    for job,row in zip(plan['jobs'],rows):
        if failure: break
        log=Journal(root/job['id'])
        try:
            adapter=NativeFirstAdapter(counter=counter,transport=transport_factory(job['id'],log),
                                       save=log,guard=guard,deadline=deadline,clock=clock)
            row.update(status='OBSERVED',score=adapter(deepcopy(job['payload'])))
        except (Exception,KeyboardInterrupt) as exc:
            failure=type(exc).__name__; guard.terminal=True
            row.update(status='FAILED',error_type=failure)
        finally:
            log('outcome',row); log.seal()
    summary=dict(status=mode+('_STOPPED' if failure else '_FINISHED'),rows=rows,
                 cost_guard=guard.snapshot(),dispatch_reservations=guard.calls,
                 stop_reason=failure,readout=summarize(rows,plan),SQL_executions=0,
                 tool_executions=0,actual_model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None)
    batch('summary',summary); batch.seal()
    audit.save_new(root/'summary.json',summary)
    audit.save_new(root/'artifact_hashes.json',{p.relative_to(root).as_posix():audit.hash_file(p)
                   for p in sorted(root.rglob('*')) if p.is_file()})
    return summary
