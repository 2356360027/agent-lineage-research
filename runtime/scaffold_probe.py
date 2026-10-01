"""Frozen small intervention diagnostic; no automatic communication experiment.

12 fresh conflict cells x complements x ID swaps x four final arms =192 calls.
48 extraction calls feed an actual deterministic local Decimal calculator,
then its result is supplied to the calculator arm's final model call: 240.
The calculator sees only the model's extracted evidence, never a hidden label
or scoring target. It is application-mediated, not native function calling.
Extraction failures still produce a final call with a calculator error record.
Arms: direct, explicit formula, explicit per-observation factors, calculator.
Primary: each intervention's paired posterior-MAE difference vs direct.
Secondary: factor correctness, arithmetic consistency, extraction fidelity,
ID/complement sensitivity; descriptive finite-grid diagnostics, 12 cells.
No internal-mechanism proof; changing output scaffolds is an intervention.
Readiness gate is operational only, not generalization/efficacy. Tool-assisted
readiness must never be reported as model-only competence. Previous results
unchanged. No retries, adaptive stopping, missing-data imputation or pooling.
"""
import argparse
import copy
from decimal import Decimal
import hashlib
import itertools
import json
import random
import sys
import time
import urllib.request
from pathlib import Path
from statistics import mean
from .dependency_probe import sources,check_server
from .e1 import canonical,digest,save
from .format_probe_v2 import Client,Audit
from .native import verify
from .probe_score import check,diagnostics
from .stage_diagnostic import (bayes,number,outcome as prior_outcome,prompt as prior_prompt,
    tasks as prior_tasks,metrics as prior_metrics)
from .id_wording_probe import tasks as id_tasks
from .readiness_pipeline import cells as initial_cells

ARMS=('direct','formula','factors','calculator')
GATE=dict(mae_max=.05,stratum_mae_max=.075,direction_min=.95,symmetry_max=.05,id_delta_max=.05)
FORMULA=('For each observation with value x and reliability r: '
         'P(X=x|Y=1)=r if x=1, otherwise 1-r; '
         'P(X=x|Y=0)=r if x=0, otherwise 1-r. '
         'Multiply the Y=0 factors over all unique observations to get L0, '
         'and multiply the Y=1 factors to get L1. '
         'With the prior 0.5, P(Y=1|all observations)=L1/(L0+L1). '
         'Do not normalize each observation separately and then average. ')


def validate(d):
    check(d==dict(protocol='likelihood-scaffold-v1',seed=60394271,cells=12,
        temperature=0.,top_p=1.,max_tokens=768,calls=240,tolerance=.0001,gate=GATE),'Frozen design changed')


def tasks(d):
    old=initial_cells(dict(protocol='readiness-pipeline-v1',seed=25108369),'double')+initial_cells(dict(protocol='readiness-pipeline-v1',seed=25108369),'communication')
    old+=id_tasks(dict(protocol='id-wording-v1',seed=48172639,cells=12))
    old+=prior_tasks(dict(protocol='stage-diagnostic-v1',seed=59283147))
    excluded={tuple(sorted(c['reliabilities'])) for c in old};out=[]
    rng=random.Random(f'{d["protocol"]}:{d["seed"]}:fresh')
    while len(out)<12:
        a,b=sorted(rng.sample(range(620,951),2));pair=(a/1000,b/1000)
        if b-a<80 or pair in excluded:continue
        excluded.add(pair);out.append(dict(reliabilities=[b/1000,a/1000],base_values=[0,1],regime='conflict'))
    return out


def prompt(cell,i,flip,assignment,arm,tool=None):
    if arm=='extract':return prior_prompt(cell,i,flip,assignment,'extract')
    p=prior_prompt(cell,i,flip,assignment,'direct')
    if arm=='formula':p[0]['content']=FORMULA+p[0]['content']
    elif arm=='factors':
        p=prior_prompt(cell,i,flip,assignment,'calculate')
        p[0]['content']=FORMULA+p[0]['content'].replace('exactly five fields:','exactly six fields:')
        p[0]['content']+=' Include factors: a list with one entry per observation, each with exactly id, p_x_given_y0, p_x_given_y1; these are the individual factors used in your joint likelihoods.'
    elif arm=='calculator':
        check(tool is not None,'Calculator record required')
        p[0]['content']+=' An external deterministic calculator result is supplied in the user input. It was computed solely from the extracted observations and the stated generative model, not from a hidden label. Use it when available; if an error is reported, answer using the observations.'
        payload=json.loads(p[1]['content']);payload['external_calculator']=tool;p[1]['content']=canonical(payload)
    else:check(arm=='direct','Arm')
    return p


