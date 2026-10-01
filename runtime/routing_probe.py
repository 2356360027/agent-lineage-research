"""Recipient routing engineering pilot with explicit correlated-source worlds."""
import argparse
import json
import random
import sys
import time
import urllib.request
from pathlib import Path
from .e1 import Client,canonical,digest,save
from .dependency_probe import check_server,sources
from .semantic_probe import CLARIFICATION
from .native import verify

CASES=['relay','same_source','recipient','mixed']
POLICIES=['raw','dedup','gate']
PRIMARY=['dedup_minus_raw_oracle_error','dedup_same_source_minus_relay_brier']
SYSTEM=(
    'Infer binary state Y with prior P(Y=1)=0.5. Each source s has one hidden '
    'binary state Z_s equal to Y with probability source_reliability, '
    'symmetrically for Y=0 and Y=1. Source states are conditionally independent '
    'given Y. Each distinct observation equals its source state Z_s with '
    'probability observation_reliability, symmetrically for both source states. '
    'Distinct observations from a source are conditionally independent given '
    'Z_s, but need not be independent given Y. Repeated observation IDs are '
    'copies of the same observation, not independent measurements. A new ID '
    'from the same source is a new measurement, not a duplicate. All relevant '
    'observations available to you are in the observations list. Peer judgments '
    'are advisory, may be wrong, and carry no additional source observations. '
    'Return only JSON with answer (integer 0 or 1), p_state_1 (number from 0 to 1), '
    'and citations (list of visible observation IDs).'
)+CLARIFICATION

def world(seed,item):
    rng=random.Random(f'correlated-source-v1:{seed}:{item}');y=rng.randrange(2);obs=[]
    for s in range(2):
        r=rng.choice([.65,.8,.9]);z=y if rng.random()<r else 1-y
        for j in range(2):
            t=rng.choice([.7,.85,.95]);v=z if rng.random()<t else 1-z
            obs.append({'id':f'O{2*s+j}','source':f'R{s}','value':v,
                'source_reliability':r,'observation_reliability':t})
    return obs,y

def unique(observations):
    seen={}
    for o in observations:
        if o['id'] in seen and seen[o['id']]!=o:raise ValueError('Conflicting observation ID')
        seen[o['id']]=o
    return list(seen.values())

def oracle(observations):
    """Exact marginalization over source states; scoring only, never prompts."""
    groups={}
    for o in unique(observations):groups.setdefault(o['source'],[]).append(o)
    likelihood=[]
    for y in (0,1):
        p=1.
        for group in groups.values():
            r=group[0]['source_reliability']
            if any(o['source_reliability']!=r for o in group):raise ValueError('Source conflict')
            total=0.
            for z in (0,1):
                term=r if z==y else 1-r
                for o in group:
                    t=o['observation_reliability'];term*=t if o['value']==z else 1-t
                total+=term
            p*=total
        likelihood.append(p)
    return likelihood[1]/sum(likelihood)

def batches(obs,case,recipient):
    o0,o1=obs[:2]
    # A already knows O0; B initially knows O2. Each scenario resets its history.
    if case=='relay':return [[o0],[o0,o0]]
    if case=='same_source':return [[o0],[o1]]
    if case=='recipient':return [[o0] if recipient==0 else [],[o0]]
    if case=='mixed':return [[o0],[o0,o1]]
    raise ValueError('Unknown case')

def ledger_route(initial,incoming):
    """Reference recipient ledger: stable IDs are assumed supplied and correct."""
    ledger={o['id']:o for o in initial}
    for bundle in incoming:
        for o in bundle:
            if o['id'] in ledger and ledger[o['id']]!=o:raise ValueError('Conflicting ID')
            ledger.setdefault(o['id'],o)
    return list(ledger.values())

def cpu_audit():
    checks=0;source_loss=0;global_loss=0
    for item in range(100):
        obs,_=world(880173,item)
        for receiver in (0,1):
            initial=[obs[2*receiver]]
            for case in CASES:
                events=batches(obs,case,receiver)
                for stage in (1,2):
                    flat=initial+sum(events[:stage],[])
                    if unique(flat)!=ledger_route(initial,events[:stage]):raise AssertionError('Ledger differs')
                    checks+=1
            # Source-level filter would lose the new O1 after O0 for recipient A.
            if receiver==0 and obs[1]['source']==initial[0]['source']:
                source_loss+=1
        # Globally seen O0 by A is still unseen for B initially.
        if obs[0]['id'] not in {obs[2]['id']}:global_loss+=1
    return {'status':'CPU_RULE_AUDIT_ONLY','equivalence_checks':checks,
        'recipient_ledger_equals_strong_ID_dedup':True,
        'same_source_new_unit_loss_counterexamples':source_loss,
        'globally_seen_but_unseen_to_recipient_counterexamples':global_loss,
        'decision':'Collapse ledger and strong dedup into one GPU policy. No separate novelty claim.'}

