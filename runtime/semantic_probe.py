"""Fixed-size exploratory probability-semantics diagnostic; real calls only."""
import argparse
import json
import random
import sys
import time
import urllib.request
from pathlib import Path
from .e1 import Client, canonical, digest, messages, save, world
from .dependency_probe import SYSTEM, check_server, sources
from .native import verify

ARMS = [f'{s}_{v}' for s in ('legacy', 'explicit') for v in ('hidden', 'zero', 'one')]
CLARIFICATION = (
    ' p_state_1 means the probability that the fixed event Y=1 is true, '
    'regardless of which answer you choose. It is NOT confidence in your chosen '
    'answer. For example, probability 0.8 for Y=0 means p_state_1=0.2; '
    'probability 0.8 for Y=1 means p_state_1=0.8. These examples only define '
    'the field and are not evidence about this task.'
)

def validate(d):
    if d['protocol'] != 'semantic-probe-v1': raise ValueError('Unknown protocol')
    for k in ('items', 'repetitions', 'max_tokens', 'bootstrap_resamples'):
        if type(d[k]) is not int or d[k] < 1: raise ValueError(k)
    for k in ('world_seed', 'bootstrap_seed'):
        if type(d[k]) is not int: raise ValueError(k)
    if not 0 <= d['temperature'] <= 2 or not 0 < d['top_p'] <= 1: raise ValueError('sampling')
    if d['primary_endpoints'] != ['explicit_verdict_sensitivity', 'explicit_minus_legacy_sensitivity']:
        raise ValueError('Endpoints changed')

def seed(d, item, rep, role):
    return int(digest([d['protocol'], d['world_seed'], item, rep, role])[:7], 16)

def prompt(obs, previous, arm):
    style, verdict = arm.split('_')
    if arm not in ARMS: raise ValueError('Unknown arm')
    peers = [] if verdict == 'hidden' else [
        {'agent': f'peer{k}', 'answer': int(verdict == 'one')} for k in range(2)]
    return [{'role':'system', 'content':SYSTEM + (CLARIFICATION if style == 'explicit' else '')},
            {'role':'user', 'content':canonical({'observations':obs,
             'your_previous_assessment':previous, 'peer_assessments':peers})}]

def schedule(d, item):
    result = [(r,a) for r in range(d['repetitions']) for a in ARMS]
    random.Random(seed(d,item,-1,'schedule')).shuffle(result)
    return result

def run_item(client, d, item, directory):
    obs, _ = world(d['world_seed'],item)
    focal = item % 3
    evidence = [obs[focal]] + [o for k,o in enumerate(obs) if k != focal]
    previous = client.call(f'i{item}-initial', messages([obs[focal]]),
                           seed(d,item,-1,'initial'), {obs[focal]['id']})
    results = {str(r):{} for r in range(d['repetitions'])}
    for rep,arm in schedule(d,item):
        results[str(rep)][arm] = client.call(f'i{item}-r{rep}-{arm}',
            prompt(evidence,previous,arm),seed(d,item,rep,'paired'),{o['id'] for o in evidence})
    result = {'item':item,'observations':evidence,'previous':previous,
              'schedule':schedule(d,item),'results':results}
    save(directory/f'result-{item}.json',result)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--design',default='configs/semantic_probe_v1.json')
    p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json')
    p.add_argument('--output',default='runtime/runs/semantic-diagnostic-001')
    p.add_argument('--execute',action='store_true')
    args=p.parse_args()
    d=json.loads(Path(args.design).read_text(encoding='utf-8'));validate(d)
    print(json.dumps({'mode':'execute' if args.execute else 'plan_only',
        'worlds':d['items'],'calls':d['items']*(1+6*d['repetitions']),
        'design_sha256':digest(d)},indent=2),flush=True)
    if not args.execute:return
    cfg=json.loads(Path(args.native_config).read_text(encoding='utf-8'))
    if cfg.get('deployment')!='native':raise ValueError('Native config required')
    cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')})
    root=Path(args.output)
    root.mkdir(parents=True,exist_ok=False)  # Never overwrite/resume a diagnostic implicitly.
    directory=root/'shard-000';directory.mkdir()
    bundle=sources()
    save(directory/'manifest.json',{'design':d,'config':cfg,'source_bundle':bundle,
        'source_sha256':digest(bundle),'created_unix':time.time(),'python':sys.version})
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as r:
        metadata=json.load(r)
    check_server(cfg,metadata);save(directory/'server_metadata.json',metadata)
    client=Client(cfg,'http://127.0.0.1:8000/v1',directory)
    for item in range(d['items']):
        run_item(client,d,item,directory)
        print(f'Completed item {item}; total={d["items"]}',flush=True)
    print('Completed. Score separately with runtime.semantic_score.',flush=True)

if __name__=='__main__':main()
