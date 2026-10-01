"""216-call finite-grid comprehension diagnostic. No peers or hidden labels."""
import argparse
import itertools
import json
import random
import sys
import time
import urllib.request
from pathlib import Path
from .e1 import Client,canonical,digest,save
from .dependency_probe import check_server,sources
from .native import verify
from .routing_probe import SYSTEM as CURRENT
from .semantic_probe import CLARIFICATION

LAYERED=CURRENT[:-len(CLARIFICATION)]
SINGLE=(
    'Infer binary state Y with prior P(Y=1)=0.5. There is one binary observation '
    'X. It equals Y with probability effective_reliability, symmetrically for '
    'Y=0 and Y=1. The value field is the observed X. Return only JSON with '
    'answer (integer 0 or 1), p_state_1 (number from 0 to 1), and citations '
    '(list of visible observation IDs).'
)
EXPLICIT=(
    ' The value field is an observed measurement, not the unknown state Y. '
    'answer is your estimate of Y, not an estimate of whether the measurement '
    'is reliable. p_state_1 always means P(Y=1 given the visible observation). '
    'It does not mean confidence in answer, probability that the measurement '
    'is correct, or probability that the source is reliable. Keep the event '
    'Y=1 fixed even when your answer is 0. Do not output hidden source states.'
)

def validate(d):
    if d['protocol']!='comprehension-probe-v1':raise ValueError('Protocol')
    if d['source_reliabilities']!=[.65,.8,.9] or d['observation_reliabilities']!=[.7,.85,.95]:raise ValueError('Frozen grid')
    if d['repetitions']!=3 or type(d['seed']) is not int:raise ValueError('Design')
    if not 0<=d['temperature']<=2 or not 0<d['top_p']<=1 or type(d['max_tokens']) is not int or d['max_tokens']<1:raise ValueError('Sampling')

def grid(d):return list(itertools.product(d['source_reliabilities'],d['observation_reliabilities']))

def prompt(r,t,value,representation,wording):
    if representation=='layered':
        system=LAYERED
        obs={'id':'O0','source':'R0','value':value,'source_reliability':r,'observation_reliability':t}
    elif representation=='effective':
        system=SINGLE
        obs={'id':'O0','value':value,'effective_reliability':round(r*t+(1-r)*(1-t),12)}
    else:raise ValueError('Representation')
    if wording not in ('current','explicit'):raise ValueError('Wording')
    return [{'role':'system','content':system+(CLARIFICATION if wording=='current' else EXPLICIT)},
            {'role':'user','content':canonical({'observations':[obs]})}]

def run_cell(client,d,cell,directory=None):
    r,t=grid(d)[cell]
    order=list(itertools.product((0,1),('layered','effective'),('current','explicit'),range(3)))
    random.Random(f'comprehension-order:{d["seed"]}:{cell}').shuffle(order)
    result={'cell':cell,'r':r,'t':t,'order':[list(x) for x in order],'outputs':{}}
    for value,rep,word,repeat in order:
        key=f'i{cell}-v{value}-{rep}-{word}-r{repeat}'
        seed=int(digest([d['protocol'],d['seed'],cell,repeat])[:7],16)
        result['outputs'][key]=client.call(key,prompt(r,t,value,rep,word),seed,{'O0'})
    if directory is not None:save(directory/f'result-{cell}.json',result)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--design',default='configs/comprehension_probe_v1.json')
    p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json');p.add_argument('--output',default='runtime/runs/comprehension-diagnostic-001')
    p.add_argument('--execute',action='store_true');a=p.parse_args()
    d=json.loads(Path(a.design).read_text(encoding='utf-8'));validate(d)
    print(json.dumps({'mode':'execute' if a.execute else 'plan_only','grid_cells':9,'calls':216,'design_sha256':digest(d)},indent=2),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text(encoding='utf-8'))
    if cfg.get('deployment')!='native':raise ValueError('Native only')
    cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')})
    root=Path(a.output);root.mkdir(parents=True,exist_ok=False);dr=root/'shard-000';dr.mkdir();bundle=sources()
    save(dr/'manifest.json',{'design':d,'config':cfg,'source_bundle':bundle,'source_sha256':digest(bundle),'created_unix':time.time(),'python':sys.version})
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as r:metadata=json.load(r)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
    for cell in range(9):
        run_cell(client,d,cell,dr);print(f'Completed cell {cell}; total=9',flush=True)
    print('Worker completed; score with runtime.comprehension_score.',flush=True)

if __name__=='__main__':main()
