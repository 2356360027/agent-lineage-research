"""Frozen exploratory repair diagnostic; not a new algorithm or DICE evaluation.

12 fresh cells x complements x four input cases x four arms = 384 calls.
Arms: original, explicit-check instruction, deterministic error report without
numeric answer, and repaired inputs with recomputation plus the same report.
The registry is trusted and complete by construction. All arms see all evidence.
Arithmetic is exact on declared inputs; this tests input errors, not tool math.
Report/repair are conventional engineering baselines, not CRITIC/ToolVerifier
implementations. No chain-of-thought interpretation, natural prevalence claims,
adaptive samples, retries, pooling, significance or cross-model claims.
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
from . import tool_input_audit as prior
from . import origin_representation as origin
from . import lineage_utility as lu
from .dependency_probe import sources, check_server
from .e1 import canonical, digest, save
from .format_probe_v2 import Client, Audit
from .native import verify
from .probe_score import check, diagnostics
from .stage_diagnostic import metrics
from .scaffold_probe import stats

DESIGN=dict(protocol='tool-input-repair-v1',seed=137061948,cells=12,calls=384,temperature=0.,top_p=1.,max_tokens=512)
ARMS=('original','explicit_check','report','repair')
CASES=prior.CASES
TOL=0.0001
CHECK=('Before answering, check whether calculator inputs cover every visible originating '
       'measurement exactly once, with matching payloads. Do not treat numerical precision '
       'as evidence of correct input selection. Correct duplicates or omissions when inferring '
       'the posterior from all unique visible measurements. ')
REPORT=('If input_audit is present, it is a deterministic check against the same visible origin '
        'registry, referring to the original calculator inputs. It contains no additional '
        'measurement or numeric answer. Use it when interpreting the current calculator. ')
GATE=dict(repair_correct_fraction_min=.95,repair_invalid_max=0,valid_control_lost_correct_max=1)

def tasks(d):
    cells=origin.tasks(d)
    used={tuple(sorted(c['reliabilities'])) for c in prior.tasks(prior.DESIGN)+origin.tasks(origin.DESIGN)+prior.bridge.tasks(prior.bridge.DESIGN)}
    check(all(tuple(sorted(c['reliabilities'])) not in used for c in cells),'Prior cell collision')
    return cells

def trial(d,i,flip,relation,condition,arm):
    check((relation,condition) in CASES and arm in ARMS,'Condition')
    obs,roots=origin.world(d,i,flip,relation)
    unique,_=lu.route(obs,roots,'verified_root')
    selected=unique if condition=='valid' else obs if condition=='duplicated' else obs[:2]
    audit=prior.validate_inputs(obs,roots,selected)
    initial_tool=lu.tool_for(selected)
    check(audit['valid']==(condition=='valid'),'Invalid intervention')
    payload=origin.encode(obs,roots,'map');payload['external_calculator']=initial_tool
    if arm in ('report','repair'):
        payload['input_audit']={k:audit[k] for k in ('valid','duplicate_origins','missing_origins')}
        payload['input_audit']['scope']='original calculator inputs'
    if arm=='repair':payload['external_calculator']=audit['corrected_calculator']
    system=origin.STRUCTURE+origin.GENERATIVE+REPORT+(CHECK if arm!='original' else '')+origin.FINAL
    return [dict(role='system',content=system),dict(role='user',content=canonical(payload))],unique,initial_tool

def schedule(d,i):
    entries=[(f,r,c,a) for f,(r,c),a in itertools.product((0,1),CASES,ARMS)]
    random.Random(f'{d["seed"]}:{i}:schedule').shuffle(entries)
    return entries

def run_cell(client,d,i,directory=None):
    result=dict(cell=i,task=tasks(d)[i],outputs={})
    seed=int(digest([d['protocol'],d['seed'],i])[:7],16)
    for flip,rel,condition,arm in schedule(d,i):
        prompt,unique,initial=trial(d,i,flip,rel,condition,arm)
        visible={o['id'] for o in json.loads(prompt[1]['content'])['messages']}
        client.evaluator=partial(lu.evaluate,visible=visible)
        key=f'i{i}-f{flip}-{rel}-{condition}-{arm}'
        result['outputs'][key]=dict(flip=flip,relation=rel,condition=condition,arm=arm,
                                   outcome=client.call(key,prompt,seed,'final'))
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result

def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text());d,cfg=m['design'],m['config']
    check(d==DESIGN and m['engineering_gate']==GATE,'Frozen design/gate changed')
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==384 and len(list(dr.glob('result-*.json')))==12,'Coverage mismatch')
    audit=Audit(dr,cfg);rows=[];index={}
    for i in range(12):
        result=json.loads((dr/f'result-{i}.json').read_text());check(result==run_cell(audit,d,i),'Replay mismatch')
        for e in result['outputs'].values():
            f,r,c,a=[e[k] for k in ('flip','relation','condition','arm')]
            prompt,unique,initial=trial(d,i,f,r,c,a);out=e['outcome']
            row=dict(cell=i,flip=f,relation=r,condition=c,arm=a,**metrics(out,unique,'direct',TOL))
            if out['format_valid']:
                p=out['assessment']['p_state_1']
                row.update(posterior_correct=int(row['mae']<=TOL),original_tool_agreement=int(abs(p-initial['p_state_1'])<=TOL))
            rows.append(row);index[i,f,r,c,a]=row
    groups={}
    for (r,c),a in itertools.product(CASES,ARMS):
        rs=[x for x in rows if (x['relation'],x['condition'],x['arm'])==(r,c,a)]
        v=[x for x in rs if x['format_valid']];g=stats(rs)
        g.update(correct_posterior_all_calls=sum(x['posterior_correct'] for x in v),original_tool_agreement_all_calls=sum(x['original_tool_agreement'] for x in v),
                 answer_probability_inconsistent=sum(not x['answer_probability_consistent'] for x in v))
        groups[f'{r}:{c}:{a}']=g
    contrasts={}
    # Four prespecified descriptive contrasts; average complements within cell.
    for r in ('copy','independent'):
        c='duplicated' if r=='copy' else 'omitted'
        for a in ('report','repair'):
            effects={}
            for i in range(12):
                pairs=[(index[i,f,r,c,a],index[i,f,r,c,'explicit_check']) for f in (0,1)]
                effects[str(i)]=mean(x['mae']-y['mae'] for x,y in pairs) if all(x['format_valid'] and y['format_valid'] for x,y in pairs) else None
            contrasts[f'{r}:{a}_minus_explicit_check_mae']=dict(per_cell=effects,complete_cells=sum(v is not None for v in effects.values()),
                complete_mean=mean(effects.values()) if all(v is not None for v in effects.values()) else None)
    repaired=[x for x in rows if x['arm']=='repair']
    lost=sum(index[i,f,r,'valid','original'].get('posterior_correct',0)==1 and index[i,f,r,'valid','repair'].get('posterior_correct',0)!=1 for i,f,r in itertools.product(range(12),(0,1),('copy','independent')))
    checks=dict(no_repair_invalid=all(x['format_valid'] for x in repaired),correct_fraction=sum(x.get('posterior_correct',0) for x in repaired)/len(repaired)>=GATE['repair_correct_fraction_min'],valid_control_regression=lost<=GATE['valid_control_lost_correct_max'])
    usage=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    summary=dict(status='EXPLORATORY_TOOL_INPUT_REPAIR',calls=len(audit.calls),independent_cells=12,groups=groups,primary_descriptive_contrasts=contrasts,
        engineering_gate=dict(passed=all(checks.values()),checks=checks,correct_repair_calls=sum(x.get('posterior_correct',0) for x in repaired),repair_calls=len(repaired),lost_valid_controls=lost),
        invalid_calls=sum(not c['outcome']['format_valid'] for c in audit.calls),usage_complete=usage,
        prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if usage else None,completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if usage else None,
        sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='One model, 12 synthetic cells, trusted complete origins. Conventional program repair; no novelty or DICE efficacy. Prompt lengths not matched. Invalids retained; no imputation; no significance claim.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return summary,rows,contrasts,hashes

def main():
    p=argparse.ArgumentParser();p.add_argument('--native-config',required=False);p.add_argument('--output',default='runtime/runs/tool-input-repair-001')
    for k in ('execute','score','audit-only'):p.add_argument('--'+k,action='store_true')
    a=p.parse_args();root=Path(a.output);check(sum((a.execute,a.score,a.audit_only))<=1,'Action')
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2));return
    if a.score:
        objects=summarize(root)
        for name,obj in zip(('summary','cell_metrics','paired_contrasts','audit_input_hashes'),objects):save(root/(name+'.json'),obj)
        print(json.dumps(objects[0],indent=2));return
    d=dict(DESIGN);tasks(d)
    for i in range(12):
        for f,r,c,arm in schedule(d,i):trial(d,i,f,r,c,arm)
    print(json.dumps(dict(design=d,engineering_gate=GATE,design_sha256=digest(d),execute=a.execute)),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text());check(cfg.get('deployment')=='native','Native only');cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')})
    root.mkdir(parents=True,exist_ok=False);dr=root/'shard-000';dr.mkdir();bundle=sources()
    save(dr/'manifest.json',dict(design=d,engineering_gate=GATE,config=cfg,source_bundle=bundle,source_sha256=digest(bundle),created_unix=time.time(),python=sys.version))
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as response:metadata=json.load(response)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
    for i in range(12):run_cell(client,d,i,dr);print(f'Completed cell {i}; total=12',flush=True)
    print('Worker completed.',flush=True)

if __name__=='__main__':main()
