"""Frozen tool-input-audit-v1: 12 fresh cells and 192 model calls.

Each cell: two label complements x two instruction bundles x four cases:
copy/valid, copy/duplicated, independent/valid, independent/omitted. All
cases expose the same three measurement messages. Copy and independent
cases differ only in supplied trusted origin metadata. Valid calculator
inputs contain one representative of each visible origin. Duplicated inputs
contain all three messages, counting a copied origin twice. Omitted inputs
exclude the third independent originating measurement. All calculator
arithmetic is correct for its declared input list. No fabricated model
outputs or hidden true label are exposed. Opaque IDs and map schema fixed.

Primary descriptive endpoints per relation: wrong-input minus valid-input
posterior MAE, and current-minus-legacy difference in this excess MAE.
Wrong-tool agreement within 1e-4, valid-tool damage and answer consistency
are secondary, not evidence of an internal trust mechanism. Paired worlds
condition on coincident measurement contents; no prevalence estimate.
12 independent cells, not 192 independent samples. No significance claim,
retries, adaptive samples, omission of invalid outputs, or old-batch pooling.

The CPU provenance validator checks coverage, origin duplication, and payload
identity. Its exact recalculation is a conventional programmatic reference
using experiment-given origins, not a learned algorithm or evidence of DICE
efficacy. Validation output is NOT exposed to the model in this diagnostic.
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
from . import prompt_id_bridge as bridge
from . import origin_representation as origin
from . import lineage_utility as lu
from .dependency_probe import sources, check_server
from .e1 import canonical, digest, save
from .format_probe_v2 import Client, Audit
from .native import verify
from .probe_score import check, diagnostics
from .stage_diagnostic import metrics
from .scaffold_probe import stats

DESIGN=dict(protocol='tool-input-audit-v1',seed=126950837,cells=12,calls=192,temperature=0.,top_p=1.,max_tokens=512)
WORDS=('legacy','current')
CASES=(('copy','valid'),('copy','duplicated'),('independent','valid'),('independent','omitted'))
TOL=0.0001

def tasks(d):
    cells=origin.tasks(d)
    previous=origin.tasks(origin.DESIGN)+bridge.tasks(bridge.DESIGN)
    used={tuple(c['reliabilities']) for c in previous}
    check(all(tuple(c['reliabilities']) not in used for c in cells),'Prior cell collision')
    return cells

def validate_inputs(obs,roots,inputs):
    """Trusted-registry validator. Reject unknown IDs and inconsistent payloads."""
    visible={o['id']:o for o in obs}
    check(len(visible)==len(obs) and set(roots)==set(visible),'Visible IDs or roots')
    lu.route(obs,roots,'verified_root')  # rejects conflicting same-origin payloads
    check(all(o['id'] in visible and o==visible[o['id']] for o in inputs),'Unknown or changed calculator record')
    counts={}
    for o in inputs:counts[roots[o['id']]]=counts.get(roots[o['id']],0)+1
    duplicate=sorted(k for k,v in counts.items() if v>1)
    missing=sorted(set(roots.values())-set(counts))
    unique,_=lu.route(obs,roots,'verified_root')
    return dict(valid=not duplicate and not missing,duplicate_origins=duplicate,missing_origins=missing,
                corrected_calculator=lu.tool_for(unique))

def trial(d,i,flip,words,relation,condition):
    check((relation,condition) in CASES,'Case')
    obs,roots=origin.world(d,i,flip,relation)
    unique,_=lu.route(obs,roots,'verified_root')
    selected=unique if condition=='valid' else obs if condition=='duplicated' else obs[:2]
    tool=lu.tool_for(selected);audit=validate_inputs(obs,roots,selected)
    target=lu.tool_for(unique)['p_state_1']
    check(audit['valid']==(condition=='valid'),'Intervention invalid')
    if condition!='valid':check(abs(tool['p_state_1']-target)>.01,'Insufficient diagnostic separation')
    payload=origin.encode(obs,roots,'map');payload['external_calculator']=tool
    system=(lu.SYSTEM.replace('experiment_authenticated_origins','origin_registry')+lu.FINAL if words=='legacy'
            else origin.STRUCTURE+origin.GENERATIVE+origin.FINAL)
    check(words in WORDS,'Instruction bundle')
    return [dict(role='system',content=system),dict(role='user',content=canonical(payload))],obs,roots,unique,tool,audit

def schedule(d,i):
    entries=[(f,w,r,c) for f,w,(r,c) in itertools.product((0,1),WORDS,CASES)]
    random.Random(f'{d["seed"]}:{i}:schedule').shuffle(entries)
    return entries

def run_cell(client,d,i,directory=None):
    result=dict(cell=i,task=tasks(d)[i],outputs={})
    seed=int(digest([d['protocol'],d['seed'],i])[:7],16)
    for flip,words,rel,condition in schedule(d,i):
        prompt,obs,roots,unique,tool,audit=trial(d,i,flip,words,rel,condition)
        client.evaluator=partial(lu.evaluate,visible={o['id'] for o in obs})
        key=f'i{i}-f{flip}-{words}-{rel}-{condition}'
        result['outputs'][key]=dict(flip=flip,words=words,relation=rel,condition=condition,
            outcome=client.call(key,prompt,seed,'final'),programmatic_audit=audit)
    if directory is not None:save(directory/f'result-{i}.json',result)
    return result

def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text());d,cfg=m['design'],m['config']
    check(d==DESIGN,'Frozen design changed')
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text()),False)
    check(len(list(dr.glob('i*.json')))==192 and len(list(dr.glob('result-*.json')))==12,'Coverage mismatch')
    audit=Audit(dr,cfg);rows=[];index={}
    for i in range(12):
        result=json.loads((dr/f'result-{i}.json').read_text());check(result==run_cell(audit,d,i),'Replay mismatch')
        for entry in result['outputs'].values():
            flip,words,rel,condition=[entry[k] for k in ('flip','words','relation','condition')]
            _,obs,roots,unique,tool,validation=trial(d,i,flip,words,rel,condition)
            out=entry['outcome'];row=dict(cell=i,flip=flip,words=words,relation=rel,condition=condition,**metrics(out,unique,'direct',TOL))
            row.update(tool_p=tool['p_state_1'],tool_input_valid=validation['valid'],program_reference_p=validation['corrected_calculator']['p_state_1'])
            if out['format_valid']:
                a=out['assessment'];row.update(tool_agreement=int(abs(a['p_state_1']-tool['p_state_1'])<=TOL),posterior_correct=int(row['mae']<=TOL),
                    answer_probability_inconsistent=int(a['answer']!=int(a['p_state_1']>.5)) if a['p_state_1']!=.5 else None)
            rows.append(row);index[i,flip,words,rel,condition]=row
    groups={}
    for words,(rel,condition) in itertools.product(WORDS,CASES):
        rs=[r for r in rows if (r['words'],r['relation'],r['condition'])==(words,rel,condition)];valid=[r for r in rs if r['format_valid']]
        g=stats(rs);g.update(posterior_correct_all_calls=sum(r['posterior_correct'] for r in valid),
            tool_agreement_all_calls=sum(r['tool_agreement'] for r in valid),
            answer_probability_inconsistent=sum(r['answer_probability_inconsistent']==1 for r in valid))
        if condition=='valid':g['valid_tool_damage_valid_calls']=sum(1-r['posterior_correct'] for r in valid)
        else:g['wrong_tool_agreement_all_calls']=sum(r['tool_agreement'] for r in valid)
        groups[f'{words}:{rel}:{condition}']=g
    pairs=[]
    for i,flip,words,rel in itertools.product(range(12),(0,1),WORDS,('copy','independent')):
        wrong='duplicated' if rel=='copy' else 'omitted'
        a,b=index[i,flip,words,rel,'valid'],index[i,flip,words,rel,wrong]
        delta=b['mae']-a['mae'] if a['format_valid'] and b['format_valid'] else None
        pairs.append(dict(cell=i,flip=flip,words=words,relation=rel,excess_mae=delta))
    contrasts={}
    for rel in ('copy','independent'):
        cell_effects={}
        for words in WORDS:
            ps=[p for p in pairs if p['words']==words and p['relation']==rel]
            cell_effects[words]={str(i):mean(p['excess_mae'] for p in ps if p['cell']==i) for i in range(12) if all(p['excess_mae'] is not None for p in ps if p['cell']==i)}
            pc=cell_effects[words];contrasts[f'{rel}:{words}:wrong_minus_valid']=dict(complete_cells=len(pc),complete_mean=mean(pc.values()) if len(pc)==12 else None,per_cell=pc)
        pc={k:cell_effects['current'][k]-v for k,v in cell_effects['legacy'].items() if k in cell_effects['current']}
        contrasts[f'{rel}:current_minus_legacy_excess']=dict(complete_cells=len(pc),complete_mean=mean(pc.values()) if len(pc)==12 else None,per_cell=pc)
    usage=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    summary=dict(status='TOOL_INPUT_SELECTION_DIAGNOSTIC',calls=len(audit.calls),independent_cells=12,groups=groups,primary_descriptive_contrasts=contrasts,
        invalid_calls=sum(not c['outcome']['format_valid'] for c in audit.calls),
        prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if usage else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if usage else None,sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='One model and 12 synthetic cells; known trusted origins; no significance/novelty/DICE or internal trust mechanism claim. Input length changes are bundled with selection errors. CPU validator is a conventional reference, not measured LLM reasoning. Invalids retained, no imputation.')
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return summary,rows,pairs,hashes

def main():
    p=argparse.ArgumentParser();p.add_argument('--native-config',default='runtime/runs/native-smoke-config.json');p.add_argument('--output',default='runtime/runs/tool-input-audit-001')
    for k in ('execute','score','audit-only'):p.add_argument('--'+k,action='store_true')
    a=p.parse_args();root=Path(a.output);check(sum((a.execute,a.score,a.audit_only))<=1,'Action')
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2));return
    if a.score:
        objects=summarize(root)
        for name,obj in zip(('summary','cell_metrics','paired_contrasts','audit_input_hashes'),objects):save(root/(name+'.json'),obj)
        print(json.dumps(objects[0],indent=2));return
    d=dict(DESIGN);tasks(d)
    for i in range(12):
        for f,w,r,c in schedule(d,i):trial(d,i,f,w,r,c)
    print(json.dumps(dict(design=d,design_sha256=digest(d),execute=a.execute)),flush=True)
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
