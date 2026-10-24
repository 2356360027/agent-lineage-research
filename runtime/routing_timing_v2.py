"""Frozen natural-report broadcast routing/timing component pilot.

12 cells x 2 complements x 2 origin relations x 46 calls = 2208.
3 shared initials; five methods x (3 S1 + 3 ST + one common adjudicator);
single-union + adjudicator; private-initial ensemble adjudicator;
three independent union calls + adjudicator; unassisted union probe.
Natural reports are actual frozen S0 answers, NOT regenerated S1 verdicts.
Task-specific correct calculator and trusted origins are hybrid reference
components. The strong ledger baseline is conventional recipient root-ID
deduplication. This is not a complete DICE or natural-task evaluation.
"""
import argparse
import copy
from functools import partial
import hashlib
import itertools
import json
import random
import sys
import time
import urllib.request
from pathlib import Path
from statistics import mean
from . import interface_replication as holdout
from . import decision_interface as interface
from .dependency_probe import sources,check_server
from .e1 import canonical,digest,save
from .format_probe_v2 import Client,Audit
from .native import verify
from .probe_score import check,diagnostics,interval
from .scaffold_probe import stats

DESIGN=dict(protocol='routing-timing-v2',seed=181405097,cells=12,calls=2208,
            temperature=0.,top_p=1.,max_tokens=512,bootstrap_seed=98231,bootstrap_resamples=2000)
METHODS=('raw_open','raw_delay','dedup_open','dedup_delay','dedup_never')
CONTROLS=('single_union','private_ensemble','union_ensemble','unassisted_union')
RELATIONS=interface.RELATIONS
AGENTS=('A','B','C')
GATE=dict(union_correct_min=.95,union_map_consistent_min=.95,all_format_invalid_max=0,
          meaningful_mae_improvement=.01,max_direction_loss=.02)
DECISION=('Before more inference, discuss if validity/readiness fails, any primary contrast is incomplete, '
          'or dedup_delay does not improve mean system posterior MAE over dedup_open by at least .01 '
          'while losing no more than .02 oracle-MAP direction rate. This is a developmental '
          'go/no-go rule, not a significance test, equivalence claim or publication criterion.')
SOCIAL=('Each agent initially assessed one private message. You now see the permitted evidence '
        'in messages and its registered origins. Peer entries, when present, contain the other '
        'agents\' recorded initial answers. They carry no observations beyond the evidence now '
        'visible to you, may be wrong, and are advisory. Your previous assessment may also be wrong. '
        'An unavailable assessment means no valid output was recorded. Duplicate receipts, if '
        'present, refer to prior measurements and add no evidence. ')
UNTYPED_FINAL=('Return only JSON with exactly answer (integer 0 or 1), p_state_1 (number from 0 to 1, '
    'P(Y=1 given all unique visible originating measurements), NOT confidence in the selected '
    'answer), and citations (list of visible message IDs). Use at least six decimal places '
    'for calculated non-integers. No additional text. ')
ADJUDICATOR=('Combine only the supplied terminal assessments; no raw evidence or external information '
    'is available. Use an equal-weight arithmetic mean of the available p_state_1 values, each '
    'of which refers to the fixed event Y=1. This is a fixed opinion-pool reference, not a '
    'claim that agent reports are independent evidence. Do not multiply their probabilities. '
    'Citations may only come from their supplied citations. Return only JSON with exactly '
    'answer (integer 0 or 1), p_state_1 (number from 0 to 1), citations (list of supplied IDs). '
    'If no valid assessment is available, return the empty JSON object; this will be recorded '
    'as unavailable, not repaired. Use at least six decimal places for calculated non-integers. ')


def tasks(d):
    check(d==DESIGN,'Frozen design changed')
    cells=interface.prior.tasks(d)
    excluded={tuple(sorted(c['reliabilities'])) for c in holdout.tasks(holdout.DESIGN)+interface.tasks(interface.DESIGN)+interface.prior.tasks(interface.prior.DESIGN)}
    check(all(tuple(sorted(c['reliabilities'])) not in excluded for c in cells),'Prior task collision')
    return cells


def world(d,i,f,r):
    return interface.origin.world(d,i,f,r)


