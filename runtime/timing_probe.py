"""Communication-timing component pilot. Synthetic evidence, real model calls."""
import argparse
import json
import random
import sys
import time
import urllib.request
from pathlib import Path
from .e1 import Client, canonical, digest, save, world
from .dependency_probe import SYSTEM, check_server, sources
from .semantic_probe import CLARIFICATION
from .native import verify

PRIMARY = ['gate_minus_open_oracle_error','evidence_new_minus_repeat_brier']
STAGES = ['open_zero','open_one','gate_zero','gate_one','evidence']

def validate(d):
    if d['protocol']!='timing-probe-v1' or d['primary_endpoints']!=PRIMARY:
        raise ValueError('Unsupported protocol/endpoints')
    for k in ('items','max_tokens','bootstrap_resamples'):
        if type(d[k]) is not int or d[k]<1:raise ValueError(k)
    for k in ('world_seed','bootstrap_seed'):
        if type(d[k]) is not int:raise ValueError(k)
    if not 0<=d['temperature']<=2 or not 0<d['top_p']<=1:raise ValueError('sampling')

def seed(d,item,role):
    return int(digest([d['protocol'],d['world_seed'],item,role])[:7],16)

def prompt(observations, previous=None, verdict=None):
    payload={'observations':observations}
    if previous is not None:
        payload['your_previous_assessment']=previous
        payload['peer_assessments']=[] if verdict is None else [
            {'agent':f'peer{k}','answer':verdict} for k in range(2)]
    return [{'role':'system','content':SYSTEM+CLARIFICATION},
            {'role':'user','content':canonical(payload)}]

def run_item(client,d,item,directory=None):
    obs,_=world(d['world_seed'],item);focal=item%3
    initial=client.call(f'i{item}-initial',prompt([obs[focal]]),seed(d,item,'initial'),{obs[focal]['id']})
    evidence={'repeat':[obs[focal]]*3,
              'new':[obs[focal]]+[o for k,o in enumerate(obs) if k!=focal]}
    order=list(evidence);random.Random(seed(d,item,'regime-order')).shuffle(order)
    result={'item':item,'initial':initial,'regime_order':order,'regimes':{}}
    for regime in order:
        visible={o['id'] for o in evidence[regime]}
        early_order=['open_zero','open_one','hidden']
        random.Random(seed(d,item,regime+'-early-order')).shuffle(early_order)
        early={}
        for arm in early_order:
            v=None if arm=='hidden' else int(arm.endswith('one'))
            early[arm]=client.call(f'i{item}-{regime}-early-{arm}',
                prompt(evidence[regime],initial,v),seed(d,item,'early'),visible)
        final_order=STAGES.copy()
        random.Random(seed(d,item,regime+'-final-order')).shuffle(final_order)
        final={}
        for arm in final_order:
            previous=early[arm] if arm.startswith('open') else early['hidden']
            v=None if arm=='evidence' else int(arm.endswith('one'))
            final[arm]=client.call(f'i{item}-{regime}-final-{arm}',
                prompt(evidence[regime],previous,v),seed(d,item,'final'),visible)
        result['regimes'][regime]={'observations':evidence[regime],
            'early_order':early_order,'final_order':final_order,'early':early,'final':final}
    if directory is not None:save(directory/f'result-{item}.json',result)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--design',default='configs/timing_probe_v1.json')
    p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json')
    p.add_argument('--output',default='runtime/runs/timing-pilot-001')
    p.add_argument('--execute',action='store_true');a=p.parse_args()
    d=json.loads(Path(a.design).read_text(encoding='utf-8'));validate(d)
    print(json.dumps({'mode':'execute' if a.execute else 'plan_only','worlds':d['items'],
        'calls':17*d['items'],'design_sha256':digest(d)},indent=2),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text(encoding='utf-8'))
    if cfg.get('deployment')!='native':raise ValueError('Native config required')
    cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')})
    root=Path(a.output);root.mkdir(parents=True,exist_ok=False)
    directory=root/'shard-000';directory.mkdir();bundle=sources()
    save(directory/'manifest.json',{'design':d,'config':cfg,'source_bundle':bundle,
        'source_sha256':digest(bundle),'created_unix':time.time(),'python':sys.version})
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as r:metadata=json.load(r)
    check_server(cfg,metadata);save(directory/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',directory)
    for item in range(d['items']):
        run_item(client,d,item,directory)
        print(f'Completed item {item}; total={d["items"]}',flush=True)
    print('Worker completed; score with runtime.timing_score.',flush=True)

if __name__=='__main__':main()
