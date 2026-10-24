"""Eight first-action development slots. No SQL, tools, credentials or network.

Injected transports enable scripted tests; a separate authorized launcher must
freeze source/input and supply the official transport. Not a C-drift estimator.
"""
from copy import deepcopy
import json
from pathlib import Path
import time

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_journal import Journal
from runtime.cache_intent_prompts import CONTRACT_FIELDS
from runtime.cache_intent_staged_loop import ACTION_INSTRUCTION, parse_action
from runtime.deepseek_trajectory_adapter import make_payload, CostGuard, MODEL

SEED = 'workflow-availability-development-20261024-v1'
MAX_INPUT, MAX_OUTPUT, MAX_CALLS = 8192, 512, 12
INPUT_RATE, OUTPUT_RATE, CEILING = 2, 8, 300000
MAX_CONDITIONAL_MICRO_CNY = MAX_CALLS*(MAX_INPUT*INPUT_RATE+MAX_OUTPUT*OUTPUT_RATE)


def build_plan(old_plan):
    jobs=[]
    for hid in ('h200a','h234a'):
        selected=[j for j in old_plan['jobs'] if j['history_id']==hid and j['read_price']==1]
        if len(selected)!=1: raise ValueError('Missing direct control')
        original=selected[0]['envelope']['messages']
        system=original[0]['content']
        if system.count('Stage 1:')!=1: raise ValueError('Unexpected staged instruction')
        for available in (False,True):
            data=json.loads(original[1]['content'])
            data['context']['cache']['materialized_available']=available
            if data['context']['cache']['read_cost']!=1: raise ValueError('Wrong fixed price')
            for workflow in ('contract_first','action_first'):
                jobs.append(dict(id=f'{hid}-{workflow}-available{int(available)}',
                    history=hid,available=available,workflow=workflow,
                    packet=deepcopy(data),contract_system=system,
                    action_system=system.split('Stage 1:')[0]+'Do not invent unspecified requirements.\n'))
    jobs.sort(key=lambda j:audit.digest(SEED+j['id']))
    return dict(version='first-action-workflow-v1',seed=SEED,jobs=jobs,
                independent_development_tasks=2,maximum_calls=12,tools_executed=0)


def payload(job, contract=None):
    text=audit.encoded(job['packet']).decode('utf-8')
    if job['workflow']=='contract_first':
        messages=[dict(role='system',content=job['contract_system']),dict(role='user',content=text)]
        if contract is not None:
            # Raw generated contract is preserved, never substituted by a gold answer.
            parse_contract(contract)
            messages += [dict(role='assistant',content=contract),dict(role='user',content=ACTION_INSTRUCTION)]
        return make_payload(messages)
    if job['workflow']!='action_first' or contract is not None: raise ValueError('Wrong phase')
    result=make_payload([dict(role='system',content=job['action_system']),
                         dict(role='user',content=text+'\n\n'+ACTION_INSTRUCTION)])
    # Avoid reintroducing Stage-1 schema advice into the unelicited workflow.
    result['messages'][0]['content']=job['action_system']
    return result


def parse_contract(text):
    def unique(items):
        result={}
        for k,v in items:
            if k in result: raise ValueError('Duplicate contract key')
            result[k]=v
        return result
    value=json.loads(text,object_pairs_hook=unique)
    if (type(value) is not dict or set(value)!=set(CONTRACT_FIELDS)
            or any(type(v) is not str or not v.strip() for v in value.values())):
        raise ValueError('Invalid contract format')
    return value


def validate_plan(plan,old_plan):
    if plan!=build_plan(old_plan): raise ValueError('Plan is not the frozen factorial construction')


