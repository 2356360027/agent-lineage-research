"""Descriptive finite-grid diagnostics, no population-significance claims."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean
from .e1 import digest,save
from .dependency_probe import check_server,sources
from .probe_score import check,diagnostics
from .timing_score import AuditClient
from .comprehension_probe import grid,run_cell,validate

def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text(encoding='utf-8'));d=m['design'];cfg=m['config'];validate(d)
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text(encoding='utf-8')),False)
    check(len(list(dr.glob('result-*.json')))==9 and len(list(dr.glob('i*.json')))==216,'Incomplete/extra records')
    client=AuditClient(dr,cfg);rows=[];by_arm={};by_value={};symmetry={};cell_metrics=[]
    for cell,(r,t) in enumerate(grid(d)):
        result=json.loads((dr/f'result-{cell}.json').read_text(encoding='utf-8'))
        check(result==run_cell(client,d,cell),'Result/order mismatch')
        a=r*t+(1-r)*(1-t);metrics={}
        for representation in ('layered','effective'):
            for wording in ('current','explicit'):
                arm=representation+'_'+wording;cellrows=[]
                for value in (0,1):
                    q=a if value else 1-a
                    for repeat in range(3):
                        v=result['outputs'][f'i{cell}-v{value}-{representation}-{wording}-r{repeat}'];p=v['p_state_1']
                        scores={'oracle_squared_error':(p-q)**2,'absolute_error':abs(p-q),
                            'direction_correct':int(v['answer']==value),'probability_direction_correct':int((p>.5)==bool(value) and p!=.5),
                            'inconsistent':int(p!=.5 and v['answer']!=int(p>.5)),'p_state_1':p}
                        rows.append({'cell':cell,'r':r,'t':t,'value':value,'representation':representation,'wording':wording,'repetition':repeat,'oracle_p':q,**scores})
                        by_arm.setdefault(arm,[]).append(scores);by_value.setdefault(arm+'_v'+str(value),[]).append(scores);cellrows.append(scores)
                vals=[]
                for repeat in range(3):
                    p0=result['outputs'][f'i{cell}-v0-{representation}-{wording}-r{repeat}']['p_state_1']
                    p1=result['outputs'][f'i{cell}-v1-{representation}-{wording}-r{repeat}']['p_state_1']
                    vals.append(abs(p0+p1-1))
                symmetry.setdefault(arm,[]).extend(vals)
                metrics[arm]=mean(x['oracle_squared_error'] for x in cellrows)
        cell_metrics.append({'cell':cell,**metrics})
    def summarize_groups(groups):return {a:{k:mean(x[k] for x in vs) for k in vs[0]} for a,vs in groups.items()}
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int and c['usage'][k]>=0 for k in ('prompt_tokens','completion_tokens')) for c in client.calls)
    report={'status':'FINITE_GRID_COMPREHENSION_DIAGNOSTIC','grid_cells':9,'calls':216,
        'arms':summarize_groups(by_arm),'direction_strata':summarize_groups(by_value),
        'mean_complement_symmetry_error':{a:mean(v) for a,v in symmetry.items()},
        'descriptive_mse_contrasts':{
            'effective_minus_layered_current':mean(x['effective_current']-x['layered_current'] for x in cell_metrics),
            'explicit_minus_current_layered':mean(x['layered_explicit']-x['layered_current'] for x in cell_metrics),
            'interaction':mean((x['effective_explicit']-x['effective_current'])-(x['layered_explicit']-x['layered_current']) for x in cell_metrics)},
        'usage_complete':complete,'prompt_tokens':sum(c['usage']['prompt_tokens'] for c in client.calls) if complete else None,
        'completion_tokens':sum(c['usage']['completion_tokens'] for c in client.calls) if complete else None,
        'sum_call_seconds':sum(c['wall_seconds'] for c in client.calls),
        'warning':'Hand-selected finite grid and repeated draws, not 216 independent tasks. No population CIs or significance tests. Effective representation supplies a computed likelihood, not proof of wording-only causality. Alternative wording removes examples and adds clarification simultaneously. No communication or DICE efficacy evaluated.'}
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.rglob('*.json'))}
    return report,rows,hashes

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--audit-only',action='store_true');a=p.parse_args();root=Path(a.run)
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2))
    else:
        s,r,h=summarize(root);save(root/'summary.json',s);save(root/'cell_metrics.json',r);save(root/'audit_input_hashes.json',h);print(json.dumps(s,indent=2))
