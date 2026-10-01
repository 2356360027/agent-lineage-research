"""Audit all original requests before calculating paired timing-pilot metrics."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean
from .e1 import digest, parse, save, world
from .dependency_probe import check_server, sources
from .probe_score import check, diagnostics, interval, oracle
from .timing_probe import run_item, validate

class AuditClient:
    def __init__(self,directory,cfg):self.directory=directory;self.cfg=cfg;self.calls=[];self.keys=set()
    def call(self,key,prompt,seed,visible):
        check(key not in self.keys,'Duplicate logical call');self.keys.add(key)
        rec=json.loads((self.directory/(key+'.json')).read_text(encoding='utf-8'))
        expected={'model':self.cfg['model'],'messages':prompt,'seed':seed,
            **{k:self.cfg[k] for k in ('temperature','top_p','max_tokens')},
            'response_format':{'type':'json_object'}}
        check(rec['status']=='ok','Unresolved call')
        check(rec['request']==expected and rec['request_hash']==digest(expected),'Request mismatch')
        check(json.loads(rec['raw_text'])==rec['raw_response'],'Raw mismatch')
        check(rec['raw_response']['model']==self.cfg['model'],'Model mismatch')
        check(rec['usage']==rec['raw_response'].get('usage'),'Usage mismatch')
        self.calls.append(rec);return parse(rec['raw_response'],visible)

def summarize(root):
    directory=root/'shard-000';m=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    d=m['design'];cfg=m['config'];validate(d)
    check(digest(sources())==m['source_sha256']==digest(m['source_bundle']),'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')),'Config mismatch')
    check_server(cfg,json.loads((directory/'server_metadata.json').read_text(encoding='utf-8')),False)
    check(len(list(directory.glob('result-*.json')))==d['items'],'Incomplete results')
    check(len(list(directory.glob('i*.json')))==17*d['items'],'Call count mismatch')
    audit=AuditClient(directory,cfg);rows=[];arms={}
    for item in range(d['items']):
        reconstructed=run_item(audit,d,item)
        result=json.loads((directory/f'result-{item}.json').read_text(encoding='utf-8'))
        check(result==reconstructed,'Results/schedule mismatch')
        _,y=world(d['world_seed'],item);values={}
        for regime in ('repeat','new'):
            r=result['regimes'][regime]
            unique={o['id']:o for o in r['observations']};q=oracle(list(unique.values()))
            final=r['final'];metrics={}
            for arm,v in final.items():
                p=v['p_state_1']
                metrics[arm]={'probability':p,'oracle_error':(p-q)**2,'brier':(p-y)**2,
                    'accuracy':int(v['answer']==y),'inconsistency':int(p!=.5 and v['answer']!=int(p>.5))}
                arms.setdefault(regime+'_'+arm,[]).append(metrics[arm])
            for protocol in ('open','gate'):
                values[regime+'_'+protocol+'_sensitivity']=metrics[protocol+'_one']['probability']-metrics[protocol+'_zero']['probability']
                values[regime+'_'+protocol+'_oracle_error']=mean(metrics[protocol+'_'+v]['oracle_error'] for v in ('zero','one'))
                values[regime+'_'+protocol+'_brier']=mean(metrics[protocol+'_'+v]['brier'] for v in ('zero','one'))
            values[regime+'_evidence_brier']=metrics['evidence']['brier']
            values[regime+'_evidence_minus_open_oracle_error']=metrics['evidence']['oracle_error']-values[regime+'_open_oracle_error']
            values[regime+'_gate_minus_open_sensitivity']=values[regime+'_gate_sensitivity']-values[regime+'_open_sensitivity']
            values[regime+'_gate_minus_open_oracle_error']=values[regime+'_gate_oracle_error']-values[regime+'_open_oracle_error']
        values['gate_minus_open_oracle_error']=mean(values[r+'_gate_minus_open_oracle_error'] for r in ('repeat','new'))
        values['evidence_new_minus_repeat_brier']=values['new_evidence_brier']-values['repeat_evidence_brier']
        values['open_new_minus_repeat_brier']=values['new_open_brier']-values['repeat_open_brier']
        values['gate_new_minus_repeat_brier']=values['new_gate_brier']-values['repeat_gate_brier']
        rows.append({'item':item,**values})
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int and c['usage'][k]>=0
        for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    report={'status':'EXPLORATORY_TIMING_COMPONENT_PILOT','worlds':d['items'],'calls':len(audit.calls),
        'primary_endpoints':{k:interval([r[k] for r in rows],d['bootstrap_seed'],d['bootstrap_resamples'],.975) for k in d['primary_endpoints']},
        'secondary_endpoints':{k:interval([r[k] for r in rows],d['bootstrap_seed'],d['bootstrap_resamples']) for k in values if k not in d['primary_endpoints']},
        'arms':{a:{k:mean(v[k] for v in vs) for k in vs[0]} for a,vs in arms.items()},
        'usage_complete':complete,'prompt_tokens':sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,
        'completion_tokens':sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,
        'sum_call_seconds':sum(c['wall_seconds'] for c in audit.calls),
        'warning':'Component pilot, not full DICE. Evidence-only excludes verdicts by construction; no zero-sensitivity efficacy claim. Shared prefixes are reused, not independent replications. Assigned verdicts are not natural peers. Different available evidence has different conditional oracle; Brier across regimes uses the same gold label.'}
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.rglob('*.json'))}
    return report,rows,hashes

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--audit-only',action='store_true');a=p.parse_args();root=Path(a.run)
    if a.audit_only:print(json.dumps(diagnostics(root),indent=2))
    else:
        s,r,h=summarize(root);save(root/'summary.json',s);save(root/'item_metrics.json',r);save(root/'audit_input_hashes.json',h)
        print(json.dumps(s,indent=2))
