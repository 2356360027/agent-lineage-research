"""Exploratory order-by-ID intervention on all six prior conflict cells."""
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

from .dependency_probe import check_server,sources
from .e1 import canonical,digest,save
from .format_probe_v2 import Client,Audit
from .native import verify
from .probe_score import check,diagnostics
from .readiness_pipeline import cells,evaluate,observations,oracle,prompt as parent_prompt


def validate(d):
    expected={'protocol':'order-id-probe-v1','parent_seed':25108369,'conflict_cells':[1,3,5,7,9,11],
              'seed':36712908,'repetitions':3,'temperature':.7,'top_p':.9,'max_tokens':256}
    check(canonical(d)==canonical(expected),'Frozen design changed')


def tasks(d):
    parent={'protocol':'readiness-pipeline-v1','seed':d['parent_seed']}
    return [cells(parent,'double')[i] for i in d['conflict_cells']]


def prompt(cell,flip,reverse,swap_ids):
    p=parent_prompt(cell,flip,'double','hidden'); payload=json.loads(p[1]['content'])
    obs=payload['observations']
    if swap_ids:
        for o in obs:o['id']='O1' if o['id']=='O0' else 'O0'
    if reverse:obs.reverse()
    p[1]['content']=canonical(payload)
    return p


def run_cell(client,d,i,directory=None):
    cell=tasks(d)[i]; schedule=list(itertools.product((0,1),(0,1),(0,1),range(3)))
    random.Random(f'{d["seed"]}:{i}:order').shuffle(schedule)
    result={'cell':i,'parent_cell':d['conflict_cells'][i],'task':cell,'schedule':[list(x) for x in schedule],'outputs':{}}
    for flip,rev,ids,repeat in schedule:
        key=f'i{i}-f{flip}-o{rev}-id{ids}-r{repeat}'
        seed=int(digest([d['protocol'],d['seed'],i,repeat])[:7],16)
        result['outputs'][key]=client.call(key,prompt(cell,flip,rev,ids),seed,'joint')
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result