def outcome(raw,arm,model):
    if arm!='factors':return prior_outcome(raw,'extract' if arm=='extract' else 'direct',model)
    check(isinstance(raw,dict) and raw.get('model')==model,'API/model mismatch')
    choices=raw.get('choices');check(isinstance(choices,list) and len(choices)==1 and isinstance(choices[0],dict),'API choices')
    c=choices[0];check(isinstance(c.get('message'),dict) and isinstance(c.get('finish_reason'),str),'API envelope')
    def unique(pairs):
        obj={}
        for k,v in pairs:
            check(k not in obj,'Duplicate JSON key');obj[k]=v
        return obj
    try:
        obj=json.loads(c['message']['content'],object_pairs_hook=unique)
        check(isinstance(obj,dict) and 'factors' in obj,'Missing factors')
        fs=obj['factors'];check(isinstance(fs,list),'Factor list')
        for f in fs:
            check(isinstance(f,dict) and set(f)=={'id','p_x_given_y0','p_x_given_y1'},'Factor schema')
            check(isinstance(f['id'],str) and number(f['p_x_given_y0']) and number(f['p_x_given_y1']),'Factor types')
        trimmed=copy.deepcopy(raw);payload={k:v for k,v in obj.items() if k!='factors'}
        trimmed['choices'][0]['message']['content']=canonical(payload)
        result=prior_outcome(trimmed,'calculate',model)
        if not result['format_valid']:return result
        return dict(format_valid=True,assessment=obj,violation=None)
    except (ValueError,KeyError,TypeError) as exc:return dict(format_valid=False,assessment=None,violation=str(exc))


def calculate(extraction):
    if not extraction['format_valid']:return dict(status='error',reason='Invalid extraction output')
    obs=extraction['assessment']['observations'];computed=bayes(obs)
    if computed is None:return dict(status='error',reason='Empty, duplicate or impossible extracted evidence',input_observations=obs)
    return dict(status='ok',input_observations=obs,method='independent_symmetric_likelihood_product_prior_0.5',**computed)


def run_cell(client,d,i,directory=None):
    cell=tasks(d)[i];schedule=list(itertools.product((0,1),(0,1),ARMS))
    random.Random(f'{d["seed"]}:{i}:schedule').shuffle(schedule)
    result=dict(cell=i,task=cell,schedule=[list(x) for x in schedule],outputs={},extractions={},calculator_records={})
    seed=int(digest([d['protocol'],d['seed'],i])[:7],16)
    for flip,assignment,arm in schedule:
        key=f'i{i}-f{flip}-a{assignment}-{arm}';tool=None
        if arm=='calculator':
            extraction=client.call(key+'-extract',prompt(cell,i,flip,assignment,'extract'),seed,'extract')
            result['extractions'][key]=extraction;tool=calculate(extraction);result['calculator_records'][key]=tool
        result['outputs'][key]=client.call(key,prompt(cell,i,flip,assignment,arm,tool),seed,arm)
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result


def factor_metrics(out,obs,tol):
    row=prior_metrics(out,obs,'calculate',tol)
    if not out['format_valid']:return row
    fs=out['assessment']['factors'];truth={o['id']:o for o in obs}
    ids=[f['id'] for f in fs];correct=len(fs)==len(obs) and set(ids)==set(truth) and len(set(ids))==len(ids)
    for f in fs:
        if f['id'] not in truth:correct=False;continue
        o=truth[f['id']];r=o['effective_reliability']
        correct=correct and abs(f['p_x_given_y0']-(r if o['value']==0 else 1-r))<=tol and abs(f['p_x_given_y1']-(r if o['value']==1 else 1-r))<=tol
    products={}
    for suffix in ('y0','y1'):
        v=Decimal(1)
        for f in fs:v*=Decimal(str(f['p_x_given_'+suffix]))
        products[suffix]=float(v)
    row.update(factors_correct=int(correct),products_consistent=int(bool(fs) and all(abs(out['assessment']['likelihood_'+k]-v)<=tol for k,v in products.items())))
    return row


def stats(rs):
    valid=[r for r in rs if r['format_valid']];out=dict(scheduled=len(rs),valid=len(valid),invalid=len(rs)-len(valid))
    for k in ('mae','direction','probability_direction','factors_correct','products_consistent','likelihoods_correct','normalization_consistent','extraction_exact','tool_extraction_exact','tool_available'):
        vs=[r[k] for r in valid if k in r]
        if vs:out[k]=dict(mean=mean(vs),denominator=len(vs))
    return out


def readiness(rs):
    checks=dict(all_valid=all(r['format_valid'] for r in rs))
    if not checks['all_valid']:return dict(passed=False,checks=checks)
    checks['overall_mae']=mean(r['mae'] for r in rs)<=GATE['mae_max']
    for flip,assignment in itertools.product((0,1),(0,1)):
        group=[r for r in rs if r['flip']==flip and r['assignment']==assignment]
        checks[f'f{flip}a{assignment}_mae']=mean(r['mae'] for r in group)<=GATE['stratum_mae_max']
        for k in ('direction','probability_direction'):checks[f'f{flip}a{assignment}_{k}']=mean(r[k] for r in group)>=GATE['direction_min']
    index={(r['cell'],r['flip'],r['assignment']):r for r in rs}
    ids=[abs(index[i,f,0]['assessment']['p_state_1']-index[i,f,1]['assessment']['p_state_1']) for i in range(12) for f in (0,1)]
    sym=[abs(index[i,0,a]['assessment']['p_state_1']+index[i,1,a]['assessment']['p_state_1']-1) for i in range(12) for a in (0,1)]
    checks['id_delta']=mean(ids)<=GATE['id_delta_max'];checks['complement']=mean(sym)<=GATE['symmetry_max']
    return dict(passed=all(checks.values()),checks=checks,id_delta=mean(ids),complement_error=mean(sym))


