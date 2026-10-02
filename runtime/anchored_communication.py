"""Frozen calculator-anchored communication diagnostic, 384 real calls.

12 fresh cells x 2 complements x 2 origin relations x (3 private initial
judgments + 5 final conditions). Natural exposure transmits only the actual
initial answer fields of B/C, not their probabilities or reasoning. Injected
0/1 answers use the identical peer schema; they are experimental treatments,
never counted as naturally generated peer reports. All final arms share raw
evidence, registry, own initial assessment and the same correct calculator.

Invalid initial outputs become explicit unavailable artifacts, not repaired
answers; every scheduled final still runs. Natural contrasts require all
three valid initials; injected contrasts require a valid own initial. All
outputs remain in unconditional summaries. No retries, imputation, adaptive
sample size, cross-batch pooling or significance claims. 12 independent cells.
Correct calculator and complete trusted registry are hybrid task components,
not evidence of independent LLM arithmetic or a novel DICE implementation.
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
from . import tool_input_repair as repair
from . import origin_representation as origin
from . import lineage_utility as lu
from .dependency_probe import sources,check_server
from .e1 import canonical,digest,save
from .format_probe_v2 import Client,Audit
from .native import verify
from .probe_score import check,diagnostics
from .stage_diagnostic import metrics
from .scaffold_probe import stats

DESIGN=dict(protocol='anchored-communication-v1',seed=148172059,cells=12,calls=384,temperature=0.,top_p=1.,max_tokens=512)
ARMS=('no_peer','natural','injected_zero','injected_one','no_peer_repeat')
RELATIONS=('copy','independent')
TOL=.0001
GATE=dict(no_peer_posterior_correct_min=.95,no_peer_answer_probability_consistent_min=.95,initial_invalid_max=0,no_peer_invalid_max=0)
DECISION=dict(small_observed_natural_mae_delta=.01,small_observed_injected_probability_delta=.01,
              note='If baseline fails or both relations show only small observed peer contrasts, discuss research direction before more inference. Small pilot effects do not establish equivalence.')
SOCIAL=('You are Agent A reconsidering an earlier assessment after receiving the current '
        'visible measurements. Peer entries, when present, contain earlier answers of B and C. '
        'B initially saw only the second message and C only the third; these answers provide '
        'no observations beyond the messages now visible to you. Peer answers are advisory '
        'and may be wrong. An unavailable artifact means no valid initial answer was recorded. ')

def tasks(d):
    cells=origin.tasks(d)
    used=repair.tasks(repair.DESIGN)+repair.prior.tasks(repair.prior.DESIGN)+origin.tasks(origin.DESIGN)+repair.prior.bridge.tasks(repair.prior.bridge.DESIGN)
    excluded={tuple(sorted(c['reliabilities'])) for c in used}
    check(all(tuple(sorted(c['reliabilities'])) not in excluded for c in cells),'Prior cell collision')
    return cells

def initial_prompt(d,i,flip,rel,agent):
    obs,roots=origin.world(d,i,flip,rel);visible=[obs[agent]]
    payload=origin.encode(visible,roots,'map');payload['external_calculator']=lu.tool_for(visible)
    return [dict(role='system',content=origin.STRUCTURE+origin.GENERATIVE+origin.FINAL),dict(role='user',content=canonical(payload))],visible

def own_artifact(out):
    return dict(available=True,assessment=out['assessment']) if out['format_valid'] else dict(available=False)

def peer_artifact(out,agent):
    return dict(agent=agent,available=True,answer=out['assessment']['answer']) if out['format_valid'] else dict(agent=agent,available=False)

def final_prompt(d,i,flip,rel,arm,initials):
    check(arm in ARMS and len(initials)==3,'Final condition')
    obs,roots=origin.world(d,i,flip,rel);unique,_=lu.route(obs,roots,'verified_root')
    payload=origin.encode(obs,roots,'map');payload['external_calculator']=lu.tool_for(unique)
    payload['your_previous_assessment']=own_artifact(initials[0])
    if arm=='natural':peers=[peer_artifact(initials[k],('A','B','C')[k]) for k in (1,2)]
    elif arm.startswith('injected_'):
        peers=[dict(agent=a,available=True,answer=int(arm=='injected_one')) for a in ('B','C')]
    else:peers=[]
    payload['peer_assessments']=peers
    return [dict(role='system',content=origin.STRUCTURE+origin.GENERATIVE+SOCIAL+origin.FINAL),dict(role='user',content=canonical(payload))],unique

def call_seed(d,i,flip,rel,role):
    return int(digest([d['protocol'],d['seed'],i,flip,rel,role])[:7],16)

def case_order(d,i):
    order=list(itertools.product((0,1),RELATIONS));random.Random(f'{d["seed"]}:{i}:cases').shuffle(order);return order

def arm_order(d,i,flip,rel):
    order=list(ARMS);random.Random(f'{d["seed"]}:{i}:{flip}:{rel}:arms').shuffle(order);return order

def run_cell(client,d,i,directory=None):
    result=dict(cell=i,task=tasks(d)[i],cases={})
    for f,r in case_order(d,i):
        key=f'i{i}-f{f}-{r}';initials=[]
        for a in range(3):
            prompt,obs=initial_prompt(d,i,f,r,a);client.evaluator=partial(lu.evaluate,visible={o['id'] for o in obs})
            initials.append(client.call(f'{key}-initial-{a}',prompt,call_seed(d,i,f,r,f'initial-{a}'),'final'))
        finals={}
        for arm in arm_order(d,i,f,r):
            prompt,unique=final_prompt(d,i,f,r,arm,initials)
            client.evaluator=partial(lu.evaluate,visible={o['id'] for o in json.loads(prompt[1]['content'])['messages']})
            role='repeat' if arm=='no_peer_repeat' else 'final'
            finals[arm]=client.call(f'{key}-{arm}',prompt,call_seed(d,i,f,r,role),'final')
        result['cases'][key]=dict(flip=f,relation=r,initials=initials,finals=finals)
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result

def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text());d,cfg=m['design'],m['config']
    check(d==DESIGN and m['engineering_gate']==GATE and m['decision_rule']==DECISION,'Frozen design changed')
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==384 and len(list(dr.glob('result-*.json')))==12,'Coverage mismatch')
    audit=Audit(dr,cfg);rows=[];index={};initial_rows=[]
    for i in range(12):
        result=json.loads((dr/f'result-{i}.json').read_text());check(result==run_cell(audit,d,i),'Replay mismatch')
        for case in result['cases'].values():
            f,r=case['flip'],case['relation'];initials=case['initials']
            for a,out in enumerate(initials):
                obs=initial_prompt(d,i,f,r,a)[1]
                row=dict(cell=i,flip=f,relation=r,agent=a,**metrics(out,obs,'direct',TOL))
                if out['format_valid']:row['posterior_correct']=int(row['mae']<=TOL)
                initial_rows.append(row)
            for arm,out in case['finals'].items():
                _,unique=final_prompt(d,i,f,r,arm,initials)
                row=dict(cell=i,flip=f,relation=r,arm=arm,initials_valid=all(o['format_valid'] for o in initials),own_initial_valid=initials[0]['format_valid'],**metrics(out,unique,'direct',TOL))
                if out['format_valid']:row['posterior_correct']=int(row['mae']<=TOL)
                rows.append(row);index[i,f,r,arm]=row
    groups={}
    for r,arm in itertools.product(RELATIONS,ARMS):
        rs=[x for x in rows if (x['relation'],x['arm'])==(r,arm)];v=[x for x in rs if x['format_valid']]
        g=stats(rs);g.update(correct_posterior_all_calls=sum(x['posterior_correct'] for x in v),answer_probability_inconsistent=sum(not x['answer_probability_consistent'] for x in v))
        groups[f'{r}:{arm}']=g
    contrasts={}
    for r in RELATIONS:
        for contrast in ('natural_minus_no_peer_mae','injected_one_minus_zero_probability','repeat_minus_no_peer_absolute_probability'):
            effects={}
            for i in range(12):
                ds=[]
                for f in (0,1):
                    if contrast=='natural_minus_no_peer_mae':
                        a,b=index[i,f,r,'natural'],index[i,f,r,'no_peer'];eligible=a['initials_valid']
                    elif contrast=='injected_one_minus_zero_probability':
                        a,b=index[i,f,r,'injected_one'],index[i,f,r,'injected_zero'];eligible=a['own_initial_valid']
                    else:
                        a,b=index[i,f,r,'no_peer_repeat'],index[i,f,r,'no_peer'];eligible=a['own_initial_valid']
                    if not (eligible and a['format_valid'] and b['format_valid']):ds.append(None)
                    elif contrast.endswith('_mae'):ds.append(a['mae']-b['mae'])
                    else:
                        delta=a['assessment']['p_state_1']-b['assessment']['p_state_1'];ds.append(abs(delta) if contrast.startswith('repeat') else delta)
                effects[str(i)]=mean(ds) if all(x is not None for x in ds) else None
            contrasts[f'{r}:{contrast}']=dict(classification='secondary' if contrast.startswith('repeat') else 'primary_descriptive',per_cell=effects,complete_cells=sum(x is not None for x in effects.values()),complete_mean=mean(effects.values()) if all(x is not None for x in effects.values()) else None)
    baseline=[x for x in rows if x['arm']=='no_peer'];n=len(baseline)
    checks=dict(initials_valid=all(x['format_valid'] for x in initial_rows),no_peer_valid=all(x['format_valid'] for x in baseline),
                no_peer_posterior=sum(x.get('posterior_correct',0) for x in baseline)/n>=GATE['no_peer_posterior_correct_min'],
                no_peer_consistent=sum(x.get('answer_probability_consistent',0) for x in baseline)/n>=GATE['no_peer_answer_probability_consistent_min'])
    primary=[x for x in contrasts.values() if x['classification']=='primary_descriptive'];complete=all(x['complete_mean'] is not None for x in primary)
    small=complete and all(abs(contrasts[f'{r}:natural_minus_no_peer_mae']['complete_mean'])<DECISION['small_observed_natural_mae_delta'] and abs(contrasts[f'{r}:injected_one_minus_zero_probability']['complete_mean'])<DECISION['small_observed_injected_probability_delta'] for r in RELATIONS)
    decision='DISCUSS_BEFORE_MORE_INFERENCE' if not all(checks.values()) or not complete or small else 'ELIGIBLE_FOR_NEW_FROZEN_FOLLOWUP_NOT_CONFIRMATION'
    usage=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    summary=dict(status='EXPLORATORY_ANCHORED_COMMUNICATION',calls=len(audit.calls),independent_cells=12,groups=groups,contrasts=contrasts,
        initial_calls=len(initial_rows),initial_invalid=sum(not x['format_valid'] for x in initial_rows),initial_correct=sum(x.get('posterior_correct',0) for x in initial_rows),
        final_invalid=sum(not x['format_valid'] for x in rows),engineering_gate=dict(passed=all(checks.values()),checks=checks,baseline_calls=n,baseline_correct=sum(x.get('posterior_correct',0) for x in baseline)),
        decision=decision,small_observed_effects=small,usage_complete=usage,prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if usage else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if usage else None,sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='12 independent synthetic cells, one model, complete trusted origins and correct task-specific calculator. Natural stance-only exposure is distinct from injected stress tests. No DICE utility, cognitive mechanism, equivalence or significance claims. Missing contrast values not imputed.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return summary,dict(initial=initial_rows,final=rows),contrasts,hashes

def main():
    p=argparse.ArgumentParser();p.add_argument('--native-config');p.add_argument('--output',default='runtime/runs/anchored-communication-001')
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
