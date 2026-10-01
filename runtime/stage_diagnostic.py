"""Prospective 144-call error-localization diagnostic, not mechanistic proof.

12 new conflict pairs x 2 complements x 2 numeric ID assignments x 3 arms.
A direct posterior; B evidence extraction only; C extraction plus likelihoods
and posterior. Same task definitions and evidence; output instructions differ.
Greedy decoding, common 512-token cap, no retries. Physical stronger evidence
position balanced across cells, not crossed within cells. No mid-run effects.
Primary: B exact extraction rate; paired C-minus-A posterior absolute error.
Secondary: B extraction + external deterministic Bayes (a HYBRID baseline,
not model reasoning), C extraction/likelihood/normalization errors, ID effects.
Conditional C diagnostics describe observable fields, NOT latent reasoning.
12 independent task cells; finite-grid description only, no significance or
success gate. Numeric correctness tolerance 1e-4, exact extraction separately.
Malformed/truncated outputs are retained outcomes, not silently repaired.
"""
import argparse
from decimal import Decimal, localcontext
import hashlib
import itertools
import json
import math
import random
import sys
import time
import urllib.request
from pathlib import Path
from statistics import mean
from .dependency_probe import sources, check_server
from .e1 import canonical, digest, save
from .format_probe_v2 import Client, Audit
from .id_wording_probe import tasks as previous_tasks, prompt as previous_prompt
from .readiness_pipeline import cells as old_cells
from .native import verify
from .probe_score import check, diagnostics

ARMS=('direct','extract','calculate')


def validate(d):
    check(d==dict(protocol='stage-diagnostic-v1',seed=59283147,cells=12,
                  temperature=0.0,top_p=1.0,max_tokens=512,calls=144,numeric_tolerance=.0001),
          'Frozen design changed')


def tasks(d):
    parent=dict(protocol='readiness-pipeline-v1',seed=25108369)
    excluded={tuple(sorted(c['reliabilities'])) for stage in ('double','communication') for c in old_cells(parent,stage)}
    excluded.update(tuple(sorted(c['reliabilities'])) for c in previous_tasks(dict(protocol='id-wording-v1',seed=48172639,cells=12)))
    rng=random.Random(f'{d["protocol"]}:{d["seed"]}:fresh');out=[]
    while len(out)<12:
        a,b=sorted(rng.sample(range(620,951),2));pair=(a/1000,b/1000)
        if b-a<80 or pair in excluded:continue
        excluded.add(pair);out.append(dict(reliabilities=[b/1000,a/1000],base_values=[0,1],regime='conflict'))
    return out


def prompt(cell,i,flip,assignment,arm):
    p=previous_prompt(cell,i,flip,0,assignment,'revised')
    if arm=='direct':return p
    check(arm in ARMS,'Arm')
    base=p[0]['content'].split('Return only one JSON object with exactly three fields:')[0]
    extraction=('observations (a list containing a faithful copy of every input observation, '
                'each with exactly id, value, and effective_reliability; preserve their associations)')
    if arm=='extract':
        # Do not carry irrelevant answer/probability field instructions into
        # the extraction-only arm (a confound seen in earlier format work).
        base=base.split('answer is your estimate of Y')[0]
        suffix='Return only one JSON object with exactly one field: '+extraction+'. Do not calculate a posterior or output an answer.'
    else:
        suffix=('Return only one JSON object with exactly five fields: '+extraction+', '
                'likelihood_y0 (P(all observed measurements given Y=0)), '
                'likelihood_y1 (P(all observed measurements given Y=1)), '
                'p_state_1 (the normalized posterior for Y=1 using both likelihoods and the prior), '
                'answer (integer 0 or 1). Compute both joint likelihoods using conditional independence. '
                'Likelihoods and p_state_1 must be numbers from 0 to 1. Use at least six decimal places for calculated non-integers.')
    p[0]['content']=base+suffix+' Do not add other fields or text.'
    return p


def number(x):return type(x) in (int,float) and math.isfinite(x) and 0<=x<=1