def delivery(d,i,f,r,agent,stage,route):
    check(route in ('raw','dedup') and stage in (1,2),'Routing/stage')
    obs,roots=world(d,i,f,r)
    ordered=[obs[agent]]+[o for a,o in enumerate(obs) if a!=agent]
    history=copy.deepcopy(ordered);registry=dict(roots)
    if stage==2:
        for o in ordered[1:]:
            alias=dict(o,id=o['id']+'_relay')
            history.append(alias);registry[alias['id']]=roots[o['id']]
    first={};receipts=[]
    for o in history:
        root=registry[o['id']]
        if root in first:
            old=first[root]
            check((o['value'],o['effective_reliability'])==(old['value'],old['effective_reliability']),'Conflicting registered measurement')
            receipts.append(dict(message_id=o['id'],prior_message_id=old['id'],origin_id=root))
        else:first[root]=o
    unique=list(first.values())
    visible=history if route=='raw' else unique
    payload=interface.origin.encode(visible,registry,'map')
    if route=='dedup':payload['duplicate_receipts']=receipts
    payload['external_calculator']=interface.lu.tool_for(unique)
    return payload,unique


def initial_prompt(d,i,f,r,a):return interface.initial_prompt(d,i,f,r,a)


def stage_prompt(d,i,f,r,method,a,stage,previous,initials):
    route,timing=method.split('_');payload,unique=delivery(d,i,f,r,a,stage,route)
    payload['your_previous_assessment']=interface.prior.own_artifact(previous)
    shown=timing=='open' or (timing=='delay' and stage==2)
    payload['peer_assessments']=[interface.prior.peer_artifact(initials[b],AGENTS[b]) for b in range(3) if b!=a] if shown else []
    system=interface.origin.STRUCTURE+interface.origin.GENERATIVE+SOCIAL+interface.origin.FINAL+interface.RULE+interface.ORDER_TEXT['answer_first']
    return [dict(role='system',content=system),dict(role='user',content=canonical(payload))],unique


def union_prompt(d,i,f,r,assisted=True):
    obs,roots=world(d,i,f,r);unique,_=interface.lu.route(obs,roots,'verified_root')
    payload=interface.origin.encode(unique,roots,'map')
    if assisted:payload['external_calculator']=interface.lu.tool_for(unique)
    system=interface.origin.STRUCTURE+interface.origin.GENERATIVE+(interface.origin.FINAL if assisted else UNTYPED_FINAL)+interface.RULE+interface.ORDER_TEXT['answer_first']
    return [dict(role='system',content=system),dict(role='user',content=canonical(payload))],unique


def adjudicator_prompt(outputs):
    visible={c for out in outputs if out['format_valid'] for c in out['assessment']['citations']}
    payload=dict(terminal_assessments=[dict(agent=str(a),**interface.prior.own_artifact(out)) for a,out in enumerate(outputs)])
    return [dict(role='system',content=ADJUDICATOR+interface.RULE+interface.ORDER_TEXT['answer_first']),dict(role='user',content=canonical(payload))],visible


def call(client,key,prompt,seed,visible):
    client.evaluator=partial(interface.evaluate,visible=set(visible),order='answer_first')
    return client.call(key,prompt,seed,'final')


def run_cell(client,d,i,directory=None):
    result=dict(cell=i,task=tasks(d)[i],cases={})
    for f,r in interface.prior.case_order(d,i):
        key=f'i{i}-f{f}-{r}';initials=[];methods={};controls={}
        for a in range(3):
            p,obs=initial_prompt(d,i,f,r,a)
            initials.append(call(client,f'{key}-initial-{a}',p,interface.prior.call_seed(d,i,f,r,f'initial-{a}'),[o['id'] for o in obs]))
        order=list(METHODS)+list(CONTROLS);random.Random(f'{d["seed"]}:{i}:{f}:{r}:jobs').shuffle(order)
        for job in order:
            if job in METHODS:
                previous=list(initials);stages=[]
                for stage in (1,2):
                    updated=[]
                    for a in range(3):
                        p,_=stage_prompt(d,i,f,r,job,a,stage,previous[a],initials)
                        updated.append(call(client,f'{key}-{job}-s{stage}-a{a}',p,interface.prior.call_seed(d,i,f,r,f's{stage}-a{a}'),[o['id'] for o in json.loads(p[1]['content'])['messages']]))
                    stages.append(updated);previous=updated
                p,visible=adjudicator_prompt(previous)
                out=call(client,f'{key}-{job}-adjudicator',p,interface.prior.call_seed(d,i,f,r,'adjudicator'),visible)
                methods[job]=dict(stages=stages,system=out)
            else:
                outputs=[]
                if job=='private_ensemble':outputs=list(initials)
                else:
                    n=3 if job=='union_ensemble' else 1
                    for a in range(n):
                        p,obs=union_prompt(d,i,f,r,job!='unassisted_union')
                        outputs.append(call(client,f'{key}-{job}-a{a}',p,interface.prior.call_seed(d,i,f,r,f'{job}-a{a}'),[o['id'] for o in obs]))
                if job=='unassisted_union':out=outputs[0]
                else:
                    p,visible=adjudicator_prompt(outputs)
                    out=call(client,f'{key}-{job}-adjudicator',p,interface.prior.call_seed(d,i,f,r,'adjudicator'),visible)
                controls[job]=dict(assessments=outputs,system=out)
        result['cases'][key]=dict(flip=f,relation=r,initials=initials,methods=methods,controls=controls,job_order=order)
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result


