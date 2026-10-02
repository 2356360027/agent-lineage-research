"""Frozen 2x2x2 interface/communication diagnostic, 528 real calls.

12 fresh cells x complements x origin relations x (3 shared initials +
2 decision-rule instructions x 2 requested field orders x 2 peer conditions).
Raw outcomes are never corrected. Order noncompliance is a retained outcome.
MAP consistency of the unspecified arm is a convention, not an instruction
violation. No task-label accuracy, calibration or DICE utility claim.
"""
import argparse
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
from . import anchored_communication as prior
from . import origin_representation as origin
from . import lineage_utility as lu
from .dependency_probe import sources, check_server
from .e1 import canonical, digest, save
from .format_probe_v2 import Client, Audit
from .native import verify
from .probe_score import check, diagnostics
from .stage_diagnostic import metrics
from .scaffold_probe import stats

DESIGN=dict(protocol='decision-interface-v1',seed=159283071,cells=12,calls=528,
            temperature=0.,top_p=1.,max_tokens=512)
RULES=('unspecified','explicit')
ORDERS=('answer_first','probability_first')
PEERS=('no_peer','natural')
RELATIONS=('copy','independent')
TOL=.0001
RULE=('Use equal costs for the two types of decision error. Choose answer=1 if '
      'p_state_1>0.5; otherwise choose answer=0, including an exact tie at 0.5. '
      'p_state_1 still refers to the fixed event Y=1, not confidence in your chosen answer. ')
ORDER_TEXT={
    'answer_first':'Emit the JSON keys in this order: answer, p_state_1, citations. ',
    'probability_first':'Emit the JSON keys in this order: p_state_1, answer, citations. '}
GATE=dict(arm='explicit:probability_first:no_peer',posterior_correct_min=.95,
          map_consistent_min=.95,format_invalid_max=0,initial_invalid_max=0)
DECISION='If the fixed gate fails or any primary contrast is incomplete, discuss before further inference. Otherwise a new frozen follow-up is eligible, not automatically validated.'


def tasks(d):
    check(d==DESIGN,'Frozen design changed')
    cells=prior.tasks(d)
    excluded={tuple(sorted(x['reliabilities'])) for x in prior.tasks(prior.DESIGN)}
    check(all(tuple(sorted(x['reliabilities'])) not in excluded for x in cells),'Prior anchored cell collision')
    return cells


def initial_prompt(d,i,f,r,a):
    prompt,obs=prior.initial_prompt(d,i,f,r,a)
    prompt[0]['content']+=RULE
    return prompt,obs


def final_prompt(d,i,f,r,rule,order,peer,initials):
    check(rule in RULES and order in ORDERS and peer in PEERS,'Factor level')
    prompt,unique=prior.final_prompt(d,i,f,r,peer,initials)
    prompt[0]['content']+=(RULE if rule=='explicit' else '')+ORDER_TEXT[order]
    return prompt,unique


def evaluate(raw,arm,model,visible,order=None):
    out=lu.evaluate(raw,arm,model,visible)
    if order is not None:
        keys=('answer','p_state_1','citations') if order=='answer_first' else ('p_state_1','answer','citations')
        out['order_compliant']=bool(out['format_valid'] and tuple(json.loads(raw['choices'][0]['message']['content']))==keys)
    return out


def schedule(d,i,f,r):
    factors=list(itertools.product(RULES,ORDERS,PEERS))
    random.Random(f'{d["seed"]}:{i}:{f}:{r}:factors').shuffle(factors)
    return factors


def run_cell(client,d,i,directory=None):
    result=dict(cell=i,task=tasks(d)[i],cases={})
    for f,r in prior.case_order(d,i):
        key=f'i{i}-f{f}-{r}';initials=[];finals={}
        for a in range(3):
            prompt,obs=initial_prompt(d,i,f,r,a)
            client.evaluator=partial(evaluate,visible={o['id'] for o in obs})
            initials.append(client.call(f'{key}-initial-{a}',prompt,prior.call_seed(d,i,f,r,f'initial-{a}'),'final'))
        for rule,order,peer in schedule(d,i,f,r):
            prompt,_=final_prompt(d,i,f,r,rule,order,peer,initials)
            visible={o['id'] for o in json.loads(prompt[1]['content'])['messages']}
            client.evaluator=partial(evaluate,visible=visible,order=order)
            arm=f'{rule}:{order}:{peer}'
            finals[arm]=client.call(f'{key}-{rule}-{order}-{peer}',prompt,prior.call_seed(d,i,f,r,'final'),'final')
        result['cases'][key]=dict(flip=f,relation=r,initials=initials,finals=finals)
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result