def outcome(raw,arm,model):
    check(isinstance(raw,dict) and raw.get('model')==model,'API/model mismatch')
    choices=raw.get('choices');check(isinstance(choices,list) and len(choices)==1 and isinstance(choices[0],dict),'API choices')
    c=choices[0];check(isinstance(c.get('message'),dict) and isinstance(c.get('finish_reason'),str),'API envelope')
    def unique(pairs):
        obj={}
        for k,v in pairs:
            if k in obj:raise ValueError('Duplicate JSON key')
            obj[k]=v
        return obj
    try:
        check(c['finish_reason']=='stop','Non-stop/truncated completion')
        obj=json.loads(c['message']['content'],object_pairs_hook=unique)
        fields={'direct':{'answer','p_state_1','citations'},'extract':{'observations'},
                'calculate':{'observations','likelihood_y0','likelihood_y1','p_state_1','answer'}}
        check(isinstance(obj,dict) and set(obj)==fields[arm],'Unexpected schema')
        if arm!='extract':
            check(type(obj['answer']) is int and obj['answer'] in (0,1),'Answer type')
            check(number(obj['p_state_1']),'Probability type')
        if arm=='direct':check(isinstance(obj['citations'],list) and all(x in ('O0','O1') for x in obj['citations']),'Citations')
        else:
            check(isinstance(obj['observations'],list),'Observations type')
            for o in obj['observations']:
                check(isinstance(o,dict) and set(o)=={'id','value','effective_reliability'},'Observation schema')
                check(isinstance(o['id'],str) and type(o['value']) is int and o['value'] in (0,1) and number(o['effective_reliability']),'Observation types')
        if arm=='calculate':check(number(obj['likelihood_y0']) and number(obj['likelihood_y1']),'Likelihood types')
    except (ValueError,TypeError,KeyError) as exc:
        return dict(format_valid=False,assessment=None,violation=str(exc))
    return dict(format_valid=True,assessment=obj,violation=None)


def bayes(obs):
    # Independent Decimal implementation, no reliance on prior scoring oracle.
    if not obs or len({o['id'] for o in obs})!=len(obs):return None
    with localcontext() as ctx:
        ctx.prec=40;l0=l1=Decimal(1)
        for o in obs:
            r=Decimal(str(o['effective_reliability']))
            l1*=r if o['value'] else 1-r;l0*=1-r if o['value'] else r
        if l0+l1==0:return None
        return dict(likelihood_y0=float(l0),likelihood_y1=float(l1),p_state_1=float(l1/(l0+l1)))


def run_cell(client,d,i,directory=None):
    cell=tasks(d)[i];schedule=list(itertools.product((0,1),(0,1),ARMS))
    random.Random(f'{d["seed"]}:{i}:schedule').shuffle(schedule)
    result=dict(cell=i,task=cell,schedule=[list(x) for x in schedule],outputs={})
    for flip,assignment,arm in schedule:
        key=f'i{i}-f{flip}-a{assignment}-{arm}'
        seed=int(digest([d['protocol'],d['seed'],i])[:7],16)
        result['outputs'][key]=client.call(key,prompt(cell,i,flip,assignment,arm),seed,arm)
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result


def metrics(out,obs,arm,tol):
    row=dict(out);truth=bayes(obs);q=truth['p_state_1'];row['oracle_p']=q
    if not out['format_valid']:return row
    a=out['assessment']
    if arm!='extract':
        p=a['p_state_1'];row.update(mae=abs(p-q),direction=int(a['answer']==int(q>.5)),
            probability_direction=int(p!=.5 and (p>.5)==(q>.5)),
            answer_probability_consistent=int(p!=.5 and a['answer']==int(p>.5)))
    if arm!='direct':
        copied=a['observations'];exact=sorted(copied,key=canonical)==sorted(obs,key=canonical)
        extracted=bayes(copied)
        row.update(extraction_exact=int(exact),hybrid_available=int(extracted is not None))
        if extracted is not None:
            row.update(hybrid_mae=abs(extracted['p_state_1']-q),hybrid_direction=int(extracted['p_state_1']!=.5 and (extracted['p_state_1']>.5)==(q>.5)))
        if arm=='calculate':
            correct=all(abs(a[k]-truth[k])<=tol for k in ('likelihood_y0','likelihood_y1'))
            denominator=a['likelihood_y0']+a['likelihood_y1']
            row.update(likelihoods_correct=int(correct),posterior_correct=int(abs(a['p_state_1']-q)<=tol),
                normalization_consistent=int(denominator>0 and abs(a['p_state_1']-a['likelihood_y1']/denominator)<=tol),
                likelihoods_match_extraction=int(extracted is not None and all(abs(a[k]-extracted[k])<=tol for k in ('likelihood_y0','likelihood_y1'))))
    return row


def stats(rs):
    valid=[r for r in rs if r['format_valid']];out=dict(scheduled=len(rs),valid=len(valid),invalid=len(rs)-len(valid))
    for k in ('mae','direction','probability_direction','answer_probability_consistent','extraction_exact',
              'hybrid_available','hybrid_mae','hybrid_direction','likelihoods_correct','posterior_correct',
              'normalization_consistent','likelihoods_match_extraction'):
        vs=[r[k] for r in valid if k in r]
        if vs:out[k]=dict(mean=mean(vs),denominator=len(vs))
    return out