def run(plan,*,counter,transport_factory,output,mode,clock=time.monotonic):
    if mode not in ('SCRIPTED_NOT_MODEL','OFFICIAL_DEVELOPMENT'): raise ValueError('Explicit mode required')
    if len(plan['jobs'])!=8: raise ValueError('Expected eight slots')
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    batch=Journal(output/'batch'); batch('plan',plan)
    guard=CostGuard(ceiling_micro_cny=CEILING,max_calls=MAX_CALLS,input_rate=INPUT_RATE,output_rate=OUTPUT_RATE)
    batch('settings',dict(mode=mode,deadline_seconds=600,max_input=MAX_INPUT,max_output=512,
        max_calls=12,ceiling_micro_cny=CEILING,conditional_upper_micro_cny=MAX_CONDITIONAL_MICRO_CNY,
        rates_micro_cny_per_token=[INPUT_RATE,OUTPUT_RATE],automatic_retry=False))
    deadline=clock()+600
    rows=[dict(id=j['id'],history=j['history'],workflow=j['workflow'],available=j['available'],
               status='NOT_STARTED',action=None,dispatches=0) for j in plan['jobs']]
    stop=None
    for job,row in zip(plan['jobs'],rows):
        if stop: break
        log=Journal(output/job['id'])
        transport=transport_factory(job['id'],log)
        def call(p, phase):
            n=counter.chat(p['messages']); remaining=deadline-clock()
            if type(n) is not int or not 0<n<=MAX_INPUT: raise ValueError('Input cap; never truncate')
            if remaining<=0: raise TimeoutError('Batch deadline')
            ticket=guard.reserve(n,MAX_OUTPUT)
            log(phase+'.pending',dict(payload=p,payload_sha256=audit.digest(p),input_tokens=n,
                ticket=ticket,automatic_retry=False,timeout_seconds=min(90,remaining)))
            row['dispatches']+=1
            started=clock()
            raw=transport(deepcopy(p),min(90,remaining))
            log(phase+'.provider',dict(raw=raw,wall_seconds=clock()-started))
            usage=raw.get('usage') if type(raw) is dict else None
            if type(usage) is not dict: raise ValueError('Missing provider usage')
            try: guard.settle(ticket,usage)
            finally: log(phase+'.cost',guard.snapshot())
            if clock()>=deadline: raise TimeoutError('Late response preserved')
            if (raw.get('model')!=MODEL or usage['prompt_tokens']!=n or usage['completion_tokens']>512):
                raise ValueError('Provider alias or tokenizer mismatch')
            choices=raw.get('choices')
            if type(choices) is not list or len(choices)!=1: raise ValueError('Expected one response')
            message=choices[0].get('message')
            if (type(message) is not dict or type(message.get('content')) is not str or
                    any(message.get(k) for k in ('reasoning_content','tool_calls','function_call','refusal'))):
                raise ValueError('Unsupported provider output')
            if counter.text(message['content'])>512: raise ValueError('Output reference cap exceeded')
            return message['content'],choices[0].get('finish_reason')
        try:
            contract=None
            if job['workflow']=='contract_first':
                contract,finish=call(payload(job),'contract')
                try:
                    if finish!='stop': raise ValueError('Unfinished contract')
                    parse_contract(contract)
                except (ValueError,TypeError):
                    row['status']='INVALID_CONTRACT'
                    continue
            text,finish=call(payload(job,contract),'action')
            try:
                if finish!='stop': raise ValueError('Unfinished action')
                action=parse_action(text)
            except (ValueError,TypeError):
                row['status']='INVALID_ACTION'
            else:
                row.update(status='ACTION_RECORDED',action=action['kind'],
                    unavailable_cache_requested=action['kind']=='read_cache' and not job['available'])
        except (Exception,KeyboardInterrupt) as exc:
            stop=type(exc).__name__; guard.terminal=True
            row.update(status='FAILED',error_type=stop)
            log('failure',dict(error_type=stop,billing_uncertain=bool(guard.uncertain),automatic_retry=False))
        finally:
            log('outcome',row); log.seal()
    summary=dict(status=mode+('_STOPPED' if stop else '_FINISHED'),rows=rows,stop_reason=stop,
        decision_slots=8,independent_development_tasks=2,dispatch_attempts=sum(r['dispatches'] for r in rows),
        cost_guard=guard.snapshot(),tool_executions=0,semantic_labels=0,confirmation_study=False,
        provider_invoice_verified=False,model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None)
    batch('summary',summary); batch.seal()
    audit.save_new(output/'summary.json',summary)
    audit.save_new(output/'artifact_hashes.json',{p.relative_to(output).as_posix():audit.hash_file(p)
        for p in sorted(output.rglob('*')) if p.is_file()})
    return summary