def measured(out,obs):
    row=metrics(out,obs,'direct',TOL)
    if out['format_valid']:
        a=out['assessment']
        # Explicit tie-to-zero rule; no renormalization, answer overwrite or salvage.
        row['answer_probability_consistent']=int(a['answer']==int(a['p_state_1']>.5))
        row['posterior_correct']=int(row['mae']<=TOL)
    return row


def paired(rows, relation, factor, metric):
    index={(x['cell'],x['flip'],x['relation'],x['rule'],x['order'],x['peer']):x for x in rows}
    per_cell={}
    for i in range(12):
        deltas=[]
        for f in (0,1):
            for nuisance in (ORDERS if factor=='rule' else RULES):
                if factor=='rule':left=('explicit',nuisance);right=('unspecified',nuisance)
                else:left=(nuisance,'probability_first');right=(nuisance,'answer_first')
                a=index[i,f,relation,*left,'no_peer'];b=index[i,f,relation,*right,'no_peer']
                eligible=a['own_initial_valid'] and a['format_valid'] and b['format_valid']
                deltas.append(a[metric]-b[metric] if eligible else None)
        per_cell[str(i)]=mean(deltas) if all(v is not None for v in deltas) else None
    return contrast(per_cell,'primary_descriptive')


def contrast(per_cell,classification):
    complete=all(x is not None for x in per_cell.values())
    return dict(classification=classification,per_cell=per_cell,
                complete_cells=sum(x is not None for x in per_cell.values()),
                complete_mean=mean(per_cell.values()) if complete else None)


