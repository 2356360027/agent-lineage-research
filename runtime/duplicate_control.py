"""Frozen duplicate-control-v1: 20 fresh cells x 2 complements x 12 calls.

One extraction plus 11 final calls per case: base, strong/weak duplicate at
front/back, four individually tokenizer-length-matched neutral controls,
two identity-registry dedup finals (strong/weak appended). All final contexts
share exactly the same model-extracted calculator result. Same IDs retain
their payload, observations retain relative order. Neutral records occupy
the same list location but necessarily differ in syntax/meaning: matching
tokens does NOT eliminate all formatting/attention confounds.
Registry is an elementary baseline, NOT a novel algorithm or DICE. Its
rendered final input equals baseline exactly, tested before inference;
dedup calls additionally measure repeatability, not an independent treatment.
Primary: duplicate-minus-matched-neutral MAE, averaged over four treatments
and complements within each cell; duplicate-minus-base MAE likewise.
Secondary: positions/strength strata, dedup-minus-duplicate, probability
drift and answer changes. Descriptive 20-cell diagnostic; no significance
claim, sample adaptation, retries, exclusions or imputation. All malformed
outputs retained. New independent evidence retention is software-tested only,
not an empirical utility/generalization claim. No natural peer this batch.
"""
import argparse
import copy
from functools import partial
import hashlib
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
from .scaffold_probe import prompt,calculate,stats,tasks as scaffold_tasks
from .stage_diagnostic import metrics,tasks as stage_tasks
from .id_wording_probe import tasks as id_tasks
from .readiness_pipeline import cells as initial_cells
from .tool_relay import tasks as relay_tasks,outcome

TREATMENTS=('strong_front','strong_back','weak_front','weak_back')
ARMS=('base',)+tuple('dup_'+t for t in TREATMENTS)+tuple('neutral_'+t for t in TREATMENTS)+('dedup_strong_back','dedup_weak_back')
DESIGN=dict(protocol='duplicate-control-v1',seed=82516493,cells=20,calls=480,temperature=0.,top_p=1.,max_tokens=768)

def validate(d):check(d==DESIGN,'Frozen design changed')

def tasks(d):
    old=relay_tasks(dict(protocol='tool-relay-v1',seed=71405382))
    old+=scaffold_tasks(dict(protocol='likelihood-scaffold-v1',seed=60394271))
    old+=stage_tasks(dict(protocol='stage-diagnostic-v1',seed=59283147))
    old+=id_tasks(dict(protocol='id-wording-v1',seed=48172639,cells=12))
    for s in ('double','communication'):old+=initial_cells(dict(protocol='readiness-pipeline-v1',seed=25108369),s)
    excluded={tuple(sorted(c['reliabilities'])) for c in old};out=[]
    rng=random.Random(f'{d["protocol"]}:{d["seed"]}:fresh')
    while len(out)<20:
        a,b=sorted(rng.sample(range(620,951),2));pair=(a/1000,b/1000)
        if b-a<80 or pair in excluded:continue
        excluded.add(pair);out.append(dict(reliabilities=[b/1000,a/1000],base_values=[0,1],regime='conflict'))
    return out

def registry(obs):
    """First-seen order; only exact-ID/payload duplicates removed; conflicts fail."""
    seen={};kept=[];receipts=[]
    for o in obs:
        key=o['id']
        if key in seen:
            check(seen[key]==o,'Conflicting payload for observation ID')
            receipts.append(dict(id=key,action='reference_prior_no_new_evidence'))
        else:seen[key]=copy.deepcopy(o);kept.append(copy.deepcopy(o))
    return kept,receipts

def base_obs(c,i,f):return json.loads(prompt(c,i,f,0,'direct')[1]['content'])['observations']

def messages(c,i,f,tool,arm,n=0):
    p=prompt(c,i,f,0,'calculator',tool)
    p[0]['content']+=' Records containing only task_irrelevant_padding are neutral formatting filler, not observations; ignore them when inferring Y.'
    payload=json.loads(p[1]['content']);obs=payload['observations'];receipts=[]
    if arm!='base':
        kind,strength,pos=arm.split('_');selected=next(o for o in obs if o['id']==('O0' if strength=='strong' else 'O1'))
        item={'task_irrelevant_padding':' filler'*n} if kind=='neutral' else dict(selected)
        obs=([item]+obs) if pos=='front' else (obs+[item])
        if kind=='dedup':obs,receipts=registry(obs)
        payload['observations']=obs
    p[1]['content']=canonical(payload)
    return p,receipts

