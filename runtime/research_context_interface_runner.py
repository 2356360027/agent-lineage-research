"""Fixed research-context first-action format check; never executes tools."""
from copy import deepcopy
from pathlib import Path
import time

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_journal import Journal
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime.research_context_interface import build_plan as build_draft, score_format, CONTEXT_IDS

MAX_CALLS, MAX_INPUT, MAX_OUTPUT, CEILING = 16, 4000, 512, 150000


def build_plan(requests):
    plan = build_draft(requests)
    plan['version'] = 'research-context-interface-v1'
    plan['executable_launcher_available'] = True
    return plan


def validate(plan):
    if type(plan) is not dict or type(plan.get('jobs')) is not list or len(plan['jobs']) != 16:
        raise ValueError('Sixteen planned slots required')
    requests = {j['context_id']:j['payload'] for j in plan['jobs'] if j['arm']=='original'}
    if audit.encoded(plan) != audit.encoded(build_plan(requests)):
        raise ValueError('Noncanonical plan or pairing')


def summarize(rows, plan):
    validate(plan)
    if len(rows)!=16 or [r['id'] for r in rows]!=[j['id'] for j in plan['jobs']]:
        raise ValueError('All sixteen slots in frozen order required')
    totals = {arm:dict(planned=8, observed=0, admitted=0, invalid=0, failed=0, not_started=0)
              for arm in ('original','separated')}
    for row,job in zip(rows,plan['jobs']):
        total = totals[job['arm']]
        if row['status']=='OBSERVED':
            total['observed'] += 1
            total['admitted'] += int(row['score']['admitted'])
            total['invalid'] += int(not row['score']['admitted'])
        elif row['status']=='FAILED': total['failed'] += 1
        elif row['status']=='NOT_STARTED': total['not_started'] += 1
        else: raise ValueError('Unknown disposition')
    complete = all(r['status']=='OBSERVED' for r in rows)
    paired = []
    if complete:
        by_id = {r['id']:r for r in rows}
        for ident in CONTEXT_IDS:
            a,b = (by_id[ident+'-'+arm]['score'] for arm in ('original','separated'))
            paired.append(dict(context_id=ident,
                admission_delta_separated_minus_original=int(b['admitted'])-int(a['admitted'])))
    return dict(complete=complete, totals=totals, paired_descriptive_differences=paired,
                inferential_statistics=False, C_type_drift_assessed=False,
                task_correctness_assessed=False, independent_task_pairs=2,
                limitation='Eight dependent contexts from two used development pairs; first-response formatting only.')


def evaluate_provider(raw, n, counter):
    if type(raw) is not dict or type(raw.get('usage')) is not dict:
        raise ValueError('Missing provider usage')
    usage = raw['usage']
    if (raw.get('model') != 'deepseek-flash' or type(usage.get('prompt_tokens')) is not int
            or usage['prompt_tokens'] != n or type(usage.get('completion_tokens')) is not int
            or not 0 <= usage['completion_tokens'] <= 512):
        raise ValueError('Model alias or token count mismatch')
    choices = raw.get('choices')
    if type(choices) is not list or len(choices)!=1 or type(choices[0]) is not dict:
        raise ValueError('Expected one choice')
    message = choices[0].get('message')
    if (type(message) is not dict or type(message.get('content')) is not str
            or any(message.get(k) for k in ('reasoning_content','tool_calls','function_call','refusal'))):
        raise ValueError('Unsupported response shape')
    if counter.text(message['content']) > 512: raise ValueError('Output token cap')
    return score_format(message['content'], choices[0].get('finish_reason'))


def run(plan, *, counter, transport_factory, output, mode, clock=time.monotonic):
    if mode not in ('SCRIPTED_NOT_MODEL', 'OFFICIAL_CONTEXT_INTERFACE'):
        raise ValueError('Explicit mode required; no implicit network authority')
    validate(plan)
    counts = [counter.chat(j['payload']['messages']) for j in plan['jobs']]
    if any(type(n) is not int or not 0 < n <= MAX_INPUT for n in counts):
        raise ValueError('Input cap; never truncate')
    if sum(n*2+MAX_OUTPUT*8 for n in counts) > CEILING:
        raise ValueError('Full planned conditional cost exceeds ceiling')
    root = Path(output); root.mkdir(parents=True, exist_ok=False)
    batch = Journal(root/'batch'); batch('plan', plan)
    guard = CostGuard(ceiling_micro_cny=CEILING, max_calls=MAX_CALLS, input_rate=2, output_rate=8)
    batch('settings', dict(mode=mode, maximum_seconds=600, max_output=512, max_input=MAX_INPUT,
        maximum_calls=MAX_CALLS, ceiling_micro_cny=CEILING, rates=[2,8], automatic_retry=False,
        SQL_executions=0, tool_executions=0))
    deadline = clock()+600
    rows = [dict(id=j['id'], status='NOT_STARTED', dispatches=0) for j in plan['jobs']]
    failure = None
    for job,n,row in zip(plan['jobs'],counts,rows):
        if failure: break
        log = Journal(root/job['id'])
        try:
            remaining = deadline-clock()
            if remaining <= 0: raise TimeoutError('Batch deadline')
            transport = transport_factory(job['id'],log)
            ticket = guard.reserve(n,512)
            log('action.pending', dict(payload=job['payload'], input_tokens=n, ticket=ticket,
                timeout_seconds=min(90,remaining), automatic_retry=False))
            row['dispatches'] = 1
            raw = transport(deepcopy(job['payload']),min(90,remaining))
            log('action.provider', dict(raw=raw))
            usage = raw.get('usage') if type(raw) is dict else None
            if type(usage) is not dict: raise ValueError('Missing usage; keep reserve')
            try: guard.settle(ticket,usage)
            finally: log('action.cost',guard.snapshot())
            if clock() >= deadline: raise TimeoutError('Late output preserved')
            row.update(status='OBSERVED', score=evaluate_provider(raw,n,counter))
        except (Exception,KeyboardInterrupt) as exc:
            failure = type(exc).__name__; guard.terminal = True
            row.update(status='FAILED', error_type=failure)
            log('failure',dict(error_type=failure,automatic_retry=False))
        finally:
            log('outcome',row); log.seal()
    result = dict(status=mode+('_STOPPED' if failure else '_FINISHED'), rows=rows,
        dispatch_attempts=sum(r['dispatches'] for r in rows), cost_guard=guard.snapshot(),
        stop_reason=failure, readout=summarize(rows,plan), tool_executions=0, SQL_executions=0,
        model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None)
    batch('summary',result); batch.seal(); audit.save_new(root/'summary.json',result)
    audit.save_new(root/'artifact_hashes.json',{p.relative_to(root).as_posix():audit.hash_file(p)
        for p in sorted(root.rglob('*')) if p.is_file()})
    return result

