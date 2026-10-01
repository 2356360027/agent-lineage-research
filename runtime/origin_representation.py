"""Frozen origin-representation-v1, 408 model calls, 12 fresh task cells.

Per cell and copy/independent relation: three no-value pure selectors, plus
two complemented numeric worlds x (three numeric selectors + three finals
with identical correct tool + one program-premerged final). 34 calls/cell.
No duplicate pure call for the complement that carries no numeric values.
Representations are separate map, inline origin_id, and explicit groups.
All raw representations encode identical messages, order and origin partition.
Grouped format reorders storage but message_order defines original order.
Opaque randomized IDs are fixed within each cell, matched across formats;
unlike prior batches they lack O0/R0 suffix alignment. No cross-batch causal
comparison. Different formats are NOT length-matched: test is representation
bundle efficacy, not an isolated token-position mechanism.

Primary descriptive copy contrasts: inline/map and grouped/map in correct
origin selection (pure, numeric) and final posterior MAE. Independent-world
retention, task-burden contrasts, and premerged reference are secondary.
Numeric vs pure changes both content and instructions, not arithmetic alone.
Selectors and finals use independent contexts: correct selection does NOT
prove the final call internally understood the mapping. All finals receive
the same exact calculator result per world, computed from experiment-known
unique roots (hybrid reference, not model reasoning). No selector-driven
tool, retries, mid-run effects, adaptive samples, invalid imputation or
significance claims. Program premerge is a standard engineering baseline.
"""
import argparse
from functools import partial
import hashlib
import itertools
import json
import random
import string
import sys
import time
import urllib.request
from pathlib import Path
from statistics import mean
from . import lineage_utility as lu
from .dependency_probe import sources,check_server
from .e1 import canonical,digest,save
from .format_probe_v2 import Client,Audit
from .native import verify
from .probe_score import check,diagnostics
from .stage_diagnostic import metrics
from .scaffold_probe import stats

DESIGN=dict(protocol='origin-representation-v1',seed=104738615,cells=12,calls=408,temperature=0.,top_p=1.,max_tokens=512)
FORMATS=('map','inline','grouped')
RELATIONS=('copy','independent')
STRUCTURE=('The input describes messages and their originating measurements. Origin information is '
    'provided by the experiment environment, not claimed by a sender. It is encoded in one of three '
    'equivalent ways: origin_registry maps message IDs to origin IDs; origin_id on a message gives '
    'its origin; origin_groups assigns all messages inside a group to that group\'s origin_id. '
    'message_order specifies original message order regardless of JSON storage order. Same origin '
    'means copies of ONE originating measurement. Different origins mean distinct measurements even '
    'when message contents happen to match. ')
SELECT=('Select exactly one message per distinct origin. Keep the first representative in '
    'message_order and return representatives in that order. Return only JSON with exactly '
    'selected_message_ids: a list of message IDs. Do not calculate probabilities or add text.')
GENERATIVE=('Infer binary state Y with prior P(Y=1)=0.5. Each unique originating measurement equals '
    'Y with its effective_reliability, symmetrically for either Y. Distinct origins are conditionally '
    'independent given Y ONLY by this experiment\'s generative definition. The value is the observed '
    'measurement, not the unknown Y. ')
FINAL=('The external_calculator gives an exact likelihood calculation on input_observations, treating '
    'its listed measurements as independent. Use the supplied origin information and visible '
    'measurements to interpret it. Return only JSON with exactly answer (integer 0 or 1), p_state_1 '
    '(number from 0 to 1, P(Y=1|all unique visible originating measurements), NOT confidence in the '
    'selected answer), and citations (list of visible message IDs). Use at least six decimal places '
    'for calculated non-integers. No additional text.')

def validate(d):check(d==DESIGN,'Frozen design changed')

def tasks(d):
    old=lu.tasks(lu.DESIGN)+lu.duplicate_tasks(lu.DUPLICATE_DESIGN)+lu.relay_tasks(dict(protocol='tool-relay-v1',seed=71405382))
    old+=lu.scaffold_tasks(dict(protocol='likelihood-scaffold-v1',seed=60394271))+lu.stage_tasks(dict(protocol='stage-diagnostic-v1',seed=59283147))
    old+=lu.id_tasks(dict(protocol='id-wording-v1',seed=48172639,cells=12))
    for s in ('double','communication'):old+=lu.initial_cells(dict(protocol='readiness-pipeline-v1',seed=25108369),s)
    excluded={tuple(sorted(c['reliabilities'])) for c in old};out=[];rng=random.Random(f'{d["protocol"]}:{d["seed"]}:fresh')
    while len(out)<12:
        a,b=sorted(rng.sample(range(620,951),2));pair=(a/1000,b/1000)
        if b-a<80 or pair in excluded:continue
        excluded.add(pair);out.append(dict(reliabilities=[b/1000,a/1000],base_values=[0,1],regime='conflict'))
    return out

