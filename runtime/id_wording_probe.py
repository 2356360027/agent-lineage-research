"""Frozen fresh-cell diagnostic, not DICE efficacy or a competence-gate repair.

12 fresh conflict pairs x 2 complements x 3 identifier families x 2 ID
assignments x 2 wordings x 2 independently seeded draws = 576 calls.
Additional numeric-ID greedy panel: 12 x 2 x 2 x 2 = 96 calls.
Physical observation 0 is stronger; position is balanced across 12 cells,
held identical within every naming/wording/repetition contrast. Position
is NOT fully crossed within cells, so no causal position effect is claimed.
The correction ONLY replaces the singular conditioning-event phrase.

Primary descriptive endpoints, specified before inference:
1. Numeric-ID mean absolute probability change under ID swap, by wording.
2. Revised-minus-legacy MAE, numeric-ID panel (paired, averaged by cell).
Secondary: two neutral-name controls, signed directional ID preference,
same-prompt between-seed probability differences, greedy robustness.
Absolute ID deltas include sampling variation; no causal noise subtraction.
All cells/invalid outputs retained; no success gate, significance claim,
retries, adaptive sampling, or reuse of old outcomes. Repeated draws do not
increase the number of independent tasks (12). Old results remain intact.
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

from .dependency_probe import check_server, sources
from .e1 import canonical, digest, save
from .format_probe_v2 import Client, Audit
from .native import verify
from .probe_score import check, diagnostics
from .readiness_pipeline import cells as old_cells, evaluate, oracle, prompt as old_prompt

FAMILIES = [['O0','O1'], ['oak','pine'], ['cedar','birch']]
OLD_PHRASE = 'P(Y=1 given the visible observation)'
NEW_PHRASE = 'P(Y=1 conditioned on all observations in the observations list)'


def validate(d):
    expected = dict(protocol='id-wording-v1', seed=48172639, cells=12,
                    repetitions=2, temperature=.7, top_p=.9, max_tokens=256,
                    families=FAMILIES, calls=672)
    check(canonical(d) == canonical(expected), 'Frozen design changed')


def tasks(d):
    parent = dict(protocol='readiness-pipeline-v1', seed=25108369)
    excluded = {tuple(sorted(c['reliabilities'])) for stage in ('double','communication')
                for c in old_cells(parent, stage)}
    rng = random.Random(f'{d["protocol"]}:{d["seed"]}:fresh')
    result = []
    while len(result) < d['cells']:
        a,b = sorted(rng.sample(range(620,951),2))
        pair = (a/1000,b/1000)
        if b-a < 80 or pair in excluded:
            continue
        excluded.add(pair)
        result.append(dict(reliabilities=[b/1000,a/1000], base_values=[0,1], regime='conflict'))
    return result


def prompt(cell, i, flip, family, assignment, wording):
    p = old_prompt(cell, flip, 'double', 'hidden')
    if wording == 'revised':
        check(p[0]['content'].count(OLD_PHRASE) == 1, 'Parent prompt drift')
        p[0]['content'] = p[0]['content'].replace(OLD_PHRASE, NEW_PHRASE)
    else:
        check(wording == 'legacy', 'Wording')
    payload = json.loads(p[1]['content'])
    for k,o in enumerate(payload['observations']):
        o['id'] = FAMILIES[family][k ^ assignment]
    if i % 2:
        payload['observations'].reverse()
    p[1]['content'] = canonical(payload)
    return p


def schedule(d,i):
    entries = [('sample',f,n,a,w,r) for f,n,a,w,r in itertools.product(
        (0,1),range(3),(0,1),('legacy','revised'),range(2))]
    entries += [('greedy',f,0,a,w,0) for f,a,w in itertools.product(
        (0,1),(0,1),('legacy','revised'))]
    random.Random(f'{d["seed"]}:{i}:schedule').shuffle(entries)
    return entries


def strict_evaluate(raw,arm,model,visible):
    result=evaluate(raw,arm,model,visible)
    # The legacy evaluator accepts O0 first; neutral-ID arms must NOT inherit
    # that allowlist. Validate the actual per-request IDs even on success.
    if result['format_valid'] and any(x not in visible for x in result['assessment']['citations']):
        return dict(format_valid=False,assessment=None,violation='Invalid citations')
    return result


def run_cell(clients,d,i,directory=None):
    cell=tasks(d)[i]; entries=schedule(d,i)
    result=dict(cell=i,task=cell,schedule=[list(x) for x in entries],outputs={})
    for mode,flip,family,assignment,wording,repeat in entries:
        key=f'i{i}-{mode}-f{flip}-n{family}-a{assignment}-{wording}-r{repeat}'
        seed=int(digest([d['protocol'],d['seed'],i,repeat])[:7],16)
        client=clients[mode]
        client.evaluator=partial(strict_evaluate,visible=set(FAMILIES[family]))
        result['outputs'][key]=client.call(key,prompt(cell,i,flip,family,assignment,wording),seed,'joint')
    if directory is not None:
        save(directory/f'result-{i}.json',result)
    return result


def configs(cfg,d):
    sample=dict(cfg,**{k:d[k] for k in ('temperature','top_p','max_tokens')})
    return dict(sample=sample,greedy=dict(sample,temperature=0.0))


def stats(rows):
    valid=[r for r in rows if r['format_valid']]
    return dict(scheduled=len(rows),valid=len(valid),invalid=len(rows)-len(valid),
                **{k:mean(r[k] for r in valid) if valid else None
                   for k in ('mae','direction','probability_direction','probability')})


def pair_summary(rows,field):
    valid=[r for r in rows if r['valid']]
    by_cell={str(i):mean(r[field] for r in valid if r['cell']==i)
             for i in sorted({r['cell'] for r in valid})}
    return dict(scheduled_pairs=len(rows),valid_pairs=len(valid),
                mean_among_valid=mean(r[field] for r in valid) if valid else None,
                per_cell=by_cell)


def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text(encoding='utf-8'))
    d=m['design'];validate(d)
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    cfgs=m['configs'];check(cfgs==configs(cfgs['sample'],d),'Config mismatch')
    check_server(cfgs['sample'],json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==672 and len(list(dr.glob('result-*.json')))==12,'Incomplete/extra records')
    audits={k:Audit(dr,v) for k,v in cfgs.items()};rows=[];pairs=[]
    for i,cell in enumerate(tasks(d)):
        result=json.loads((dr/f'result-{i}.json').read_text(encoding='utf-8'))
        check(result==run_cell(audits,d,i),'Replay mismatch');index={}
        for mode,flip,family,assignment,wording,repeat in result['schedule']:
            key=f'i{i}-{mode}-f{flip}-n{family}-a{assignment}-{wording}-r{repeat}'
            out=result['outputs'][key]
            obs=json.loads(prompt(cell,i,flip,family,assignment,wording)[1]['content'])['observations']
            q=oracle(obs)
            row=dict(cell=i,mode=mode,flip=flip,family=family,assignment=assignment,
                     wording=wording,repeat=repeat,stronger_last=i%2,oracle_p=q,**out)
            if out['format_valid']:
                a=out['assessment'];p=a['p_state_1']
                row.update(probability=p,mae=abs(p-q),direction=int(a['answer']==int(q>.5)),
                           probability_direction=int(p!=.5 and (p>.5)==(q>.5)))
            rows.append(row);index[mode,flip,family,assignment,wording,repeat]=row
        for key,a in index.items():
            mode,flip,family,assignment,wording,repeat=key
            for kind,other in (
                ('id',(mode,flip,family,1,wording,repeat) if assignment==0 else None),
                ('wording',(mode,flip,family,assignment,'revised',repeat) if wording=='legacy' else None),
                ('noise',(mode,flip,family,assignment,wording,1) if mode=='sample' and repeat==0 else None)):
                if other is None:continue
                b=index[other];valid=a['format_valid'] and b['format_valid']
                pairs.append(dict(cell=i,kind=kind,mode=mode,family=family,wording=wording,
                    valid=valid,absolute_probability_delta=abs(b['probability']-a['probability']) if valid else None,
                    mae_delta=b['mae']-a['mae'] if valid else None,
                    direction_delta=b['direction']-a['direction'] if valid else None))
    contrasts={}
    for mode in ('sample','greedy'):
        for family in (range(3) if mode=='sample' else (0,)):
            selected=[r for r in pairs if r['mode']==mode and r['family']==family]
            prefix=f'{mode}/family{family}'
            for wording in ('legacy','revised'):
                for kind in ('id','noise'):
                    rs=[r for r in selected if r['wording']==wording and r['kind']==kind]
                    if rs:
                        contrasts[f'{prefix}/{wording}/{kind}']=pair_summary(rs,'absolute_probability_delta')
                        if kind=='id':contrasts[f'{prefix}/{wording}/id_direction']=pair_summary(rs,'direction_delta')
            contrasts[f'{prefix}/revised_minus_legacy_mae']=pair_summary([r for r in selected if r['kind']=='wording'],'mae_delta')
    calls=[c for a in audits.values() for c in a.calls]
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int and c['usage'][k]>=0
                 for k in ('prompt_tokens','completion_tokens')) for c in calls)
    report=dict(status='FRESH_CELL_WORDING_ID_DIAGNOSTIC',calls=len(calls),independent_cells=12,
        overall=stats(rows),groups={f'{mode}/family{n}/{w}':stats([r for r in rows if (r['mode'],r['family'],r['wording'])==(mode,n,w)])
          for mode in ('sample','greedy') for n in (range(3) if mode=='sample' else (0,)) for w in ('legacy','revised')},
        contrasts=contrasts,primary_keys=['sample/family0/legacy/id','sample/family0/revised/id','sample/family0/revised_minus_legacy_mae'],
        usage_complete=complete,prompt_tokens=sum(c['usage']['prompt_tokens'] for c in calls) if complete else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in calls) if complete else None,
        sum_call_seconds=sum(c['wall_seconds'] for c in calls),
        warning='12 fresh finite-grid cells, not 672 independent tasks. No DICE efficacy, population significance, or internal mechanism claim. Neutral names are controls, not guaranteed unbiased. Position balanced between cells only. Absolute deltas include sampling noise. Valid-only summaries may be selected; invalids retained. Greedy panel numeric IDs only. Old failures unchanged.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return report,rows,pairs,hashes


def main():
    p=argparse.ArgumentParser();p.add_argument('--design',default='configs/id_wording_v1.json')
    p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json')
    p.add_argument('--output',default='runtime/runs/id-wording-001')
    for name in ('execute','score','audit-only'):p.add_argument('--'+name,action='store_true')
    a=p.parse_args();root=Path(a.output);check(sum([a.execute,a.score,a.audit_only])<=1,'Choose one action')
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2));return
    if a.score:
        objects=summarize(root)
        for name,obj in zip(('summary','cell_metrics','paired_contrasts','audit_input_hashes'),objects):save(root/(name+'.json'),obj)
        print(json.dumps(objects[0],indent=2));return
    d=json.loads(Path(a.design).read_text());validate(d)
    print(json.dumps(dict(mode='execute' if a.execute else 'plan_only',calls=672,design_sha256=digest(d))),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text());check(cfg.get('deployment')=='native','Native only')
    cfgs=configs(cfg,d);root.mkdir(parents=True,exist_ok=False);dr=root/'shard-000';dr.mkdir();bundle=sources()
    save(dr/'manifest.json',dict(design=d,configs=cfgs,source_bundle=bundle,source_sha256=digest(bundle),created_unix=time.time(),python=sys.version))
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as response:metadata=json.load(response)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    clients={k:Client(v,'http://127.0.0.1:8000/v1',dr) for k,v in cfgs.items()}
    for i in range(12):run_cell(clients,d,i,dr);print(f'Completed cell {i}; total=12',flush=True)
    print('Worker completed.',flush=True)


if __name__=='__main__':main()