def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text());d=m['design'];cfg=m['config'];validate(d)
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==240 and len(list(dr.glob('result-*.json')))==12,'Incomplete/extra records')
    audit=Audit(dr,cfg,outcome);rows=[];pairs=[]
    for i,cell in enumerate(tasks(d)):
        result=json.loads((dr/f'result-{i}.json').read_text());check(result==run_cell(audit,d,i),'Replay mismatch');index={}
        for flip,assignment,arm in result['schedule']:
            key=f'i{i}-f{flip}-a{assignment}-{arm}';out=result['outputs'][key]
            obs=json.loads(prior_prompt(cell,i,flip,assignment,'direct')[1]['content'])['observations']
            calculated=factor_metrics(out,obs,d['tolerance']) if arm=='factors' else prior_metrics(out,obs,'direct',d['tolerance'])
            row=dict(cell=i,flip=flip,assignment=assignment,arm=arm,**calculated)
            if arm=='calculator':
                ex=result['extractions'][key];row.update(tool_available=int(result['calculator_records'][key]['status']=='ok'),
                    tool_extraction_exact=int(ex['format_valid'] and sorted(ex['assessment']['observations'],key=canonical)==sorted(obs,key=canonical)))
            rows.append(row);index[flip,assignment,arm]=row
        for flip,assignment,arm in itertools.product((0,1),(0,1),ARMS[1:]):
            a,b=index[flip,assignment,'direct'],index[flip,assignment,arm];valid=a['format_valid'] and b['format_valid']
            pairs.append(dict(cell=i,flip=flip,assignment=assignment,arm=arm,valid=valid,mae_delta=b['mae']-a['mae'] if valid else None))
    contrasts={}
    for arm in ARMS[1:]:
        rs=[r for r in pairs if r['arm']==arm];vs=[r for r in rs if r['valid']]
        contrasts[arm]=dict(scheduled_pairs=len(rs),valid_pairs=len(vs),mae_delta=mean(r['mae_delta'] for r in vs) if vs else None,
            per_cell={str(i):mean(r['mae_delta'] for r in vs if r['cell']==i) for i in sorted({r['cell'] for r in vs})})
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int and c['usage'][k]>=0 for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    report=dict(status='FROZEN_SCAFFOLD_DIAGNOSTIC',calls=len(audit.calls),independent_cells=12,
        groups={arm:stats([r for r in rows if r['arm']==arm]) for arm in ARMS},primary_mae_contrasts=contrasts,
        readiness={arm:readiness([r for r in rows if r['arm']==arm]) for arm in ARMS},
        usage_complete=complete,prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='12 finite-grid cells; no population or internal mechanism inference. Formula and factors include instruction/output-format interventions. Calculator is model extraction + application-mediated exact tool + model final answer, not LLM-only. No automatic communication batch or DICE claim. Invalid records retained.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return report,rows,pairs,hashes


def main():
    p=argparse.ArgumentParser();p.add_argument('--design',default='configs/scaffold_v1.json');p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json');p.add_argument('--output',default='runtime/runs/scaffold-diagnostic-001')
    for name in ('execute','score','audit-only'):p.add_argument('--'+name,action='store_true')
    a=p.parse_args();root=Path(a.output);check(sum([a.execute,a.score,a.audit_only])<=1,'Choose one action')
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2));return
    if a.score:
        objects=summarize(root)
        for name,obj in zip(('summary','cell_metrics','paired_contrasts','audit_input_hashes'),objects):save(root/(name+'.json'),obj)
        print(json.dumps(objects[0],indent=2));return
    d=json.loads(Path(a.design).read_text());validate(d)
    print(json.dumps(dict(mode='execute' if a.execute else 'plan_only',calls=240,design_sha256=digest(d))),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text());check(cfg.get('deployment')=='native','Native only');cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')})
    root.mkdir(parents=True,exist_ok=False);dr=root/'shard-000';dr.mkdir();bundle=sources()
    save(dr/'manifest.json',dict(design=d,config=cfg,source_bundle=bundle,source_sha256=digest(bundle),created_unix=time.time(),python=sys.version))
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as response:metadata=json.load(response)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',dr,outcome)
    for i in range(12):run_cell(client,d,i,dr);print(f'Completed cell {i}; total=12',flush=True)
    print('Worker completed.',flush=True)


if __name__=='__main__':main()