def world(d,i,flip,relation):
    obs,origin=lu.world(tasks(d)[i],i,flip,relation)
    rng=random.Random(f'{d["seed"]}:{i}:identifiers');labels=[]
    while len(labels)<6:
        label=''.join(rng.choices(string.ascii_lowercase,k=4))
        if label not in labels:labels.append(label)
    mids={f'O{k}':'m_'+labels[k] for k in range(3)};rids={f'R{k}':'r_'+labels[k+3] for k in range(3)}
    return [dict(o,id=mids[o['id']]) for o in obs],{mids[k]:rids[v] for k,v in origin.items()}

def encode(obs,origin,representation,pure=False):
    records=[dict(id=o['id']) if pure else dict(o) for o in obs]
    payload=dict(message_order=[o['id'] for o in obs])
    if representation=='map':payload.update(messages=records,origin_registry={o['id']:origin[o['id']] for o in obs})
    elif representation=='inline':payload['messages']=[dict(o,origin_id=origin[o['id']]) for o in records]
    elif representation=='grouped':
        groups={}
        for o in records:groups.setdefault(origin[o['id']],[]).append(o)
        payload['origin_groups']=[dict(origin_id=k,messages=v) for k,v in groups.items()]
    else:raise ValueError('Representation')
    return payload

def decode(payload):
    """Losslessness audit of experiment encodings, not an LLM intervention."""
    if 'origin_groups' in payload:
        records=[o for g in payload['origin_groups'] for o in g['messages']]
        origins={o['id']:g['origin_id'] for g in payload['origin_groups'] for o in g['messages']}
    elif 'origin_registry' in payload:records=payload['messages'];origins=payload['origin_registry']
    else:
        records=[{k:v for k,v in o.items() if k!='origin_id'} for o in payload['messages']]
        origins={o['id']:o['origin_id'] for o in payload['messages']}
    index={o['id']:o for o in records}
    check(len(index)==len(records) and set(payload['message_order'])==set(index),'Encoding IDs')
    return [index[k] for k in payload['message_order']],origins

def schedule(d,i):
    out=[]
    for rel in RELATIONS:
        out.extend((rel,-1,'pure',rep) for rep in FORMATS)
        for flip in (0,1):
            out.extend((rel,flip,stage,rep) for stage in ('numeric_select','final') for rep in FORMATS)
            out.append((rel,flip,'final','premerged'))
    random.Random(f'{d["seed"]}:{i}:schedule').shuffle(out)
    return out

def trial(d,i,rel,flip,stage,rep):
    obs,origin=world(d,i,max(0,flip),rel);unique,_=lu.route(obs,origin,'verified_root')
    rendered=unique if rep=='premerged' else obs
    payload=encode(rendered,origin,'map' if rep=='premerged' else rep,stage=='pure')
    if stage=='final':payload['external_calculator']=lu.tool_for(unique)
    system=STRUCTURE+('' if stage=='pure' else GENERATIVE)+(FINAL if stage=='final' else SELECT)
    return [dict(role='system',content=system),dict(role='user',content=canonical(payload))],rendered,unique,origin

def run_cell(client,d,i,directory=None):
    result=dict(cell=i,task=tasks(d)[i],outputs={});seed=int(digest([d['protocol'],d['seed'],i])[:7],16)
    for rel,flip,stage,rep in schedule(d,i):
        p,rendered,unique,origin=trial(d,i,rel,flip,stage,rep)
        client.evaluator=partial(lu.evaluate,visible={o['id'] for o in rendered})
        key=f'i{i}-{rel}-f{flip}-{stage}-{rep}'
        result['outputs'][key]=dict(relation=rel,flip=flip,stage=stage,representation=rep,
            outcome=client.call(key,p,seed,'final' if stage=='final' else 'select'))
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result