def measured(out,obs):
    row=interface.measured(out,obs)
    if out['format_valid']:
        p=out['assessment']['p_state_1'];q=row['oracle_p']
        row['excess_expected_brier']=(p-q)**2
    return row


def pair_contrasts(rows,d):
    index={(x['cell'],x['flip'],x['relation'],x['job']):x for x in rows}
    specs={
        'delay_with_dedup':(('dedup_delay',1),('dedup_open',-1)),
        'dedup_with_open':(('dedup_open',1),('raw_open',-1)),
        'routing_timing_interaction':(('dedup_delay',1),('dedup_open',-1),('raw_delay',-1),('raw_open',1)),
        'delay_vs_never':(('dedup_delay',1),('dedup_never',-1)),
        'delay_vs_union_ensemble':(('dedup_delay',1),('union_ensemble',-1)),
        'delay_vs_single_union':(('dedup_delay',1),('single_union',-1)),
        'delay_vs_private_ensemble':(('dedup_delay',1),('private_ensemble',-1))}
    contrasts={}
    for name,terms in specs.items():
        for metric in ('mae','direction'):
            values={}
            for i in range(12):
                parts=[]
                for f,r in itertools.product((0,1),RELATIONS):
                    rs=[(index[i,f,r,j],weight) for j,weight in terms]
                    parts.append(sum(x[metric]*weight for x,weight in rs) if all(x['pipeline_valid'] and x['format_valid'] for x,_ in rs) else None)
                values[str(i)]=mean(parts) if all(v is not None for v in parts) else None
            complete=all(v is not None for v in values.values())
            contrasts[f'{name}:{metric}']=dict(classification='primary_descriptive' if name in ('delay_with_dedup','dedup_with_open','routing_timing_interaction') else 'secondary_descriptive',
                per_cell=values,complete_cells=sum(v is not None for v in values.values()),complete_mean=mean(values.values()) if complete else None,
                descriptive_interval=interval(list(values.values()),d['bootstrap_seed'],d['bootstrap_resamples']) if complete else None)
    return contrasts


