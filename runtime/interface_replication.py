"""Prospective held-out interface replication, not DICE confirmation.

24 fresh cells x 2 complements x 2 origin relations x (3 initials +
2 output orders x 2 peer conditions) = 672 calls. Explicit decision rule
and all prompt wording unchanged from decision-interface-v1. Candidate
answer-first was selected on the prior exploratory batch, which is never
pooled. No retries, missing-output salvage or post-hoc candidate swapping.
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
from . import decision_interface as previous
from .dependency_probe import sources,check_server
from .e1 import canonical,digest,save
from .format_probe_v2 import Client,Audit
from .native import verify
from .probe_score import check,diagnostics,interval
from .scaffold_probe import stats

DESIGN=dict(protocol='interface-replication-v1',seed=170394083,cells=24,calls=672,
            temperature=0.,top_p=1.,max_tokens=512,bootstrap_resamples=2000,bootstrap_seed=81721)
ORDERS=previous.ORDERS
PEERS=previous.PEERS
RELATIONS=previous.RELATIONS
GATE=dict(candidate='answer_first:no_peer',posterior_correct_min=.95,map_consistent_min=.95,
          baseline_invalid_max=0,initial_invalid_max=0)
DECISION='Discuss before further inference if candidate readiness fails or a primary order contrast is incomplete. Passing allows further frozen communication evaluation, not DICE efficacy, superiority, equivalence or publication claims.'


def block(d,i):
    check(0<=i<24,'Cell range')
    return dict(d,seed=d['seed']+i//12),i%12


def tasks(d):
    check(d==DESIGN,'Frozen design changed')
    cells=[]
    for b in range(2):cells.extend(previous.prior.tasks(dict(d,seed=d['seed']+b)))
    excluded={tuple(sorted(x['reliabilities'])) for x in previous.tasks(previous.DESIGN)+previous.prior.tasks(previous.prior.DESIGN)}
    pairs=[tuple(sorted(x['reliabilities'])) for x in cells]
    check(len(pairs)==24 and len(set(pairs))==24 and not(set(pairs)&excluded),'Task collision')
    return cells


def initial_prompt(d,i,f,r,a):
    bd,bi=block(d,i)
    return previous.initial_prompt(bd,bi,f,r,a)


def final_prompt(d,i,f,r,order,peer,initials):
    bd,bi=block(d,i)
    return previous.final_prompt(bd,bi,f,r,'explicit',order,peer,initials)


def schedule(d,i,f,r):
    arms=list(itertools.product(ORDERS,PEERS))
    random.Random(f'{d["seed"]}:{i}:{f}:{r}:arms').shuffle(arms)
    return arms


def run_cell(client,d,i,directory=None):
    result=dict(cell=i,task=tasks(d)[i],cases={})
    for f,r in previous.prior.case_order(d,i):
        key=f'i{i}-f{f}-{r}';initials=[];finals={}
        for a in range(3):
            p,obs=initial_prompt(d,i,f,r,a)
            client.evaluator=partial(previous.evaluate,visible={o['id'] for o in obs})
            initials.append(client.call(f'{key}-initial-{a}',p,previous.prior.call_seed(d,i,f,r,f'initial-{a}'),'final'))
        for order,peer in schedule(d,i,f,r):
            p,_=final_prompt(d,i,f,r,order,peer,initials)
            client.evaluator=partial(previous.evaluate,visible={o['id'] for o in json.loads(p[1]['content'])['messages']},order=order)
            finals[f'{order}:{peer}']=client.call(f'{key}-{order}-{peer}',p,previous.prior.call_seed(d,i,f,r,'final'),'final')
        result['cases'][key]=dict(flip=f,relation=r,initials=initials,finals=finals)
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result


def contrasts_for(rows,d):
    index={(x['cell'],x['flip'],x['relation'],x['order'],x['peer']):x for x in rows};contrasts={}
    for r,kind,metric in itertools.product(RELATIONS,('candidate_minus_probability_first','natural_minus_no_peer'),('mae','answer_probability_consistent')):
        per_cell={}
        for i in range(24):
            delta=[]
            for f in (0,1):
                if kind=='candidate_minus_probability_first':
                    a=index[i,f,r,'answer_first','no_peer'];b=index[i,f,r,'probability_first','no_peer'];eligible=a['own_initial_valid']
                else:
                    a=index[i,f,r,'answer_first','natural'];b=index[i,f,r,'answer_first','no_peer'];eligible=a['initials_valid']
                delta.append(a[metric]-b[metric] if eligible and a['format_valid'] and b['format_valid'] else None)
            per_cell[str(i)]=mean(delta) if all(x is not None for x in delta) else None
        complete=all(x is not None for x in per_cell.values())
        c=dict(classification='primary_descriptive' if kind.startswith('candidate') else 'secondary_descriptive',per_cell=per_cell,
               complete_cells=sum(x is not None for x in per_cell.values()),complete_mean=mean(per_cell.values()) if complete else None)
        # Resample entire independent cells, preserving complement/arm pairing.
        c['descriptive_interval']=interval(list(per_cell.values()),d['bootstrap_seed'],d['bootstrap_resamples']) if complete else None
        contrasts[f'{r}:{kind}:{metric}']=c
    return contrasts


def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text());d,cfg=m['design'],m['config']
    tasks(d);check(m['engineering_gate']==GATE and m['decision_rule']==DECISION,'Frozen controls')
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Generation mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==672 and len(list(dr.glob('result-*.json')))==24,'Coverage mismatch')
    audit=Audit(dr,cfg);rows=[];initial_rows=[]
    for i in range(24):
        result=json.loads((dr/f'result-{i}.json').read_text());check(result==run_cell(audit,d,i),'Replay mismatch')
        for case in result['cases'].values():
            f,r,initials=case['flip'],case['relation'],case['initials']
            for a,out in enumerate(initials):
                obs=initial_prompt(d,i,f,r,a)[1]
                initial_rows.append(dict(cell=i,flip=f,relation=r,agent=a,**previous.measured(out,obs)))
            for arm,out in case['finals'].items():
                order,peer=arm.split(':');obs=final_prompt(d,i,f,r,order,peer,initials)[1]
                rows.append(dict(cell=i,flip=f,relation=r,order=order,peer=peer,
                    own_initial_valid=initials[0]['format_valid'],initials_valid=all(x['format_valid'] for x in initials),**previous.measured(out,obs)))
    groups={}
    for r,order,peer in itertools.product(RELATIONS,ORDERS,PEERS):
        rs=[x for x in rows if (x['relation'],x['order'],x['peer'])==(r,order,peer)]
        g=stats(rs);g.update(correct_posterior_all_calls=sum(x.get('posterior_correct',0) for x in rs),
            map_consistent_all_calls=sum(x.get('answer_probability_consistent',0) for x in rs),order_compliant_all_calls=sum(x.get('order_compliant',False) for x in rs))
        groups[f'{r}:{order}:{peer}']=g
    contrasts=contrasts_for(rows,d)
    baseline=[x for x in rows if (x['order'],x['peer'])==('answer_first','no_peer')]
    check(len(baseline)==96,'Candidate denominator')
    checks=dict(initials_valid=all(x['format_valid'] for x in initial_rows),baseline_valid=all(x['format_valid'] for x in baseline),
        posterior=sum(x.get('posterior_correct',0) for x in baseline)/96>=GATE['posterior_correct_min'],
        map_consistency=sum(x.get('answer_probability_consistent',0) for x in baseline)/96>=GATE['map_consistent_min'])
    complete=all(x['complete_mean'] is not None for x in contrasts.values() if x['classification']=='primary_descriptive')
    usage=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    summary=dict(status='HELD_OUT_INTERFACE_REPLICATION_NOT_DICE_CONFIRMATION',calls=len(audit.calls),independent_cells=24,
        initial_calls=len(initial_rows),initial_invalid=sum(not x['format_valid'] for x in initial_rows),initial_correct=sum(x.get('posterior_correct',0) for x in initial_rows),
        final_invalid=sum(not x['format_valid'] for x in rows),groups=groups,contrasts=contrasts,
        engineering_gate=dict(passed=all(checks.values()),checks=checks,baseline_calls=96,baseline_correct=sum(x.get('posterior_correct',0) for x in baseline),baseline_map_consistent=sum(x.get('answer_probability_consistent',0) for x in baseline)),
        decision='ELIGIBLE_FOR_FROZEN_COMMUNICATION_FOLLOWUP_NOT_CONFIRMATION' if all(checks.values()) and complete else 'DISCUSS_BEFORE_MORE_INFERENCE',
        usage_complete=usage,prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if usage else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if usage else None,sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='Candidate selected on earlier data; this held-out batch is not pooled with exploration. Bootstrap intervals are cell-clustered descriptive intervals, not multiplicity-adjusted significance tests. One model, known synthetic generative law, trusted origins and supplied exact calculator. No DICE efficacy or natural-task transfer claim.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return summary,dict(initial=initial_rows,final=rows),contrasts,hashes


def main():
    p=argparse.ArgumentParser();p.add_argument('--native-config');p.add_argument('--output',default='runtime/runs/interface-replication-001')
    for k in ('execute','score','audit-only'):p.add_argument('--'+k,action='store_true')
    a=p.parse_args();root=Path(a.output);check(sum((a.execute,a.score,a.audit_only))<=1,'Action')
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2));return
    if a.score:
        objects=summarize(root)
        for name,obj in zip(('summary','cell_metrics','paired_contrasts','audit_input_hashes'),objects):save(root/(name+'.json'),obj)
        print(json.dumps(objects[0],indent=2));return
    d=dict(DESIGN);tasks(d);print(json.dumps(dict(design=d,engineering_gate=GATE,decision_rule=DECISION,design_sha256=digest(d),execute=a.execute)),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text());check(cfg.get('deployment')=='native','Native only');cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')})
    root.mkdir(parents=True,exist_ok=False);dr=root/'shard-000';dr.mkdir();bundle=sources()
    save(dr/'manifest.json',dict(design=d,engineering_gate=GATE,decision_rule=DECISION,config=cfg,source_bundle=bundle,source_sha256=digest(bundle),created_unix=time.time(),python=sys.version))
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as response:metadata=json.load(response)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
    for i in range(24):run_cell(client,d,i,dr);print(f'Completed cell {i}; total=24',flush=True)
    print('Worker completed.',flush=True)


if __name__=='__main__':main()
