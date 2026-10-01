"""Full-coverage, request-reconstruction audit before diagnostic analysis."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean
from .e1 import digest, messages, parse, save, world
from .dependency_probe import check_server, sources
from .probe_score import check, diagnostics, interval, oracle
from .semantic_probe import ARMS, prompt, schedule, seed, validate

def summarize(root):
    directory=root/'shard-000'
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    d=manifest['design'];validate(d);cfg=manifest['config']
    check(digest(sources())==manifest['source_sha256']==digest(manifest['source_bundle']), 'Source mismatch')
    check(all(cfg[k]==d[k] for k in ('temperature','top_p','max_tokens')), 'Config mismatch')
    check_server(cfg,json.loads((directory/'server_metadata.json').read_text(encoding='utf-8')),False)
    check(len(list(directory.glob('result-*.json')))==d['items'],'Incomplete results')
    check(len(list(directory.glob('i*.json')))==d['items']*(1+6*d['repetitions']),'Call coverage mismatch')
    calls=[];rows=[];arm_values={a:[] for a in ARMS}
    def read(key,p,s,visible):
        rec=json.loads((directory/(key+'.json')).read_text(encoding='utf-8'))
        expected={'model':cfg['model'],'messages':p,'seed':s,'temperature':cfg['temperature'],
            'top_p':cfg['top_p'],'max_tokens':cfg['max_tokens'],'response_format':{'type':'json_object'}}
        check(rec['status']=='ok','Failed/pending call')
        check(rec['request']==expected and rec['request_hash']==digest(expected),'Request mismatch')
        check(json.loads(rec['raw_text'])==rec['raw_response'],'Raw mismatch')
        check(rec['raw_response']['model']==cfg['model'],'Model mismatch')
        check(rec['usage']==rec['raw_response'].get('usage'),'Usage mismatch')
        calls.append(rec)
        return parse(rec['raw_response'],visible)
    for item in range(d['items']):
        result=json.loads((directory/f'result-{item}.json').read_text(encoding='utf-8'))
        obs,y=world(d['world_seed'],item);focal=item%3
        evidence=[obs[focal]]+[o for k,o in enumerate(obs) if k!=focal];q=oracle(evidence)
        previous=read(f'i{item}-initial',messages([obs[focal]]),seed(d,item,-1,'initial'),{obs[focal]['id']})
        check(result['item']==item and result['observations']==evidence and result['previous']==previous,'World mismatch')
        check(result['schedule']==[list(s) for s in schedule(d,item)],'Schedule mismatch')
        check(set(result['results'])=={str(r) for r in range(d['repetitions'])},'Repetition mismatch')
        reps=[]
        for r in range(d['repetitions']):
            check(set(result['results'][str(r)])==set(ARMS),'Arm mismatch')
            a={}
            for arm in ARMS:
                v=read(f'i{item}-r{r}-{arm}',prompt(evidence,previous,arm),seed(d,item,r,'paired'),{o['id'] for o in evidence})
                check(v==result['results'][str(r)][arm],'Result mismatch')
                p=v['p_state_1'];a[arm]=p
                arm_values[arm].append({'oracle_squared_error':(p-q)**2,'brier':(p-y)**2,
                    'explicit_accuracy':int(v['answer']==y),'probability':p,
                    'inconsistency':int(p!=.5 and v['answer']!=int(p>.5))})
            legacy=a['legacy_one']-a['legacy_zero'];explicit=a['explicit_one']-a['explicit_zero']
            reps.append({'explicit_verdict_sensitivity':explicit,
                'explicit_minus_legacy_sensitivity':explicit-legacy,
                'legacy_verdict_sensitivity':legacy,
                'explicit_assigned_minus_hidden_oracle_error':((a['explicit_zero']-q)**2+(a['explicit_one']-q)**2)/2-(a['explicit_hidden']-q)**2,
                'explicit_minus_legacy_hidden_oracle_error':(a['explicit_hidden']-q)**2-(a['legacy_hidden']-q)**2})
        rows.append({'item':item,**{k:mean(v[k] for v in reps) for k in reps[0]}})
    complete=all(isinstance(c['usage'],dict) and all(type(c['usage'].get(k)) is int and c['usage'][k]>=0
                 for k in ('prompt_tokens','completion_tokens')) for c in calls)
    report={'status':'EXPLORATORY_SEMANTIC_DIAGNOSTIC','worlds':d['items'],'calls':len(calls),
        'endpoints':{k:interval([r[k] for r in rows],d['bootstrap_seed'],d['bootstrap_resamples'],
                     .975 if k in d['primary_endpoints'] else .95) for k in reps[0]},
        'arms':{a:{k:mean(v[k] for v in vs) for k in vs[0]} for a,vs in arm_values.items()},
        'usage_complete':complete,
        'prompt_tokens':sum(c['usage']['prompt_tokens'] for c in calls) if complete else None,
        'completion_tokens':sum(c['usage']['completion_tokens'] for c in calls) if complete else None,
        'sum_call_seconds':sum(c['wall_seconds'] for c in calls),
        'warning':'Post-validation diagnostic, not confirmatory method efficacy. No natural-peer arm; no probability flipping or exclusions. Clarification changes instructions and cannot uniquely identify an internal cognitive mechanism.'}
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.rglob('*.json'))}
    return report,rows,hashes

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--audit-only',action='store_true');args=p.parse_args()
    root=Path(args.run)
    if args.audit_only:print(json.dumps(diagnostics(root),indent=2))
    else:
        report,rows,hashes=summarize(root)
        save(root/'summary.json',report);save(root/'item_metrics.json',rows);save(root/'audit_input_hashes.json',hashes)
        print(json.dumps(report,indent=2))