def padding_plan(c,i,f,tool,count):
    plan={}
    for t in TREATMENTS:
        target=count(messages(c,i,f,tool,'dup_'+t)[0]);found=None
        for n in range(129):
            tokens=count(messages(c,i,f,tool,'neutral_'+t,n)[0])
            if tokens==target:found=dict(n=n,tokens=tokens);break
        check(found is not None,'Cannot match neutral token length; stop before finals')
        check(target+768<=4096,'Context overflow')
        plan[t]=found
    return plan

def run_cell(client,d,i,directory=None,count=None,saved_plans=None):
    c=tasks(d)[i];seed=int(digest([d['protocol'],d['seed'],i])[:7],16)
    result=dict(cell=i,task=c,cases={})
    flips=[0,1];random.Random(f'{d["seed"]}:{i}:flips').shuffle(flips)
    for f in flips:
        obs=base_obs(c,i,f);client.evaluator=partial(outcome,visible={'O0','O1'})
        ex=client.call(f'i{i}-f{f}-extract',prompt(c,i,f,0,'extract'),seed,'extract');tool=calculate(ex)
        plan=padding_plan(c,i,f,tool,count) if count else saved_plans[str(f)]
        check(set(plan)==set(TREATMENTS) and all(type(x['n']) is int and 0<=x['n']<=128 and type(x['tokens']) is int for x in plan.values()),'Padding plan')
        order=list(ARMS);random.Random(f'{d["seed"]}:{i}:{f}:order').shuffle(order)
        case=dict(observations=obs,extraction=ex,tool=tool,padding_plan=plan,order=order,outputs={},receipts={})
        for arm in order:
            n=plan[arm.removeprefix('neutral_')]['n'] if arm.startswith('neutral_') else 0
            p,receipts=messages(c,i,f,tool,arm,n)
            if arm.startswith('dedup_'):check(p==messages(c,i,f,tool,'base')[0],'Dedup input not identical to base')
            case['receipts'][arm]=receipts
            case['outputs'][arm]=client.call(f'i{i}-f{f}-{arm}',p,seed,'calculator')
        result['cases'][str(f)]=case
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result

