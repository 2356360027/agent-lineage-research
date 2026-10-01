"""Complete-run routing analysis, world clusters and raw-request reconstruction."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean
from .e1 import digest,save
from .dependency_probe import check_server,sources
from .probe_score import check,diagnostics,interval
from .timing_score import AuditClient
from .routing_probe import CASES,POLICIES,cpu_audit,oracle,run_item,unique,validate,world

def summarize(root):
    dr=root/'shard-000';m=json.loads((dr/'manifest.json').read_text(encoding='utf-8'))
    d=m['design'];cfg=m['config'];validate(d)
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(m['cpu_audit']==cpu_audit(),'CPU audit mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((dr/'server_metadata.json').read_text(encoding='utf-8')),False)
    check(len(list(dr.glob('result-*.json')))==d['items'],'Incomplete results')
    check(len(list(dr.glob('i*.json')))==50*d['items'],'Call coverage mismatch')
    client=AuditClient(dr,cfg);rows=[];arms={};initial_errors=[]
    for item in range(d['items']):
        result=json.loads((dr/f'result-{item}.json').read_text(encoding='utf-8'))
        check(result==run_item(client,d,item),'Reconstructed result mismatch')
        obs,y=world(d['world_seed'],item)
        for r in (0,1):initial_errors.append((result['initial'][r]['p_state_1']-oracle([obs[2*r]]))**2)
        metrics={}
        for case in CASES:
            for receiver in (0,1):
                target=unique(result['trajectories'][f'{case}-raw-{receiver}'][-1]['visible_observations'])
                q=oracle(target)
                for policy in POLICIES:
                    key=f'{case}-{policy}-{receiver}';states=result['trajectories'][key];end=states[-1]
                    check(unique(end['visible_observations'])==target,'Methods differ in final unique evidence')
                    v=end['assessment'];p=v['p_state_1']
                    metrics[key]={'oracle_error':(p-q)**2,'brier':(p-y)**2,'accuracy':int(v['answer']==y),
                        'probability':p,'inconsistency':int(p!=.5 and v['answer']!=int(p>.5)),
                        'oracle_brier':(q-y)**2,'unique_evidence_count':len(target),
                        'final_observation_entries':len(end['visible_observations'])}
                    arms.setdefault(case+'_'+policy,[]).append(metrics[key])
        row={'item':item}
        for case in CASES:
            for policy in POLICIES:
                for metric in ('oracle_error','brier'):
                    row[f'{case}_{policy}_{metric}']=mean(metrics[f'{case}-{policy}-{r}'][metric] for r in (0,1))
        row['dedup_minus_raw_oracle_error']=mean(row[f'{c}_dedup_oracle_error']-row[f'{c}_raw_oracle_error'] for c in CASES)
        row['dedup_same_source_minus_relay_brier']=row['same_source_dedup_brier']-row['relay_dedup_brier']
        row['gate_minus_dedup_oracle_error']=mean(row[f'{c}_gate_oracle_error']-row[f'{c}_dedup_oracle_error'] for c in CASES)
        row['dedup_mixed_minus_same_source_oracle_error']=row['mixed_dedup_oracle_error']-row['same_source_dedup_oracle_error']
        row['raw_mixed_minus_same_source_oracle_error']=row['mixed_raw_oracle_error']-row['same_source_raw_oracle_error']
        rows.append(row)
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int and c['usage'][k]>=0 for k in ('prompt_tokens','completion_tokens')) for c in client.calls)
    secondary=['gate_minus_dedup_oracle_error','dedup_mixed_minus_same_source_oracle_error','raw_mixed_minus_same_source_oracle_error']
    report={'status':'EXPLORATORY_RECIPIENT_ROUTING_PILOT','worlds':d['items'],'calls':len(client.calls),
        'primary_endpoints':{k:interval([r[k] for r in rows],d['bootstrap_seed'],d['bootstrap_resamples'],.975) for k in d['primary_endpoints']},
        'secondary_endpoints':{k:interval([r[k] for r in rows],d['bootstrap_seed'],d['bootstrap_resamples']) for k in secondary},
        'arms':{a:{k:mean(v[k] for v in vs) for k in vs[0]} for a,vs in arms.items()},
        'mean_initial_oracle_error':mean(initial_errors),'cpu_audit':cpu_audit(),'usage_complete':complete,
        'prompt_tokens':sum(c['usage']['prompt_tokens'] for c in client.calls) if complete else None,
        'completion_tokens':sum(c['usage']['completion_tokens'] for c in client.calls) if complete else None,
        'sum_call_seconds':sum(c['wall_seconds'] for c in client.calls),
        'warning':'Trusted stable observation IDs only. Ledger is equivalent to strong recipient ID dedup here, not a novel distinct method. Source labels do not imply independence. Small exploratory single-model pilot; do not pool earlier batches or count recipients/conditions as independent worlds.'}
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.rglob('*.json'))}
    return report,rows,hashes

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--audit-only',action='store_true');a=p.parse_args();root=Path(a.run)
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2))
    else:
        s,r,h=summarize(root);save(root/'summary.json',s);save(root/'item_metrics.json',r);save(root/'audit_input_hashes.json',h);print(json.dumps(s,indent=2))