def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text());d,cfg=m['design'],m['config']
    tasks(d);check(m['engineering_gate']==GATE and m['decision_rule']==DECISION,'Frozen controls changed')
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Generation config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==528 and len(list(dr.glob('result-*.json')))==12,'Coverage mismatch')
    audit=Audit(dr,cfg);rows=[];initial_rows=[]
    for i in range(12):
        result=json.loads((dr/f'result-{i}.json').read_text())
        check(result==run_cell(audit,d,i),'Replay mismatch')
        for case in result['cases'].values():
            f,r,initials=case['flip'],case['relation'],case['initials']
            for a,out in enumerate(initials):
                obs=initial_prompt(d,i,f,r,a)[1]
                initial_rows.append(dict(cell=i,flip=f,relation=r,agent=a,**measured(out,obs)))
            for arm,out in case['finals'].items():
                rule,order,peer=arm.split(':')
                unique=final_prompt(d,i,f,r,rule,order,peer,initials)[1]
                rows.append(dict(cell=i,flip=f,relation=r,rule=rule,order=order,peer=peer,
                    own_initial_valid=initials[0]['format_valid'],initials_valid=all(o['format_valid'] for o in initials),**measured(out,unique)))
    groups={}
    for r,rule,order,peer in itertools.product(RELATIONS,RULES,ORDERS,PEERS):
        rs=[x for x in rows if (x['relation'],x['rule'],x['order'],x['peer'])==(r,rule,order,peer)]
        g=stats(rs)
        g.update(correct_posterior_all_calls=sum(x.get('posterior_correct',0) for x in rs),
                 map_consistent_all_calls=sum(x.get('answer_probability_consistent',0) for x in rs),
                 order_compliant_all_calls=sum(x.get('order_compliant',False) for x in rs))
        groups[f'{r}:{rule}:{order}:{peer}']=g
    contrasts={}
    for r,factor,metric in itertools.product(RELATIONS,('rule','order'),('mae','answer_probability_consistent')):
        contrasts[f'{r}:{factor}:{metric}']=paired(rows,r,factor,metric)
    for r in RELATIONS:
        for metric in ('mae','direction'):
            ds={}
            for i in range(12):
                diffs=[]
                for f in (0,1):
                    rs=[x for x in rows if (x['cell'],x['flip'],x['relation'],x['rule'],x['order'])==(i,f,r,'explicit','probability_first')]
                    a=next(x for x in rs if x['peer']=='natural');b=next(x for x in rs if x['peer']=='no_peer')
                    diffs.append(a[metric]-b[metric] if a['initials_valid'] and a['format_valid'] and b['format_valid'] else None)
                ds[str(i)]=mean(diffs) if all(v is not None for v in diffs) else None
            contrasts[f'{r}:natural_minus_no_peer:{metric}']=contrast(ds,'secondary_descriptive')
    baseline=[x for x in rows if (x['rule'],x['order'],x['peer'])==('explicit','probability_first','no_peer')]
    checks=dict(initials_valid=all(x['format_valid'] for x in initial_rows),baseline_valid=all(x['format_valid'] for x in baseline),
        posterior=sum(x.get('posterior_correct',0) for x in baseline)/48>=GATE['posterior_correct_min'],
        map_consistency=sum(x.get('answer_probability_consistent',0) for x in baseline)/48>=GATE['map_consistent_min'])
    complete=all(c['complete_mean'] is not None for c in contrasts.values() if c['classification']=='primary_descriptive')
    usage=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    summary=dict(status='EXPLORATORY_DECISION_INTERFACE',calls=len(audit.calls),independent_cells=12,
        initial_calls=len(initial_rows),initial_invalid=sum(not x['format_valid'] for x in initial_rows),
        initial_correct=sum(x.get('posterior_correct',0) for x in initial_rows),
        final_invalid=sum(not x['format_valid'] for x in rows),groups=groups,contrasts=contrasts,
        engineering_gate=dict(passed=all(checks.values()),checks=checks,baseline_calls=48,
            baseline_correct=sum(x.get('posterior_correct',0) for x in baseline),
            baseline_map_consistent=sum(x.get('answer_probability_consistent',0) for x in baseline)),
        decision='ELIGIBLE_FOR_NEW_FROZEN_FOLLOWUP_NOT_CONFIRMATION' if all(checks.values()) and complete else 'DISCUSS_BEFORE_MORE_INFERENCE',
        usage_complete=usage,prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if usage else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if usage else None,
        sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='12 independent synthetic cells; task-specific correct calculator and trusted origins. Field-order effects are intention-to-treat, including noncompliance. Rule and order main effects average other factor; no interaction or mediation claim. No DICE efficacy, significance, equivalence or natural prevalence claim. Original prior results unchanged.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return summary,dict(initial=initial_rows,final=rows),contrasts,hashes


def main():
    p=argparse.ArgumentParser();p.add_argument('--native-config');p.add_argument('--output',default='runtime/runs/decision-interface-001')
    for k in ('execute','score','audit-only'):p.add_argument('--'+k,action='store_true')
    a=p.parse_args();root=Path(a.output);check(sum((a.execute,a.score,a.audit_only))<=1,'Action')
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2));return
    if a.score:
        objects=summarize(root)
        for name,obj in zip(('summary','cell_metrics','paired_contrasts','audit_input_hashes'),objects):save(root/(name+'.json'),obj)
        print(json.dumps(objects[0],indent=2));return
    d=dict(DESIGN);tasks(d)
    print(json.dumps(dict(design=d,engineering_gate=GATE,decision_rule=DECISION,design_sha256=digest(d),execute=a.execute)),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text());check(cfg.get('deployment')=='native','Native only');cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')})
    root.mkdir(parents=True,exist_ok=False);dr=root/'shard-000';dr.mkdir();bundle=sources()
    save(dr/'manifest.json',dict(design=d,engineering_gate=GATE,decision_rule=DECISION,config=cfg,source_bundle=bundle,source_sha256=digest(bundle),created_unix=time.time(),python=sys.version))
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as response:metadata=json.load(response)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
    for i in range(12):run_cell(client,d,i,dr);print(f'Completed cell {i}; total=12',flush=True)
    print('Worker completed.',flush=True)


if __name__=='__main__':main()