def validate(d):
    if d['protocol']!='routing-probe-v1' or d['primary_endpoints']!=PRIMARY:raise ValueError('Protocol')
    for k in ('items','max_tokens','bootstrap_resamples'):
        if type(d[k]) is not int or d[k]<1:raise ValueError(k)
    for k in ('world_seed','bootstrap_seed'):
        if type(d[k]) is not int:raise ValueError(k)
    if not 0<=d['temperature']<=2 or not 0<d['top_p']<=1:raise ValueError('Sampling')

def seed(d,item,role):return int(digest([d['protocol'],d['world_seed'],item,role])[:7],16)

def prompt(obs,previous=None,verdict=None):
    body={'observations':obs}
    if previous is not None:
        body['your_previous_assessment']=previous
        body['peer_assessments']=[] if verdict is None else [{'agent':'peer0','answer':verdict},{'agent':'peer1','answer':verdict}]
    return [{'role':'system','content':SYSTEM},{'role':'user','content':canonical(body)}]

def run_item(client,d,item,directory=None):
    obs,_=world(d['world_seed'],item)
    initial=[client.call(f'i{item}-initial-{r}',prompt([obs[2*r]]),seed(d,item,f'initial-{r}'),{obs[2*r]['id']}) for r in (0,1)]
    jobs=[(case,p,r) for case in CASES for p in POLICIES for r in (0,1)]
    random.Random(seed(d,item,'order')).shuffle(jobs)
    result={'item':item,'observations':obs,'initial':initial,'order':[list(j) for j in jobs],'trajectories':{}}
    for case,policy,r in jobs:
        history=[obs[2*r]];previous=initial[r];states=[]
        v=(item+r)%2 # Balanced assigned verdict, independent of gold; not natural peers.
        for stage,event in enumerate(batches(obs,case,r),1):
            history+=event
            visible=history if policy=='raw' else unique(history)
            verdict=None if policy=='gate' and stage==1 else v
            previous=client.call(f'i{item}-{case}-{policy}-r{r}-s{stage}',prompt(visible,previous,verdict),
                seed(d,item,f'receiver-{r}-stage-{stage}'),{o['id'] for o in visible})
            states.append({'visible_observations':list(visible),'assessment':previous})
        result['trajectories'][f'{case}-{policy}-{r}']=states
    if directory is not None:save(directory/f'result-{item}.json',result)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--design',default='configs/routing_probe_v1.json')
    p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json');p.add_argument('--output',default='runtime/runs/routing-pilot-001')
    p.add_argument('--execute',action='store_true');p.add_argument('--cpu-audit',action='store_true');a=p.parse_args()
    if a.cpu_audit:print(json.dumps(cpu_audit(),indent=2));return
    d=json.loads(Path(a.design).read_text(encoding='utf-8'));validate(d)
    print(json.dumps({'mode':'execute' if a.execute else 'plan_only','worlds':d['items'],
        'calls':50*d['items'],'design_sha256':digest(d)},indent=2),flush=True)
    if not a.execute:return
    cfg=json.loads(Path(a.native_config).read_text(encoding='utf-8'))
    if cfg.get('deployment')!='native':raise ValueError('Native config required')
    cfg.update({k:d[k] for k in ('temperature','top_p','max_tokens')})
    root=Path(a.output);root.mkdir(parents=True,exist_ok=False);dr=root/'shard-000';dr.mkdir();bundle=sources()
    save(dr/'manifest.json',{'design':d,'config':cfg,'source_bundle':bundle,'source_sha256':digest(bundle),
        'created_unix':time.time(),'python':sys.version,'cpu_audit':cpu_audit()})
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as r:metadata=json.load(r)
    check_server(cfg,metadata);save(dr/'server_metadata.json',metadata)
    c=Client(cfg,'http://127.0.0.1:8000/v1',dr)
    for item in range(d['items']):
        run_item(c,d,item,dr);print(f'Completed item {item}; total={d["items"]}',flush=True)
    print('Worker completed. Score with runtime.routing_score.',flush=True)

if __name__=='__main__':main()
