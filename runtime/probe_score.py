"""Item-clustered dependency-probe analysis with raw-record integrity checks."""
import argparse
import csv
import json
import math
from pathlib import Path
import random
from statistics import mean, median
from .e1 import canonical, digest, messages, parse, save, world
from .dependency_probe import final_messages, call_seed, configure, sources, check_server


def oracle(observations):
    z=sum((2*o['value']-1)*math.log(o['reliability']/(1-o['reliability'])) for o in observations)
    return 1/(1+math.exp(-z))


def check(condition, message):
    if not condition: raise ValueError(message)


def interval(values,seed,resamples,level=.95):
    rng=random.Random(seed)
    boot=sorted(mean(values[rng.randrange(len(values))] for _ in values) for _ in range(resamples))
    alpha=(1-level)/2
    return {'mean':mean(values),'level':level,
            'item_bootstrap_percentile_interval':[boot[int(alpha*resamples)],boot[min(resamples-1,int((1-alpha)*resamples))]]}


def diagnostics(root):
    """Coverage only: do not peek at effect estimates during validation."""
    status={}; malformed=[]
    for path in sorted(root.glob('shard-*/i*.json')):
        try:
            rec=json.loads(path.read_text(encoding='utf-8'))
            label=rec.get('status','unknown')
            status[label]=status.get(label,0)+1
        except (ValueError,OSError):
            malformed.append(str(path.relative_to(root)))
    return {'kind':'COVERAGE_ONLY_NO_EFFECT_ESTIMATES','call_status_counts':status,
            'completed_item_files':len(list(root.glob('shard-*/result-*.json'))),
            'malformed_records':malformed,
            'warning':'Preserve failed/pending calls. Do not replace failed runs with selected successful samples.'}