def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text());d=m['design'];cfg=m['config'];validate(d)
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==480 and len(list(dr.glob('result-*.json')))==20,'Incomplete/extra records')
    audit=Audit(dr,cfg);rows=[];pairs=[];extracts=[];token_matches=[]
    for i,c in enumerate(tasks(d)):
        res=json.loads((dr/f'result-{i}.json').read_text())
        check(res==run_cell(audit,d,i,saved_plans={f:v['padding_plan'] for f,v in res['cases'].items()}),'Replay mismatch')
        for f,case in res['cases'].items():
            obs=case['observations'];ex=case['extraction'];extracts.append(ex['format_valid'] and sorted(ex['assessment']['observations'],key=canonical)==sorted(obs,key=canonical));index={}
            for arm,out in case['outputs'].items():
                row=dict(cell=i,flip=int(f),arm=arm,**metrics(out,obs,'direct',.0001));rows.append(row);index[arm]=row
            for t in TREATMENTS:
                usage=[]
                for prefix in ('dup_','neutral_'):
                    rec=json.loads((dr/f'i{i}-f{f}-{prefix}{t}.json').read_text());usage.append((rec.get('usage') or {}).get('prompt_tokens'))
                token_matches.append(all(type(v) is int and v==case['padding_plan'][t]['tokens'] for v in usage))
                contrasts=[('duplicate_minus_neutral','neutral_'+t,'dup_'+t),('duplicate_minus_base','base','dup_'+t)]
                if t.endswith('back'):contrasts.append(('dedup_minus_duplicate','dup_'+t,'dedup_'+t))
                for contrast,a,b in contrasts:
                    x,y=index[a],index[b];valid=x['format_valid'] and y['format_valid']
                    pairs.append(dict(cell=i,flip=int(f),treatment=t,contrast=contrast,valid=valid,
                        mae_delta=y['mae']-x['mae'] if valid else None,
                        abs_probability_delta=abs(y['assessment']['p_state_1']-x['assessment']['p_state_1']) if valid else None,
                        answer_changed=int(y['assessment']['answer']!=x['assessment']['answer']) if valid else None))
    summaries={}
    for contrast in ('duplicate_minus_neutral','duplicate_minus_base','dedup_minus_duplicate'):
        ps=[p for p in pairs if p['contrast']==contrast];vs=[p for p in ps if p['valid']]
        percell={str(i):mean(p['mae_delta'] for p in vs if p['cell']==i) for i in sorted({p['cell'] for p in vs})}
        all_cells_complete=len(vs)==len(ps)
        summaries[contrast]=dict(scheduled_pairs=len(ps),valid_pairs=len(vs),per_cell_mae_delta=percell,
            complete_cell_mean=mean(percell.values()) if all_cells_complete else None,
            valid_pairs_mean=mean(p['mae_delta'] for p in vs) if vs else None,
            mean_abs_probability_delta=mean(p['abs_probability_delta'] for p in vs) if vs else None,
            answer_changes=sum(p['answer_changed'] for p in vs))
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    summary=dict(status='DUPLICATE_CONTROL_DESCRIPTIVE',calls=len(audit.calls),independent_cells=20,
        groups={a:stats([r for r in rows if r['arm']==a]) for a in ARMS},contrasts=summaries,
        primary=['duplicate_minus_neutral','duplicate_minus_base'],token_matched_pairs=sum(token_matches),expected_token_matched_pairs=160,
        token_control_valid=all(token_matches),exact_extractions=sum(extracts),expected_extractions=40,
        prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,
        sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='Fresh synthetic finite-grid diagnostic, one model, no significance/mechanism/DICE claim. Padding semantics and record syntax differ. Tool result fixed; generated probabilities are not token probabilities. Registry equals baseline rendering; new-independent-evidence utility not tested. Invalids retained; token-control interpretation requires token_control_valid.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return summary,rows,pairs,hashes

def main():
    p=argparse.ArgumentParser();p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json');p.add_argument('--output',default='runtime/runs/duplicate-control-001')
    for k in ('execute','score','audit-only'):p.add_argument('--'+k,action='store_true')
    a=p.parse_args();root=Path(a.output);check(sum((a.execute,a.score,a.audit_only))<=1,'Action')
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2));return
    if a.score:
        objs=summarize(root)
        for name,obj in zip(('summary','cell_metrics','paired_contrasts','audit_input_hashes'),objs):save(root/(name+'.json'),obj)
        print(json.dumps(objs[0],indent=2));return
    d=dict(DESIGN);print(json.dumps(dict(design=d,design_sha256=digest(d),execute=a.execute)),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text());check(cfg.get('deployment')=='native','Native only');cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')})
    root.mkdir(parents=True,exist_ok=False);dr=root/'shard-000';dr.mkdir();bundle=sources()
    save(dr/'manifest.json',dict(design=d,config=cfg,source_bundle=bundle,source_sha256=digest(bundle),python=sys.version,created_unix=time.time()))
    verify(cfg)
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(cfg['model_path'],local_files_only=True,trust_remote_code=False)
    count=lambda msg:len(tokenizer.apply_chat_template(msg,tokenize=True,add_generation_prompt=True))
    # Match controls before any inference; recheck actual extracted tool in run_cell.
    for i,c in enumerate(tasks(d)):
        for f in (0,1):padding_plan(c,i,f,calculate(dict(format_valid=True,assessment=dict(observations=base_obs(c,i,f)))),count)
    print('All prospective tokenizer length matches passed.',flush=True)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as response:metadata=json.load(response)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
    for i in range(20):run_cell(client,d,i,dr,count);print(f'Completed cell {i}; total=20',flush=True)
    print('Worker completed.',flush=True)

if __name__=='__main__':main()
