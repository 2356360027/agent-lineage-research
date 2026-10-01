"""Frozen lineage-utility-v1 engineering/behavioral diagnostic, 480 real calls.

20 fresh conflicting reliability pairs x complements x copy/independent
worlds x (one LLM selector + five final policies). Same three message bodies
in copy and independent worlds; only experiment-authenticated origin map
changes. Third message aliases a strong/weak initial observation (balanced
across cells). Distinct origins are independent ONLY by this synthetic world
definition. Conditional-on-equal-value test grid, not a natural prevalence
estimate. No natural sender generation or learned provenance discovery.

Policies: transport-ID dedup and content dedup are deliberately incomplete
engineering controls, NOT strong research competitors. verified_root is
standard first-seen origin dedup; llm_root selects representatives from the
same supplied map; raw_correct_tool retains all messages with the exact tool
computed over unique roots (privileged deterministic reference, not LLM
reasoning). Every final prompt exposes the same GENERATIVE instructions and
the map restricted to rendered records. Tools compute solely on selected
visible measurement values; scoring posterior is never used for selection.

Primary: verified_root minus llm_root posterior MAE separately by relation;
raw_correct_tool minus verified_root MAE separately by relation. Secondary:
weak controls' errors, selector accuracy, tool/output disagreement, retained
new evidence vs collapsed-copy behavior. 20 independent task cells. No
significance claim/adaptive sample size/retries/imputation. Invalid selector
outputs produce explicit tool errors; final calls still run, all retained.
Correct root dedup is an engineering baseline, not a novel algorithm/DICE.
"""
import argparse
import copy
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
from .stage_diagnostic import bayes,number,metrics
from .scaffold_probe import stats,tasks as scaffold_tasks
from .stage_diagnostic import tasks as stage_tasks
from .id_wording_probe import tasks as id_tasks
from .readiness_pipeline import cells as initial_cells
from .tool_relay import tasks as relay_tasks
from .duplicate_control import tasks as duplicate_tasks,DESIGN as DUPLICATE_DESIGN

DESIGN=dict(protocol='lineage-utility-v1',seed=93627504,cells=20,calls=480,temperature=0.,top_p=1.,max_tokens=512)
POLICIES=('transport_id','content_key','verified_root','llm_root','raw_correct_tool')
RELATIONS=('copy','independent')
SYSTEM=('Infer binary state Y with prior P(Y=1)=0.5. Each unique originating measurement equals Y '
    'with its effective_reliability, symmetrically for Y=0 and Y=1. Different origin IDs are '
    'conditionally independent given Y in this experiment. Different message IDs need not be '
    'independent. The experiment_authenticated_origins map is supplied by the environment, not '
    'claimed by the message sender. Messages with the same origin ID are copies of ONE measurement. '
    'Messages with different origin IDs are separate measurements even if their value and reliability '
    'happen to match. The value is an observed measurement, not the hidden state Y. ')
FINAL=('An external calculator computes the likelihood product on its input_observations, treating '
    'each listed ID as independent. This arithmetic may be correct even when the input selection '
    'incorrectly counts an origin twice or discards independent evidence. Use the origin map and '
    'visible evidence to determine the posterior. If a calculator error is given, reason from the '
    'visible records. Return only one JSON object with exactly answer (integer 0 or 1), p_state_1 '
    '(number from 0 to 1, always P(Y=1|all unique visible originating measurements), NOT confidence '
    'in the selected answer), and citations (list of visible message IDs). Use at least six decimal '
    'places for calculated non-integers. No additional text.')
SELECT=('Select exactly one message for each distinct originating measurement using the supplied '
    'origin map. Keep the first encountered representative of each origin, preserving input order. '
    'Do not discard independent measurements merely because their contents match. Do not compute '
    'probabilities. Return only JSON with exactly selected_message_ids: a list of message IDs.')

def validate(d):check(d==DESIGN,'Frozen design changed')

def tasks(d):
    old=duplicate_tasks(DUPLICATE_DESIGN)+relay_tasks(dict(protocol='tool-relay-v1',seed=71405382))
    old+=scaffold_tasks(dict(protocol='likelihood-scaffold-v1',seed=60394271))
    old+=stage_tasks(dict(protocol='stage-diagnostic-v1',seed=59283147))
    old+=id_tasks(dict(protocol='id-wording-v1',seed=48172639,cells=12))
    for s in ('double','communication'):old+=initial_cells(dict(protocol='readiness-pipeline-v1',seed=25108369),s)
    excluded={tuple(sorted(c['reliabilities'])) for c in old};out=[];rng=random.Random(f'{d["protocol"]}:{d["seed"]}:fresh')
    while len(out)<20:
        a,b=sorted(rng.sample(range(620,951),2));pair=(a/1000,b/1000)
        if b-a<80 or pair in excluded:continue
        excluded.add(pair);out.append(dict(reliabilities=[b/1000,a/1000],base_values=[0,1],regime='conflict'))
    return out

