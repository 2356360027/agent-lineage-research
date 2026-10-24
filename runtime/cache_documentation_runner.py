"""Fixed first-action calibration. No SQL, tools, retries, or training."""
from copy import deepcopy
from pathlib import Path
import time

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_journal import Journal
from runtime.cache_intent_staged_loop import parse_action
from runtime.deepseek_official_transport import validate_payload
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime.cache_documentation_inputs import HISTORIES

MAX_INPUT = 1200
MAX_OUTPUT = 512
MAX_CALLS = 12
CEILING = 100000


def validate(plan):
    jobs = plan['jobs']
    expected = {h+'-'+c for h in HISTORIES for c in ('original','documented')}
    if len(jobs)!=12 or {j['id'] for j in jobs}!=expected:
        raise ValueError('Only fixed twelve slots')
    for j in jobs:
        if j['id'] != j['history_id']+'-'+j['condition']:
            raise ValueError('Slot metadata mismatch')
        validate_payload(j['payload'])


def run(plan, *, counter, transport_factory, output, mode, clock=time.monotonic):
    if mode not in ('SCRIPTED_NOT_MODEL','OFFICIAL_DEVELOPMENT'):
        raise ValueError('Explicit mode required')
    validate(plan)
    counts = [counter.chat(j['payload']['messages']) for j in plan['jobs']]
    if any(type(n) is not int or not 0<n<=MAX_INPUT for n in counts):
        raise ValueError('Full input cap; never truncate')
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    batch = Journal(output/'batch')
    batch('plan', plan)
    batch('settings', dict(mode=mode, max_calls=12, deadline_seconds=600,
        max_input=MAX_INPUT, max_output=512, ceiling_micro_cny=CEILING,
        rates_micro_cny_per_token=[2,8], automatic_retry=False, tool_executions=0))
    guard = CostGuard(ceiling_micro_cny=CEILING, max_calls=12, input_rate=2, output_rate=8)
    deadline = clock()+600
    rows = [dict(id=j['id'], history_id=j['history_id'], condition=j['condition'],
                 status='NOT_STARTED', action=None, dispatches=0) for j in plan['jobs']]
    stop = None
    for job, n, row in zip(plan['jobs'], counts, rows):
        if stop: break
        log = Journal(output/job['id'])
        try:
            remaining = deadline-clock()
            if remaining<=0: raise TimeoutError('Batch deadline')
            transport = transport_factory(job['id'],log)
            ticket = guard.reserve(n,512)
            log('action.pending', dict(payload=job['payload'], payload_sha256=audit.digest(job['payload']),
                input_tokens=n, ticket=ticket, automatic_retry=False, timeout_seconds=min(90,remaining)))
            row['dispatches']=1
            started=clock()
            raw=transport(deepcopy(job['payload']),min(90,remaining))
            log('action.provider',dict(raw=raw,wall_seconds=clock()-started))
            usage = raw.get('usage') if type(raw) is dict else None
            if type(usage) is not dict: raise ValueError('Missing usage')
            try: guard.settle(ticket,usage)
            finally: log('action.cost',guard.snapshot())
            if clock()>=deadline: raise TimeoutError('Late response preserved')
            if (raw.get('model')!='deepseek-flash' or usage['prompt_tokens']!=n or
                    usage['completion_tokens']>512):
                raise ValueError('Provider alias/count mismatch')
            choices=raw.get('choices')
            if type(choices) is not list or len(choices)!=1: raise ValueError('Unexpected choices')
            message=choices[0].get('message')
            if (type(message) is not dict or type(message.get('content')) is not str or
                    any(message.get(k) for k in ('reasoning_content','tool_calls','function_call','refusal'))):
                raise ValueError('Unsupported provider output')
            if counter.text(message['content'])>512: raise ValueError('Output cap exceeded')
            try:
                if choices[0].get('finish_reason')!='stop': raise ValueError('Unfinished action')
                action=parse_action(message['content'])
            except (ValueError,TypeError):
                row['status']='INVALID_ACTION'
            else:
                row.update(status='ACTION_RECORDED',action=action['kind'])
        except (Exception,KeyboardInterrupt) as exc:
            stop=type(exc).__name__; guard.terminal=True
            row.update(status='FAILED',error_type=stop)
            log('failure',dict(error_type=stop,billing_uncertain=bool(guard.uncertain),automatic_retry=False))
        finally:
            log('outcome',row); log.seal()
    result=dict(status=mode+('_STOPPED' if stop else '_FINISHED'),rows=rows,stop_reason=stop,
        dispatch_attempts=sum(r['dispatches'] for r in rows),cost_guard=guard.snapshot(),
        decision_slots=12,independent_development_tasks=2,tool_executions=0,semantic_labels=0,
        confirmation_study=False,provider_invoice_verified=False,
        model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None)
    batch('summary',result); batch.seal()
    audit.save_new(output/'summary.json',result)
    audit.save_new(output/'artifact_hashes.json',{p.relative_to(output).as_posix():audit.hash_file(p)
        for p in sorted(output.rglob('*')) if p.is_file()})
    return result