def summarize(root):
    paths=sorted(root.glob('shard-*/manifest.json'))
    check(bool(paths),'No manifests')
    manifests=[json.loads(p.read_text(encoding='utf-8')) for p in paths]
    spec=manifests[0]['run_spec']
    check(all(m['run_spec']==spec for m in manifests),'Incompatible runs')
    check(all(digest(m['source_bundle'])==spec['source_sha256'] for m in manifests),'Source checksum mismatch')
    check(digest(sources())==spec['source_sha256'],
          'Analysis runtime differs from recorded runtime; use the preserved source version')
    cfg=spec['config']; design=spec['design']; R=cfg['repetitions']
    check(spec['stage'] in ['explore','validate'],'Unknown stage')
    check(configure(cfg,design,spec['stage'])==cfg,'Config/design mismatch')
    check(type(spec['shards']) is int and 0 < spec['shards'] <= cfg['items'],'Invalid worker count')
    shard_ids=[m['shard'] for m in manifests]
    check(len(set(shard_ids))==len(shard_ids),'Duplicate shards')
    check(set(shard_ids)==set(range(spec['shards'])),'Missing or invalid worker IDs')
    rows=[]; calls=[]; keys=[]; input_hashes={}
    arm_metrics={a:[] for a in cfg['arms']}
    for path,m in zip(paths,manifests):
        directory=path.parent
        check(directory.name==f"shard-{m['shard']:03d}",'Worker directory mismatch')
        check_server(cfg,json.loads((directory/'server_metadata.json').read_text(encoding='utf-8')))
        expected_items=list(range(m['shard'],cfg['items'],spec['shards']))
        check(len(list(directory.glob('result-*.json')))==len(expected_items),'Worker results missing/extra')
        check(len(list(directory.glob('i*.json')))==len(expected_items)*(3+R*len(cfg['arms'])),'Call records missing/extra')
        for item in expected_items:
            keys.append(item)
            result=json.loads((directory/f'result-{item}.json').read_text(encoding='utf-8'))
            obs,y=world(cfg['seed'],item); focal=item%3
            other=[a for a in range(3) if a!=focal]
            evidence=[obs[focal]]+[obs[a] for a in other]
            q=oracle(evidence)
            check(result['item']==item and result['focal']==focal,'Result identity mismatch')
            check(result['observations']==evidence and result['evidence_hash']==digest(evidence),'Evidence mismatch')

            def read(key,prompt,seed,visible):
                record_path=directory/(key+'.json')
                rec=json.loads(record_path.read_text(encoding='utf-8'))
                check(rec['status']=='ok',f'Unresolved call: {key}')
                req=rec['request']
                expected={'model':cfg['model'],'messages':prompt,'seed':seed,
                    'temperature':cfg['temperature'],'top_p':cfg['top_p'],'max_tokens':cfg['max_tokens'],
                    'response_format':{'type':'json_object'}}
                check(req==expected and rec['request_hash']==digest(req),f'Request mismatch: {key}')
                check(json.loads(rec['raw_text'])==rec['raw_response'],f'Raw data mismatch: {key}')
                check(rec['raw_response']['model']==cfg['model'],f'Response model mismatch: {key}')
                check(rec['usage']==rec['raw_response'].get('usage'),f'Usage mismatch: {key}')
                calls.append(rec)
                return parse(rec['raw_response'],visible)

            initial=[read(f'i{item}-s0-{a}',messages([o]),call_seed(cfg['seed'],item,-1,f's0-{a}'),{o['id']})
                     for a,o in enumerate(obs)]
            check(initial==result['initial'],'Initial results mismatch')
            natural=[{'agent':f'peer{k}','answer':initial[a]['answer']} for k,a in enumerate(other)]
            check(natural==result['natural_peers'],'Natural verdict mismatch')
            check(set(result['results'])=={str(r) for r in range(R)},'Repetition set mismatch')
            check(all(set(v)==set(cfg['arms']) for v in result['results'].values()),'Arm set mismatch')
            peers={'hidden':[],'hidden_repeat':[],'natural':natural,
                   'injected_zero':[{'agent':f'peer{k}','answer':0} for k in range(2)],
                   'injected_one':[{'agent':f'peer{k}','answer':1} for k in range(2)]}
            schedule=[(r,a) for r in range(R) for a in cfg['arms']]
            random.Random(call_seed(cfg['seed'],item,-1,'schedule')).shuffle(schedule)
            check(result['schedule']==[list(x) for x in schedule],'Schedule mismatch')
            values=[]
            for rep in range(R):
                a={}
                for arm in cfg['arms']:
                    role='repeat' if arm=='hidden_repeat' else 'paired'
                    a[arm]=read(f'i{item}-r{rep}-{arm}',final_messages(evidence,initial[focal],peers[arm]),
                        call_seed(cfg['seed'],item,rep,role),{o['id'] for o in evidence})
                    check(a[arm]==result['results'][str(rep)][arm],'Derived result mismatch')
                    v=a[arm]; prob=v['p_state_1']
                    arm_metrics[arm].append({'item':item,'oracle_squared_error':(prob-q)**2,
                        'probability':prob,
                        'realized_brier':(prob-y)**2,'explicit_accuracy':int(v['answer']==y),
                        'threshold_accuracy':int(int(prob>=.5)==y),
                        'probability_tie':int(prob==.5),
                        'decision_inconsistent':int(prob!=.5 and v['answer']!=int(prob>.5))})
                h,n,z,o,hr=[a[k]['p_state_1'] for k in ['hidden','natural','injected_zero','injected_one','hidden_repeat']]
                values.append({'natural_minus_hidden_oracle_squared_error':(n-q)**2-(h-q)**2,
                    'injected_one_minus_zero_probability':o-z,
                    'assigned_minus_hidden_oracle_squared_error':((z-q)**2+(o-q)**2)/2-(h-q)**2,
                    'hidden_repeat_abs_probability_difference':abs(hr-h),
                    'natural_abs_probability_difference':abs(n-h),
                    'natural_minus_hidden_realized_brier':(n-y)**2-(h-y)**2,
                    'natural_minus_hidden_explicit_accuracy':int(a['natural']['answer']==y)-int(a['hidden']['answer']==y)})
            row={'item':item,'gold':y,'oracle_p':q,'focal':focal,
                 'focal_initial_p':initial[focal]['p_state_1'],'focal_initial_answer':initial[focal]['answer'],
                 'peer_0_answer':natural[0]['answer'],'peer_1_answer':natural[1]['answer'],
                 'oracle_direction':int(q>.5) if q!=.5 else 'tie',
                 'natural_peer_unanimous':natural[0]['answer']==natural[1]['answer'],
                 'initial_decision_inconsistent_count':sum(x['p_state_1']!=.5 and x['answer']!=int(x['p_state_1']>.5) for x in initial)}
            row.update({k:mean(v[k] for v in values) for k in values[0]})
            for arm in cfg['arms']:
                row['mean_p_'+arm]=mean(result['results'][str(r)][arm]['p_state_1'] for r in range(R))
            row['unanimous_peer_attraction']=(
                (2*natural[0]['answer']-1)*(row['mean_p_natural']-row['mean_p_hidden'])
                if row['natural_peer_unanimous'] else None)
            rows.append(row)
        for f in sorted(directory.rglob('*.json')):
            import hashlib
            input_hashes[f.relative_to(root).as_posix()]=hashlib.sha256(f.read_bytes()).hexdigest()
    check(set(keys)==set(range(cfg['items'])) and len(keys)==cfg['items'],'Full planned dataset required before summary')
    rows.sort(key=lambda r:r['item'])
    metrics={}
    endpoints=list(values[0])
    for k in endpoints:
        level=.975 if k in design['primary_endpoints'] else .95
        metrics[k]=interval([r[k] for r in rows],design['bootstrap_seed'],design['bootstrap_resamples'],level)
    arm_summary={arm:{k:mean(r[k] for r in data) for k in data[0] if k!='item'} for arm,data in arm_metrics.items()}
    complete=all(isinstance(c.get('usage'),dict) and all(type(c['usage'].get(k)) is int and c['usage'][k]>=0
        for k in ['prompt_tokens','completion_tokens']) for c in calls)
    output={'status':spec['stage'].upper()+'_SYNTHETIC_WORLD_STUDY',
        'independent_worlds':len(rows),'repetitions_per_world':R,'calls':len(calls),
        'integrity':'reconstructed prompts, current/recorded runtime sources, endpoint metadata and outputs matched; not independent execution attestation',
        'endpoints':metrics,'arms':arm_summary,
        'initial_inconsistent_count':sum(r['initial_decision_inconsistent_count'] for r in rows),
        'usage_complete':complete,
        'prompt_tokens':sum(c['usage']['prompt_tokens'] for c in calls) if complete else None,
        'completion_tokens':sum(c['usage']['completion_tokens'] for c in calls) if complete else None,
        'median_call_seconds':median(c['wall_seconds'] for c in calls),
        'sum_call_seconds':sum(c['wall_seconds'] for c in calls),
        'warning':'Assigned verdicts are controlled interventions, not natural agents. '
        'Sensitivity alone is not harm. Repeat variation must not be subtracted as an unbiased noise correction. '
        'Synthetic task/model scope only. Bootstrap intervals are approximate; no effect is not equivalence.'}
    return output,rows,input_hashes


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True)
    p.add_argument('--audit-only',action='store_true');args=p.parse_args()
    root=Path(args.run)
    if args.audit_only:
        print(json.dumps(diagnostics(root),indent=2))
        raise SystemExit(0)
    output,rows,hashes=summarize(root)
    save(root/'summary.json',output);save(root/'audit_input_hashes.json',hashes)
    with (root/'item_metrics.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(json.dumps(output,indent=2))