def summarize(root):
    dr=root/'shard-000';manifest=json.loads((dr/'manifest.json').read_text());d,cfg=manifest['design'],manifest['config']
    tasks(d);check(manifest['gate']==GATE and manifest['decision_rule']==DECISION,'Frozen controls')
    check(digest(sources())==manifest['source_sha256']==digest(manifest['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Generation mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==2208 and len(list(dr.glob('result-*.json')))==12,'Coverage mismatch')
    audit=Audit(dr,cfg);system=[];agent_rows=[];initial_rows=[];reference=[];pooling=[]
    for i in range(12):
        result=json.loads((dr/f'result-{i}.json').read_text());check(result==run_cell(audit,d,i),'Replay mismatch')
        for case in result['cases'].values():
            f,r=case['flip'],case['relation'];obs=union_prompt(d,i,f,r)[1]
            initials=case['initials']
            for a,x in enumerate(initials):initial_rows.append(dict(cell=i,flip=f,relation=r,agent=a,**measured(x,initial_prompt(d,i,f,r,a)[1])))
            for job,detail in {**case['methods'],**case['controls']}.items():
                if job in METHODS:
                    outcomes=initials+sum(detail['stages'],[]);terminals=detail['stages'][-1]
                    for stage in (1,2):
                        for a,x in enumerate(detail['stages'][stage-1]):
                            before=measured(initials[a],obs)
                            row=dict(cell=i,flip=f,relation=r,job=job,stage=stage,agent=a,**measured(x,obs))
                            if x['format_valid'] and initials[a]['format_valid']:
                                row['initial_union_map_correct_to_wrong']=int(before['direction']==1 and row['direction']==0)
                                row['initial_union_map_wrong_to_correct']=int(before['direction']==0 and row['direction']==1)
                            agent_rows.append(row)
                else:outcomes=detail['assessments'];terminals=outcomes
                system.append(dict(cell=i,flip=f,relation=r,job=job,pipeline_valid=all(x['format_valid'] for x in outcomes),**measured(detail['system'],obs)))
                if job=='single_union':reference.append(measured(outcomes[0],obs))
                if job!='unassisted_union':
                    valid=[x['assessment']['p_state_1'] for x in terminals if x['format_valid']]
                    pooling.append(dict(cell=i,flip=f,relation=r,job=job,all_terminals_valid=len(valid)==len(terminals),
                        mean_reported_probability=mean(valid) if valid else None,
                        adjudicator_pool_error=abs(detail['system']['assessment']['p_state_1']-mean(valid)) if valid and detail['system']['format_valid'] else None))
    contrasts=pair_contrasts(system,d)
    groups={}
    for r,job in itertools.product(RELATIONS,METHODS+CONTROLS):
        rs=[x for x in system if x['relation']==r and x['job']==job];g=stats(rs)
        g.update(pipeline_valid=sum(x['pipeline_valid'] for x in rs),posterior_correct=sum(x.get('posterior_correct',0) for x in rs),map_consistent=sum(x.get('answer_probability_consistent',0) for x in rs))
        groups[f'{r}:{job}']=g
    invalid=sum(c['status']!='ok' for c in audit.calls)
    readiness=dict(all_format_valid=invalid==0,union_correct=sum(x.get('posterior_correct',0) for x in reference)/48>=GATE['union_correct_min'],
                   union_map_consistent=sum(x.get('answer_probability_consistent',0) for x in reference)/48>=GATE['union_map_consistent_min'])
    complete=all(c['complete_mean'] is not None for c in contrasts.values() if c['classification']=='primary_descriptive')
    gain=contrasts['delay_with_dedup:mae']['complete_mean'];loss=contrasts['delay_with_dedup:direction']['complete_mean']
    promising=gain is not None and loss is not None and gain<=-GATE['meaningful_mae_improvement'] and loss>=-GATE['max_direction_loss']
    usage=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    s=dict(status='EXPLORATORY_ROUTING_TIMING_COMPONENT_NOT_DICE_CONFIRMATION',calls=len(audit.calls),independent_cells=12,invalid_calls=invalid,
        readiness=readiness,reference_calls=48,reference_correct=sum(x.get('posterior_correct',0) for x in reference),reference_map_consistent=sum(x.get('answer_probability_consistent',0) for x in reference),
        primary_complete=complete,developmental_gain_rule_passed=promising,groups=groups,contrasts=contrasts,
        decision='ELIGIBLE_FOR_SEPARATELY_FROZEN_VALIDATION_NOT_CONFIRMATION' if all(readiness.values()) and complete and promising else 'DISCUSS_BEFORE_MORE_INFERENCE',
        usage_complete=usage,prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if usage else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if usage else None,sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        resource_contract=dict(logical_calls_per_method=10,single_union_calls=2,private_ensemble_calls=4,union_ensemble_calls=4,unassisted_union_calls=1,
             note='Shared S0 calls reused across methods but charged logically to every method. Utility controls are lower-call references, not equal-realized-compute baselines. Per-call raw token/time records retained.'),
        warning='Known synthetic generative law, trusted registry, exact calculator hybrid reference; frozen S0 broadcasts, not adaptive debate. Strong recipient dedup equals ledger here. Twelve independent cells; descriptive intervals, no significance/equivalence claim. No sampled labels: oracle-MAP direction and posterior error are not observed label accuracy or empirical Brier. No native published-baseline replication or natural-task claim.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return s,dict(system=system,agents=agent_rows,initials=initial_rows,pooling=pooling),contrasts,hashes


def main():
    p=argparse.ArgumentParser();p.add_argument('--native-config');p.add_argument('--output',default='runtime/runs/routing-timing-002')
    for k in ('execute','score','audit-only'):p.add_argument('--'+k,action='store_true')
    a=p.parse_args();root=Path(a.output);check(sum((a.execute,a.score,a.audit_only))<=1,'Action')
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2));return
    if a.score:
        objects=summarize(root)
        for name,obj in zip(('summary','cell_metrics','paired_contrasts','audit_input_hashes'),objects):save(root/(name+'.json'),obj)
        print(json.dumps(objects[0],indent=2));return
    d=dict(DESIGN);tasks(d);print(json.dumps(dict(design=d,gate=GATE,decision_rule=DECISION,design_sha256=digest(d),execute=a.execute)),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text());check(cfg.get('deployment')=='native','Native only');cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')})
    root.mkdir(parents=True,exist_ok=False);dr=root/'shard-000';dr.mkdir();bundle=sources()
    save(dr/'manifest.json',dict(design=d,gate=GATE,decision_rule=DECISION,config=cfg,source_bundle=bundle,source_sha256=digest(bundle),created_unix=time.time(),python=sys.version))
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as response:metadata=json.load(response)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
    for i in range(12):run_cell(client,d,i,dr);print(f'Completed cell {i}; total=12',flush=True)
    print('Worker completed.',flush=True)


if __name__=='__main__':main()