def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text());d=m['design'];cfg=m['config'];validate(d)
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==408 and len(list(dr.glob('result-*.json')))==12,'Incomplete/extra records')
    audit=Audit(dr,cfg);rows=[];index={}
    for i in range(12):
        result=json.loads((dr/f'result-{i}.json').read_text());check(result==run_cell(audit,d,i),'Replay mismatch')
        for entry in result['outputs'].values():
            rel,f,stage,rep=[entry[k] for k in ('relation','flip','stage','representation')];out=entry['outcome']
            _,rendered,unique,origin=trial(d,i,rel,f,stage,rep)
            row=dict(cell=i,relation=rel,flip=f,stage=stage,representation=rep)
            if stage=='final':row.update(metrics(out,unique,'direct',.0001))
            else:
                ids=out['assessment']['selected_message_ids'] if out['format_valid'] else None;expected=[o['id'] for o in unique]
                row.update(out);row.update(correct_origins=int(ids is not None and len(ids)==len(expected) and {origin[k] for k in ids}=={origin[k] for k in expected}),exact_representatives=int(ids==expected))
            rows.append(row);index[i,rel,f,stage,rep]=row
    groups={}
    for rel,stage in itertools.product(RELATIONS,('pure','numeric_select','final')):
        for rep in FORMATS+(('premerged',) if stage=='final' else ()):
            rs=[r for r in rows if r['relation']==rel and r['stage']==stage and r['representation']==rep]
            group=stats(rs)
            if stage!='final':
                group.update(correct_origins_all_calls=sum(r['correct_origins'] for r in rs),exact_representatives_all_calls=sum(r['exact_representatives'] for r in rs))
            groups[f'{rel}:{stage}:{rep}']=group
    pairs=[]
    for i,rel,stage,rep in itertools.product(range(12),RELATIONS,('pure','numeric_select','final'),('inline','grouped')):
        flips=(-1,) if stage=='pure' else (0,1)
        for f in flips:
            a,b=index[i,rel,f,stage,'map'],index[i,rel,f,stage,rep]
            valid=a['format_valid'] and b['format_valid']
            delta=(b['mae']-a['mae'] if valid else None) if stage=='final' else b['correct_origins']-a['correct_origins']
            pairs.append(dict(cell=i,relation=rel,stage=stage,representation=rep,flip=f,valid=valid,delta=delta))
    contrasts={}
    for rel,stage,rep in itertools.product(RELATIONS,('pure','numeric_select','final'),('inline','grouped')):
        ps=[p for p in pairs if p['relation']==rel and p['stage']==stage and p['representation']==rep];vs=[p for p in ps if p['delta'] is not None]
        pc={str(i):mean(p['delta'] for p in vs if p['cell']==i) for i in sorted({p['cell'] for p in vs})}
        contrasts[f'{rel}:{stage}:{rep}_minus_map']=dict(scheduled=len(ps),valid_output_pairs=sum(p['valid'] for p in ps),per_cell=pc,complete_mean=mean(pc.values()) if len(vs)==len(ps) else None,
            meaning='MAE difference (negative better)' if stage=='final' else 'Correct-origin-selection rate difference; invalid=unsuccessful')
    burden={}
    for rel,rep in itertools.product(RELATIONS,FORMATS):
        vals=[mean(index[i,rel,f,'numeric_select',rep]['correct_origins'] for f in (0,1))-index[i,rel,-1,'pure',rep]['correct_origins'] for i in range(12)]
        burden[f'{rel}:{rep}']=dict(numeric_minus_pure_correct_rate=mean(vals),per_cell=vals)
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    summary=dict(status='ORIGIN_REPRESENTATION_DIAGNOSTIC',calls=len(audit.calls),independent_cells=12,groups=groups,
        primary_copy_contrasts={k:v for k,v in contrasts.items() if k.startswith('copy:')},secondary_independent_contrasts={k:v for k,v in contrasts.items() if k.startswith('independent:')},secondary_burden=burden,
        invalid_calls=sum(not c['outcome']['format_valid'] for c in audit.calls),
        prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='One model/12 synthetic cells; known origins; descriptive, not DICE efficacy. Encoding bundles differ in length. Selectors and finals are separate contexts, not proof of internal recognition-use dissociation. Pure/numeric changes instructions and content. Opaque IDs/new prompt prevent cross-batch causal comparison. All invalids retained, no retries/adaptation.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return summary,rows,pairs,hashes

def main():
    p=argparse.ArgumentParser();p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json');p.add_argument('--output',default='runtime/runs/origin-representation-001')
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
    save(dr/'manifest.json',dict(design=d,config=cfg,source_bundle=bundle,source_sha256=digest(bundle),created_unix=time.time(),python=sys.version))
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as response:metadata=json.load(response)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
    for i in range(12):run_cell(client,d,i,dr);print(f'Completed cell {i}; total=12',flush=True)
    print('Worker completed.',flush=True)

if __name__=='__main__':main()