def summarize(root):
    dr=root/'shard-000'; m=json.loads((dr/'manifest.json').read_text(encoding='utf-8')); d,cfg=m['design'],m['config'];validate(d)
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text(encoding='utf-8')),False)
    check(len(list(dr.glob('result-*.json')))==6 and len(list(dr.glob('i*.json')))==144,'Incomplete/extra records')
    audit=Audit(dr,cfg,partial(evaluate,visible={'O0','O1'}));rows=[];order_pairs=[];id_pairs=[]
    for i,cell in enumerate(tasks(d)):
        result=json.loads((dr/f'result-{i}.json').read_text(encoding='utf-8'))
        check(result==run_cell(audit,d,i),'Results mismatch');index={}
        stronger=0 if cell['reliabilities'][0]>cell['reliabilities'][1] else 1
        for flip,rev,ids,repeat in result['schedule']:
            out=result['outputs'][f'i{i}-f{flip}-o{rev}-id{ids}-r{repeat}'];q=oracle(observations(cell,flip))
            row={'cell':i,'parent_cell':d['conflict_cells'][i],'flip':flip,'reverse':rev,'swap_ids':ids,'repeat':repeat,
                 'stronger_last':int((stronger^rev)==1),'stronger_id':f'O{stronger^ids}','oracle_p':q,**out}
            if out['format_valid']:
                a=out['assessment'];p=a['p_state_1'];row.update(probability=p,mae=abs(p-q),
                    direction=int(a['answer']==int(q>.5)),probability_direction=int(p!=.5 and (p>.5)==(q>.5)),
                    follows_last=int(a['answer']==observations(cell,flip)[0 if rev else 1]['value']))
            rows.append(row);index[flip,rev,ids,repeat]=row
        for flip,ids,repeat in itertools.product((0,1),(0,1),range(3)):
            a,b=[index[flip,rev,ids,repeat] for rev in (0,1)];valid=a['format_valid'] and b['format_valid']
            first,last=(a,b) if not a['stronger_last'] else (b,a)
            order_pairs.append({'cell':i,'flip':flip,'swap_ids':ids,'repeat':repeat,'valid':valid,
                'absolute_order_probability_delta':abs(a['probability']-b['probability']) if valid else None,
                'stronger_last_minus_first_direction':last['direction']-first['direction'] if valid else None})
        for flip,rev,repeat in itertools.product((0,1),(0,1),range(3)):
            a,b=[index[flip,rev,ids,repeat] for ids in (0,1)];valid=a['format_valid'] and b['format_valid']
            id_pairs.append({'cell':i,'flip':flip,'reverse':rev,'repeat':repeat,'valid':valid,
                'absolute_id_probability_delta':abs(a['probability']-b['probability']) if valid else None})
    def stats(rs):
        valid=[r for r in rs if r['format_valid']]
        return {'scheduled':len(rs),'valid':len(valid),'invalid':len(rs)-len(valid),
                **{k:mean(r[k] for r in valid) if valid else None for k in ('mae','direction','probability_direction','follows_last')}}
    def paired(rs,keys):
        valid=[r for r in rs if r['valid']]
        return {'scheduled_pairs':len(rs),'valid_pairs':len(valid),**{k:mean(r[k] for r in valid) if valid else None for k in keys}}
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int and c['usage'][k]>=0 for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    report={'status':'EXPLORATORY_ORDER_ID_DIAGNOSTIC','calls':144,'parent_conflict_cells':6,
        'overall':stats(rows),'by_stronger_position':{str(v):stats([r for r in rows if r['stronger_last']==v]) for v in (0,1)},
        'by_stronger_id':{name:stats([r for r in rows if r['stronger_id']==name]) for name in ('O0','O1')},
        'primary_order_contrasts':paired(order_pairs,['absolute_order_probability_delta','stronger_last_minus_first_direction']),
        'secondary_id_contrast':paired(id_pairs,['absolute_id_probability_delta']),
        'usage_complete':complete,'prompt_tokens':sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,
        'completion_tokens':sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,
        'sum_call_seconds':sum(c['wall_seconds'] for c in audit.calls),
        'warning':'Posthoc-motivated exploratory intervention on all six earlier conflict cells, not independent validation or DICE efficacy. Same evidence/new seeds. Repeated draws are not independent tasks. Valid-only paired effects may have selection bias; report invalid counts. No significance or pass claim.'}
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.rglob('*.json'))}
    return report,rows,{'order':order_pairs,'id':id_pairs},hashes


def main():
    p=argparse.ArgumentParser();p.add_argument('--design',default='configs/order_probe_v1.json')
    p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json');p.add_argument('--output',default='runtime/runs/order-diagnostic-001')
    for name in ('execute','score','audit-only'):p.add_argument('--'+name,action='store_true')
    a=p.parse_args();root=Path(a.output);check(sum([a.execute,a.score,a.audit_only])<=1,'Choose one action')
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2));return
    if a.score:
        s,r,c,h=summarize(root)
        for name,obj in [('summary',s),('cell_metrics',r),('paired_contrasts',c),('audit_input_hashes',h)]:save(root/(name+'.json'),obj)
        print(json.dumps(s,indent=2));return
    d=json.loads(Path(a.design).read_text(encoding='utf-8'));validate(d)
    print(json.dumps({'mode':'execute' if a.execute else 'plan_only','calls':144,'design_sha256':digest(d)},indent=2),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text(encoding='utf-8'));check(cfg.get('deployment')=='native','Native only')
    cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')});root.mkdir(parents=True,exist_ok=False);dr=root/'shard-000';dr.mkdir();bundle=sources()
    save(dr/'manifest.json',{'design':d,'config':cfg,'source_bundle':bundle,'source_sha256':digest(bundle),'created_unix':time.time(),'python':sys.version})
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as response:metadata=json.load(response)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',dr,partial(evaluate,visible={'O0','O1'}))
    for i in range(6):run_cell(client,d,i,dr);print(f'Completed cell {i}; total=6',flush=True)
    print('Worker completed.',flush=True)


if __name__=='__main__':main()