def world(cell,i,flip,relation):
    obs=[dict(id=f'O{k}',value=v^flip,effective_reliability=r) for k,(r,v) in enumerate(zip(cell['reliabilities'],cell['base_values']))]
    if i%2:obs.reverse()
    chosen='O'+str((i//2)%2);incoming=dict(next(o for o in obs if o['id']==chosen),id='O2')
    obs.append(incoming);origin={o['id']:'R'+o['id'][1:] for o in obs}
    if relation=='copy':origin['O2']=origin[chosen]
    else:check(relation=='independent','Relation')
    return obs,origin

def route(obs,origin,policy):
    check(set(origin)=={o['id'] for o in obs},'Map coverage')
    seen={};out=[];receipts=[]
    for o in obs:
        key=o['id'] if policy=='transport_id' else (o['value'],o['effective_reliability']) if policy=='content_key' else origin[o['id']] if policy=='verified_root' else None
        check(key is not None,'Policy')
        payload=(o['value'],o['effective_reliability'])
        if key in seen:
            check(seen[key]==payload,'Conflicting payload for identity/origin')
            receipts.append(dict(id=o['id'],origin=origin[o['id']],action='suppressed'))
        else:seen[key]=payload;out.append(copy.deepcopy(o))
    return out,receipts

def tool_for(obs):
    q=bayes(obs)
    return dict(status='ok',input_observations=obs,method='independent_symmetric_likelihood_product_prior_0.5',**q) if q else dict(status='error',reason='Invalid or empty calculator selection')

def messages(obs,origin,selector=False,tool=None):
    payload=dict(observations=obs,experiment_authenticated_origins={o['id']:origin[o['id']] for o in obs})
    if not selector:payload['external_calculator']=tool
    return [dict(role='system',content=SYSTEM+(SELECT if selector else FINAL)),dict(role='user',content=canonical(payload))]

def evaluate(raw,arm,model,visible):
    check(isinstance(raw,dict) and raw.get('model')==model,'API/model mismatch')
    choices=raw.get('choices');check(isinstance(choices,list) and len(choices)==1 and isinstance(choices[0].get('message'),dict),'API envelope')
    def unique(pairs):
        obj={}
        for k,v in pairs:check(k not in obj,'Duplicate JSON key');obj[k]=v
        return obj
    try:
        check(choices[0]['finish_reason']=='stop','Truncated output')
        obj=json.loads(choices[0]['message']['content'],object_pairs_hook=unique)
        if arm=='select':
            check(isinstance(obj,dict) and set(obj)=={'selected_message_ids'},'Selection schema')
            ids=obj['selected_message_ids'];check(isinstance(ids,list) and all(isinstance(v,str) and v in visible for v in ids) and len(ids)==len(set(ids)),'Selection IDs')
        else:
            check(isinstance(obj,dict) and set(obj)=={'answer','p_state_1','citations'},'Final schema')
            check(type(obj['answer']) is int and obj['answer'] in (0,1) and number(obj['p_state_1']),'Answer/probability')
            check(isinstance(obj['citations'],list) and all(isinstance(v,str) and v in visible for v in obj['citations']),'Citations')
        return dict(format_valid=True,assessment=obj,violation=None)
    except (ValueError,TypeError,KeyError) as exc:return dict(format_valid=False,assessment=None,violation=str(exc))

def run_cell(client,d,i,directory=None):
    cell=tasks(d)[i];schedule=list(itertools.product((0,1),RELATIONS));random.Random(f'{d["seed"]}:{i}:schedule').shuffle(schedule)
    result=dict(cell=i,task=cell,cases={});seed=int(digest([d['protocol'],d['seed'],i])[:7],16)
    for flip,relation in schedule:
        obs,origin=world(cell,i,flip,relation);key=f'i{i}-f{flip}-{relation}';client.evaluator=partial(evaluate,visible={o['id'] for o in obs})
        selection=client.call(key+'-select',messages(obs,origin,True),seed,'select')
        order=list(POLICIES);random.Random(f'{key}:order').shuffle(order)
        case=dict(observations=obs,origins=origin,selection=selection,order=order,policies={})
        for policy in order:
            receipts=[]
            if policy=='llm_root':
                selected=[next(o for o in obs if o['id']==k) for k in selection['assessment']['selected_message_ids']] if selection['format_valid'] else []
                rendered=selected or obs;tool=tool_for(selected)
            elif policy=='raw_correct_tool':
                selected,receipts=route(obs,origin,'verified_root');rendered=obs;tool=tool_for(selected)
            else:selected,receipts=route(obs,origin,policy);rendered=selected;tool=tool_for(selected)
            client.evaluator=partial(evaluate,visible={o['id'] for o in rendered})
            final=client.call(key+'-'+policy,messages(rendered,origin,tool=tool),seed,'final')
            case['policies'][policy]=dict(selected=selected,rendered=rendered,receipts=receipts,tool=tool,final=final)
        result['cases'][f'{flip}-{relation}']=case
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result

def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text());d=m['design'];cfg=m['config'];validate(d)
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==480 and len(list(dr.glob('result-*.json')))==20,'Incomplete/extra records')
    audit=Audit(dr,cfg);rows=[];pairs=[];sels=[]
    for i,cell in enumerate(tasks(d)):
        res=json.loads((dr/f'result-{i}.json').read_text());check(res==run_cell(audit,d,i),'Replay mismatch')
        for name,case in res['cases'].items():
            f,relation=name.split('-');obs=case['observations'];origin=case['origins'];reference,_=route(obs,origin,'verified_root');q=bayes(reference)['p_state_1'];baseq=bayes(obs[:2])['p_state_1'];index={}
            sel=case['selection'];ids=[o['id'] for o in reference];got=sel['assessment']['selected_message_ids'] if sel['format_valid'] else None
            sels.append(dict(cell=i,flip=int(f),relation=relation,format_valid=sel['format_valid'],exact_first_representatives=got==ids,
                correct_origins=bool(got is not None and len(got)==len(ids) and len({origin[k] for k in got})==len(ids))))
            for policy,p in case['policies'].items():
                out=p['final'];row=dict(cell=i,flip=int(f),relation=relation,policy=policy,strong_incoming=(i//2)%2==0,
                    baseline_p=baseq,**metrics(out,reference,'direct',.0001))
                row['tool_error']=abs(p['tool']['p_state_1']-q) if p['tool']['status']=='ok' else None
                row['final_tool_disagreement']=abs(out['assessment']['p_state_1']-p['tool']['p_state_1']) if out['format_valid'] and p['tool']['status']=='ok' else None
                row['update_error']=abs((out['assessment']['p_state_1']-baseq)-(q-baseq)) if out['format_valid'] else None
                rows.append(row);index[policy]=row
            for contrast,a,b in (('root_minus_llm','llm_root','verified_root'),('raw_tool_minus_root','verified_root','raw_correct_tool')):
                x,y=index[a],index[b];valid=x['format_valid'] and y['format_valid']
                pairs.append(dict(cell=i,flip=int(f),relation=relation,contrast=contrast,valid=valid,mae_delta=y['mae']-x['mae'] if valid else None))
    contrasts={}
    for relation,contrast in itertools.product(RELATIONS,('root_minus_llm','raw_tool_minus_root')):
        ps=[p for p in pairs if p['relation']==relation and p['contrast']==contrast];vs=[p for p in ps if p['valid']]
        pc={str(i):mean(p['mae_delta'] for p in vs if p['cell']==i) for i in sorted({p['cell'] for p in vs})}
        contrasts[relation+':'+contrast]=dict(scheduled=len(ps),valid=len(vs),per_cell=pc,complete_mean=mean(pc.values()) if len(vs)==len(ps) else None)
    groups={}
    for rel,policy in itertools.product(RELATIONS,POLICIES):
        rs=[r for r in rows if r['relation']==rel and r['policy']==policy];g=stats(rs)
        for k in ('tool_error','final_tool_disagreement'):
            vals=[r[k] for r in rs if r[k] is not None];g[k]=dict(mean=mean(vals) if vals else None,denominator=len(vals))
        groups[rel+':'+policy]=g
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    summary=dict(status='LINEAGE_UTILITY_ENGINEERING_DIAGNOSTIC',calls=len(audit.calls),independent_cells=20,groups=groups,primary_contrasts=contrasts,
        selectors={rel:dict(scheduled=40,valid=sum(s['format_valid'] for s in sels if s['relation']==rel),exact=sum(s['exact_first_representatives'] for s in sels if s['relation']==rel),correct_origins=sum(s['correct_origins'] for s in sels if s['relation']==rel)) for rel in RELATIONS},
        prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,
        sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='Known authenticated synthetic origins, not inferred real provenance. Root router is a standard engineering baseline. Transport/content controls are intentionally incomplete. End-to-end policy intervention changes tool input and rendered evidence. One model, finite grid, no significance/DICE/novelty claim; all invalids retained; generated probabilities not token probabilities.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return summary,rows,dict(pairs=pairs,selections=sels),hashes

def main():
    p=argparse.ArgumentParser();p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json');p.add_argument('--output',default='runtime/runs/lineage-utility-001')
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
    for i in range(20):run_cell(client,d,i,dr);print(f'Completed cell {i}; total=20',flush=True)
    print('Worker completed.',flush=True)

if __name__=='__main__':main()
