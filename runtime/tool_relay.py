"""Prospectively frozen minimal two-agent, tool-assisted relay diagnostic.

12 fresh conflict pairs x 2 complements x (sender extract+final, recipient
extract, four recipient finals) =168 model calls, no retries or selection.
Sender sees one real observation (strong/weak prospectively balanced), uses
its own extraction/calculator, and generates a real answer. Recipient sees
both observations and its own fixed calculator result. Conditions: hidden,
peer judgment, same judgment plus experiment-known sender input IDs, and a
repeat of the sender's already-seen observation with no judgment. All four
recipient calls are independent contexts, with identical seed and tool data.
This tests interference from redundant peer information, not novel evidence
acquisition, free-form debate, model-only competence, or DICE efficacy.
Primary paired contrasts: peer-hidden MAE; lineage-peer MAE. Secondary:
duplicate-hidden MAE, probability changes, errors by sender evidence quality.
12 task cells, not 168 independent tasks. No significance claim; failures and
missing sender outputs retained. No pooling or adaptive samples. Prior tool
readiness motivates this new pilot; recipient hidden competence is rechecked.
Provenance is known from our controlled sender input, not inferred from CoT.
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
from .dependency_probe import sources,check_server
from .e1 import canonical,digest,save
from .format_probe_v2 import Client,Audit
from .native import verify
from .probe_score import check,diagnostics
from .scaffold_probe import (tasks as scaffold_tasks,prompt as scaffold_prompt,
    outcome as base_outcome,calculate,stats)
from .stage_diagnostic import tasks as stage_tasks,bayes,metrics
from .id_wording_probe import tasks as id_tasks
from .readiness_pipeline import cells as initial_cells

CONDITIONS=('hidden','peer','lineage','duplicate')


def validate(d):
    check(d==dict(protocol='tool-relay-v1',seed=71405382,cells=12,temperature=0.,top_p=1.,max_tokens=768,calls=168),'Frozen design changed')


def tasks(d):
    old=scaffold_tasks(dict(protocol='likelihood-scaffold-v1',seed=60394271))
    old+=stage_tasks(dict(protocol='stage-diagnostic-v1',seed=59283147))
    old+=id_tasks(dict(protocol='id-wording-v1',seed=48172639,cells=12))
    for stage in ('double','communication'):old+=initial_cells(dict(protocol='readiness-pipeline-v1',seed=25108369),stage)
    excluded={tuple(sorted(c['reliabilities'])) for c in old};out=[];rng=random.Random(f'{d["protocol"]}:{d["seed"]}:fresh')
    while len(out)<12:
        a,b=sorted(rng.sample(range(620,951),2));pair=(a/1000,b/1000)
        if b-a<80 or pair in excluded:continue
        excluded.add(pair);out.append(dict(reliabilities=[b/1000,a/1000],base_values=[0,1],regime='conflict'))
    return out


def evidence(cell,i,flip):
    p=scaffold_prompt(cell,i,flip,0,'direct');obs=json.loads(p[1]['content'])['observations']
    sender_id='O'+str((i//2)%2)
    return obs,[o for o in obs if o['id']==sender_id]


def message(cell,i,flip,obs,arm,tool=None,peer=None,condition='hidden',sender_obs=None):
    p=scaffold_prompt(cell,i,flip,0,arm,tool)
    payload=json.loads(p[1]['content']);payload['observations']=obs
    if condition in ('peer','lineage'):
        payload['peer_assessments']=[peer]
        if condition=='lineage':
            payload['peer_evidence_provenance']={'agent':'A','input_observation_ids':[o['id'] for o in sender_obs],
                'all_sender_observations_already_in_recipient_input':True,
                'meaning':'This records the sender input, not its hidden reasoning.'}
    elif condition=='duplicate':payload['observations']=obs+[dict(sender_obs[0])]
    p[1]['content']=canonical(payload)
    return p


def outcome(raw,arm,model,visible):
    out=base_outcome(raw,arm,model)
    if out['format_valid'] and arm!='extract' and any(x not in visible for x in out['assessment']['citations']):
        return dict(format_valid=False,assessment=None,violation='Invisible citation')
    return out


def run_cell(client,d,i,directory=None):
    cell=tasks(d)[i];flips=[0,1];random.Random(f'{d["seed"]}:{i}:flips').shuffle(flips)
    result=dict(cell=i,task=cell,flips=flips,cases={});seed=int(digest([d['protocol'],d['seed'],i])[:7],16)
    for flip in flips:
        obs,sobs=evidence(cell,i,flip);case=dict(recipient_observations=obs,sender_observations=sobs)
        client.evaluator=partial(outcome,visible={o['id'] for o in sobs})
        ex=client.call(f'i{i}-f{flip}-sender-extract',message(cell,i,flip,sobs,'extract'),seed,'extract')
        tool=calculate(ex);sender=client.call(f'i{i}-f{flip}-sender-final',message(cell,i,flip,sobs,'calculator',tool),seed,'calculator')
        case.update(sender_extraction=ex,sender_tool=tool,sender=sender)
        peer={'agent':'A',**{k:sender['assessment'][k] for k in ('answer','p_state_1')}} if sender['format_valid'] else {'agent':'A','status':'unavailable_due_to_invalid_output'}
        case['peer_message']=peer
        client.evaluator=partial(outcome,visible={o['id'] for o in obs})
        rex=client.call(f'i{i}-f{flip}-recipient-extract',message(cell,i,flip,obs,'extract'),seed,'extract')
        rtool=calculate(rex);case.update(recipient_extraction=rex,recipient_tool=rtool)
        order=list(CONDITIONS);random.Random(f'{d["seed"]}:{i}:{flip}:conditions').shuffle(order)
        case['order']=order;case['outputs']={}
        for condition in order:
            p=message(cell,i,flip,obs,'calculator',rtool,peer,condition,sobs)
            case['outputs'][condition]=client.call(f'i{i}-f{flip}-recipient-{condition}',p,seed,'calculator')
        result['cases'][str(flip)]=case
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result


def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text());d=m['design'];cfg=m['config'];validate(d)
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==168 and len(list(dr.glob('result-*.json')))==12,'Incomplete/extra records')
    audit=Audit(dr,cfg);rows=[];pairs=[];sender_rows=[];extraction_correct=[]
    for i,cell in enumerate(tasks(d)):
        result=json.loads((dr/f'result-{i}.json').read_text());check(result==run_cell(audit,d,i),'Replay mismatch')
        for flip in result['flips']:
            case=result['cases'][str(flip)];obs=case['recipient_observations'];sobs=case['sender_observations'];q=bayes(obs)['p_state_1']
            s=case['sender'];sender_rows.append(dict(cell=i,flip=flip,stronger_sender=(i//2)%2==0,
                format_valid=s['format_valid'],agrees_with_full_evidence=int(s['assessment']['answer']==int(q>.5)) if s['format_valid'] else None))
            for name,expected in (('sender_extraction',sobs),('recipient_extraction',obs)):
                ex=case[name];extraction_correct.append(ex['format_valid'] and sorted(ex['assessment']['observations'],key=canonical)==sorted(expected,key=canonical))
            index={}
            for condition,out in case['outputs'].items():
                row=dict(cell=i,flip=flip,condition=condition,stronger_sender=(i//2)%2==0,**metrics(out,obs,'direct',.0001))
                rows.append(row);index[condition]=row
            for name,a,b in (('peer_minus_hidden','hidden','peer'),('lineage_minus_peer','peer','lineage'),('duplicate_minus_hidden','hidden','duplicate')):
                x,y=index[a],index[b];valid=x['format_valid'] and y['format_valid']
                pairs.append(dict(cell=i,flip=flip,contrast=name,stronger_sender=(i//2)%2==0,valid=valid,
                    mae_delta=y['mae']-x['mae'] if valid else None,
                    probability_delta=y['assessment']['p_state_1']-x['assessment']['p_state_1'] if valid else None,
                    answer_changed=int(x['assessment']['answer']!=y['assessment']['answer']) if valid else None))
    contrasts={}
    for name in ('peer_minus_hidden','lineage_minus_peer','duplicate_minus_hidden'):
        ps=[p for p in pairs if p['contrast']==name];vs=[p for p in ps if p['valid']]
        contrasts[name]=dict(scheduled_pairs=len(ps),valid_pairs=len(vs),
            mae_delta=mean(p['mae_delta'] for p in vs) if vs else None,
            absolute_probability_delta=mean(abs(p['probability_delta']) for p in vs) if vs else None,
            answer_changes=sum(p['answer_changed'] for p in vs),
            per_cell={str(i):mean(p['mae_delta'] for p in vs if p['cell']==i) for i in sorted({p['cell'] for p in vs})})
    hidden=[r for r in rows if r['condition']=='hidden'];gate={'all_valid':all(r['format_valid'] for r in hidden)}
    if gate['all_valid']:
        gate['mae']=mean(r['mae'] for r in hidden)<=.05
        for f,strong in itertools.product((0,1),(False,True)):
            group=[r for r in hidden if r['flip']==f and r['stronger_sender']==strong]
            gate[f'f{f}s{strong}_direction']=mean(r['direction'] for r in group)>=.95
            gate[f'f{f}s{strong}_probability_direction']=mean(r['probability_direction'] for r in group)>=.95
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int and c['usage'][k]>=0 for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    report=dict(status='TOOL_ASSISTED_TWO_AGENT_RELAY_PILOT',calls=len(audit.calls),independent_cells=12,
        recipient_groups={k:stats([r for r in rows if r['condition']==k]) for k in CONDITIONS},
        primary_contrasts=['peer_minus_hidden','lineage_minus_peer'],contrasts=contrasts,
        hidden_readiness=dict(passed=all(gate.values()),checks=gate),
        sender_groups={str(strong):dict(scheduled=12,valid=sum(s['format_valid'] for s in sender_rows if s['stronger_sender']==strong),
            agrees_with_full_evidence=sum(s['agrees_with_full_evidence'] or 0 for s in sender_rows if s['stronger_sender']==strong)) for strong in (False,True)},
        exact_extractions=sum(extraction_correct),expected_extractions=48,
        usage_complete=complete,prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='Tool-assisted minimal relay: recipient already has all task evidence and exact calculator output. Natural sender output, known input provenance. No novel evidence, unrestricted debate, DICE efficacy, population inference or model-only competence. Null effects do not prove independence. All failures retained.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return report,rows,{'contrasts':pairs,'senders':sender_rows},hashes


def main():
    p=argparse.ArgumentParser();p.add_argument('--design',default='configs/tool_relay_v1.json');p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json');p.add_argument('--output',default='runtime/runs/tool-relay-001')
    for name in ('execute','score','audit-only'):p.add_argument('--'+name,action='store_true')
    a=p.parse_args();root=Path(a.output);check(sum([a.execute,a.score,a.audit_only])<=1,'Choose one action')
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2));return
    if a.score:
        objects=summarize(root)
        for name,obj in zip(('summary','cell_metrics','paired_contrasts','audit_input_hashes'),objects):save(root/(name+'.json'),obj)
        print(json.dumps(objects[0],indent=2));return
    d=json.loads(Path(a.design).read_text());validate(d)
    print(json.dumps(dict(mode='execute' if a.execute else 'plan_only',calls=168,design_sha256=digest(d))),flush=True)
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