def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text());d=m['design'];cfg=m['config'];validate(d)
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==144 and len(list(dr.glob('result-*.json')))==12,'Incomplete/extra records')
    audit=Audit(dr,cfg,outcome);rows=[];pairs=[]
    for i,cell in enumerate(tasks(d)):
        result=json.loads((dr/f'result-{i}.json').read_text());check(result==run_cell(audit,d,i),'Replay mismatch');index={}
        for flip,assignment,arm in result['schedule']:
            obs=json.loads(prompt(cell,i,flip,assignment,arm)[1]['content'])['observations']
            row=dict(cell=i,flip=flip,assignment=assignment,arm=arm,stronger_last=i%2,
                **metrics(result['outputs'][f'i{i}-f{flip}-a{assignment}-{arm}'],obs,arm,d['numeric_tolerance']))
            rows.append(row);index[flip,assignment,arm]=row
        for flip,assignment in itertools.product((0,1),(0,1)):
            a,c=index[flip,assignment,'direct'],index[flip,assignment,'calculate'];valid=a['format_valid'] and c['format_valid']
            pairs.append(dict(cell=i,flip=flip,assignment=assignment,valid=valid,
                calculate_minus_direct_mae=c['mae']-a['mae'] if valid else None,
                calculate_minus_direct_direction=c['direction']-a['direction'] if valid else None))
    valid=[r for r in pairs if r['valid']];calc=[r for r in rows if r['arm']=='calculate' and r['format_valid']]
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int and c['usage'][k]>=0
                 for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    report=dict(status='FRESH_STAGE_DIAGNOSTIC',calls=len(audit.calls),independent_cells=12,
        groups={arm:stats([r for r in rows if r['arm']==arm]) for arm in ARMS},
        by_stronger_id={f'{arm}/O{assignment}':stats([r for r in rows if r['arm']==arm and r['assignment']==assignment]) for arm in ARMS for assignment in (0,1)},
        primary=dict(extraction='groups.extract.extraction_exact',scheduled_pairs=len(pairs),valid_pairs=len(valid),
            calculate_minus_direct_mae=mean(r['calculate_minus_direct_mae'] for r in valid) if valid else None,
            per_cell={str(i):mean(r['calculate_minus_direct_mae'] for r in valid if r['cell']==i) for i in sorted({r['cell'] for r in valid})}),
        secondary_C_observable_failures=dict(valid=len(calc),extraction_errors=sum(not r['extraction_exact'] for r in calc),
            exact_extraction_wrong_likelihoods=sum(r['extraction_exact'] and not r['likelihoods_correct'] for r in calc),
            correct_likelihoods_wrong_posterior=sum(r['likelihoods_correct'] and not r['posterior_correct'] for r in calc)),
        usage_complete=complete,prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,
        sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='12 finite-grid cells, no repeated draws, no population inference or internal reasoning proof. Output scaffolding is an intervention, not passive observation. B+Bayes uses an external exact calculator; do not call it LLM competence. Invalids retained; valid-only/conditional analyses may be selected. No DICE efficacy; old failures unchanged.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return report,rows,pairs,hashes


def main():
    p=argparse.ArgumentParser();p.add_argument('--design',default='configs/stage_diagnostic_v1.json')
    p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json');p.add_argument('--output',default='runtime/runs/stage-diagnostic-001')
    for name in ('execute','score','audit-only'):p.add_argument('--'+name,action='store_true')
    a=p.parse_args();root=Path(a.output);check(sum([a.execute,a.score,a.audit_only])<=1,'Choose one action')
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2));return
    if a.score:
        objects=summarize(root)
        for name,obj in zip(('summary','cell_metrics','paired_contrasts','audit_input_hashes'),objects):save(root/(name+'.json'),obj)
        print(json.dumps(objects[0],indent=2));return
    d=json.loads(Path(a.design).read_text());validate(d)
    print(json.dumps(dict(mode='execute' if a.execute else 'plan_only',calls=144,design_sha256=digest(d))),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text());check(cfg.get('deployment')=='native','Native only')
    cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')});root.mkdir(parents=True,exist_ok=False);dr=root/'shard-000';dr.mkdir();bundle=sources()
    save(dr/'manifest.json',dict(design=d,config=cfg,source_bundle=bundle,source_sha256=digest(bundle),created_unix=time.time(),python=sys.version))
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as response:metadata=json.load(response)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',dr,outcome)
    for i in range(12):run_cell(client,d,i,dr);print(f'Completed cell {i}; total=12',flush=True)
    print('Worker completed.',flush=True)


if __name__=='__main__':main()
