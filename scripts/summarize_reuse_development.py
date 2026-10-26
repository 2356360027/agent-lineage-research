"""Produce publishable aggregates from sealed development outputs; no raw replies.

This readout code is written after outcomes; the main scoring code was frozen
before inference. Added paired H2 display follows the previously stated contrast.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import mean

from runtime.reuse_statistics import clustered_difference
from scripts.run_local_separation import sha,save
from scripts.run_reuse_development import verify_index,verify_prepared

ROOT=Path(__file__).resolve().parents[1]
EXPECTED_SUMMARY='7e44e4c821e40baea80b8b117b822233796cd32da398dce02baf10df031db6fb'


def compact_contrast(obj):
    return {k:v for k,v in obj.items() if k not in ('paired_rows','template_differences')}


def summarize(prepared,audit,replicate,planner,public_record,report):
    verify_index(audit);verify_index(replicate);verify_index(planner)
    # Narrative interpretations below are explicitly this completed run's
    # readout, not a template that may silently report these numbers on new data.
    if sha(audit/'summary.json')!=EXPECTED_SUMMARY:
        raise ValueError('This narrative is locked to the 20261027 development readout')
    if sha(audit/'summary.json')!=sha(replicate/'summary.json'):
        raise ValueError('Frozen-source repeated scoring is not identical')
    result=json.loads((audit/'summary.json').read_bytes())
    verify_prepared(prepared,result['manifest_sha256'])
    if result['missing_calls'] or result['responses_recorded']!=480:
        raise ValueError('This readout expects the completed fixed matrix')
    obs=result['observations'];decisions=result['decisions']
    semantic=[];action=[]
    for method in ('M1','M2','M3','M4'):
        for price in (1,10,20):
            neg=[r for r in obs if r['method']==method and r['price']==price and not r['sufficient']]
            pos=[r for r in obs if r['method']==method and r['price']==price and r['sufficient']]
            if len(neg)!=24 or len(pos)!=24:raise ValueError('Changed denominator')
            semantic.append({'method':method,'price':price,'negative_n':len(neg),'positive_n':len(pos),
                'negative_labels':dict(Counter(r['admissibility'] for r in neg)),
                'positive_labels':dict(Counter(r['admissibility'] for r in pos))})
    for method in ('M1','M2','M3','M4','M5','M6'):
        for price in (1,10,20):
            neg=[r for r in decisions if r['method']==method and r['price']==price and not r['sufficient']]
            pos=[r for r in decisions if r['method']==method and r['price']==price and r['sufficient']]
            action.append({'method':method,'price':price,'negative_n':len(neg),'positive_n':len(pos),
                'unsafe_reuse':sum(r['unsafe_reuse'] for r in neg),
                'sufficient_cache_reuse':sum(r['action']=='reuse' for r in pos),
                'useful_reuse':sum(r['useful_reuse'] for r in pos),
                'unresolved_actions':sum(r['unresolved_action'] for r in neg+pos)})
    paired_H2={}
    for price in (1,10,20):
        selected={(r['base_id'],r['method']):r for r in obs
                  if r['method'] in ('M2','M3') and not r['sufficient'] and r['price']==price}
        bases=sorted({b for b,m in selected})
        rows=[]
        for base in bases:
            a,b=selected[base,'M3'],selected[base,'M2']
            rows.append({'base_id':base,'family':a['family'],'template':a['template'],
                         'value':int(a['admissibility']=='applicable')-int(b['admissibility']=='applicable')})
        paired_H2[str(price)]=clustered_difference(rows)
    first={(r['case_id'],r['price']):r for r in obs if r['method']=='M1'}
    verified=[r for r in obs if r['method']=='M4']
    agreement=sum(r['admissibility']==first[r['case_id'],r['price']]['admissibility'] for r in verified)
    tokens=Counter();prompt_counts=[];generated_usage=[]
    for batch in range(3):
        folder=prepared/f'batch-{batch:02d}';verify_index(folder)
        for path in sorted(folder.glob('*.measurement.json')):
            m=json.loads(path.read_bytes());tokens.update({k:m['usage'][k]
                for k in ('prompt_tokens','completion_tokens','total_tokens') if k in m['usage']})
            prompt_counts.append(m['preflight_prompt_tokens'])
            generated_usage.append(m)
    if len(generated_usage)!=480:raise ValueError('Missing actual usage')
    classical=json.loads((planner/'summary.json').read_bytes())
    family_rows=[]
    for family in sorted({r['family'] for r in obs}):
        for method in ('M1','M2','M3','M4','M5','M6'):
            rows=[r for r in decisions if r['family']==family and r['method']==method and r['price']==1]
            family_rows.append({'family':family,'method':method,
                'negative_n':sum(not r['sufficient'] for r in rows),'positive_n':sum(r['sufficient'] for r in rows),
                'unsafe_reuse':sum(r['unsafe_reuse'] for r in rows),'useful_reuse':sum(r['useful_reuse'] for r in rows)})
    record={'scope':'SYNTHETIC_LOCAL_DEVELOPMENT_NOT_CONFIRMATION','model':'Qwen2.5-7B-Instruct Q5_K_M',
        'physical_attempts':result['dispatch_attempts'],'responses':480,'bases':24,'templates':12,
        'manifest_sha256':result['manifest_sha256'],'summary_sha256':sha(audit/'summary.json'),
        'repeat_summary_sha256':sha(replicate/'summary.json'),'repeat_scoring_exact':True,
        'audit_index_sha256':sha(audit/'sha256.json'),
        'batch_indices':{str(i):sha(prepared/f'batch-{i:02d}'/'sha256.json') for i in range(3)},
        'classical_index_sha256':sha(planner/'sha256.json'),
        'classical_summary':{k:v for k,v in classical.items() if k!='rows'},
        'methods':result['methods'],'semantic_by_price':semantic,'action_by_price':action,
        'price_contrasts':{k:compact_contrast(v) for k,v in result['price_contrasts'].items()},
        'action_contrasts':{k:compact_contrast(v) for k,v in result['action_contrasts'].items()},
        'H2_M3_minus_M2_false_applicability_by_price':paired_H2,
        'low_price_family_rows':family_rows,'M4_M1_label_agreement':{'equal':agreement,'total':len(verified)},
        'physical_tokens':dict(tokens),'prompt_token_range':[min(prompt_counts),max(prompt_counts)],
        'physical_generation_http_seconds':sum(m['seconds'] for m in generated_usage),
        'owned_service_seconds':result['server_seconds'],
        'cold_start_seconds':[t['cold_start_seconds'] for t in result['terminals']],
        'all_owned_services_closed':result['all_started_servers_closed'],'paid_API_cny':0,
        'goal_new_attempts_total':32+480,'goal_service_seconds_total':17.2614+result['server_seconds'],
        'goal_added_weight_bytes':0,'goal_attempt_cap':10000,'goal_service_seconds_cap':72000,
        'E3_model_execution_cases':0,'energy_measured':False,
        'interpretation':'M1 and M2 price effects differ in sign. M3 is not safer than M2 at low price; '
          'M6 has zero observed unsafe reuse while retaining 18/24 useful opportunities. '
          'No universal cache-induced drift or superior separation algorithm claim. '
          'Development only; independent confirmation and E3 remain outstanding.',
        'readout_code_timing':'Display/aggregate export written after outcomes; main scorer was frozen before inference.'}
    save(public_record,record)
    lines=['Six-family DEVELOPMENT readout — 2026-10-27','',
        '480/480 new local generations, 24 bases, 12 structural templates, six families.',
        'No missing/invalid/truncated response, no automatic retry, no paid API.',
        'Frozen-source scoring repeated exactly; every actual prompt reconstructed and verified.',
        'These are E1 labels and E2 decisions, NOT E3 agent execution success rates.','',
        'SEMANTIC LABELS: counts out of 24 negative / 24 positive caches at each price.',
        'Method price false-applicable negative-unknown positive-applicable']
    for r in semantic:
        lines.append(f"{r['method']} {r['price']:>2} {r['negative_labels'].get('applicable',0):>2}/24 "
            f"{r['negative_labels'].get('unknown',0):>2}/24 {r['positive_labels'].get('applicable',0):>2}/24")
    lines+=['','LOW-PRICE ACTIONS (reuse=1, recompute=10):',
        'Method unsafe-reuse/24-negative useful-reuse/24-positive']
    for r in action:
        if r['price']==1:lines.append(f"{r['method']} {r['unsafe_reuse']}/24 {r['useful_reuse']}/24")
    lines+=['','H1: false-applicability low-minus-high price contrast.']
    for method,con in result['price_contrasts'].items():
        lines.append(f"{method}: {con['weighted_difference']:+.6f}; template resampling band "
            f"{con['bootstrap_95_resampling_band']}; unresolved bounds {con['unresolved_worst_case_difference_bounds']}.")
    lines+=['M2 shows the hypothesized direction in development; M1 shows the opposite sign.',
        'M2 also rejects more SUFFICIENT caches at high prices. Thus lower false acceptance',
        'at high price is not a free improvement in semantic discrimination.',
        'Do not present the more favorable M2 comparison as if M1 corroborated it.','',
        'H2: M3 false acceptance is 14/24, versus M2 8/24 at low price.',
        'The price-isolated method is worse here, with equal useful reuse (20/24).',
        'M3 invariance across prices is implemented sharing, not a learned property.','',
        'H3: at LOW price M3 has 14 unsafe reuses, versus M1 9; useful reuse is 20 vs17.',
        'Over all prices M1 has 24 unsafe reuses versus M3 14, but this includes',
        'M1 reusing at tie/high prices despite the stated policy. That total cannot',
        'prove improved low-price semantic safety. M2 has 8 unsafe reuses overall.',
        'Controller benefits and semantic classification must remain separate.','',
        f'H4: M4 labels equal their M1 proposals in {agreement}/{len(verified)} cells.',
        'This is observable agreement, not proof of an internal anchoring mechanism.',
        'M4 low-price unsafe/useful=13/19; M3=14/20. No safety dominance for M3.',
        'M6 unsafe/useful=0/18; two additional useful reuses from M3 do not erase',
        'its 14 unsafe reuses. Do not call cheaper wrong decisions efficiency gains.',
        'Logical M4 includes proposal+verifier costs even though M1 records are shared.','',
        'Classical baseline: 48 attempted contexts, 46 planned, two 60-second timeouts.',
        '18 cache-only rewrites, 18 snapshot matches, 342 stress checks; no value or',
        'column mismatch. Timeout/missing rewrites remain unknown with recompute fallback.',
        'This configured coverage is not the strongest theoretically possible SQL optimizer.',
        'The full process cost, including failed/time-limited attempts, remains in raw logs.','',
        'Resource accounting:',f"Physical tokens: {dict(tokens)}.",
        f"Prompt token range: {record['prompt_token_range']} (no input truncation).",
        f"Owned service seconds: {record['owned_service_seconds']:.6f}; cold starts {record['cold_start_seconds']}.",
        f"Generation HTTP seconds: {record['physical_generation_http_seconds']:.6f}.",
        f"New-goal totals: {record['goal_new_attempts_total']}/10000 attempts; "
        f"{record['goal_service_seconds_total']:.6f}/72000 service seconds.",
        'Zero new weight downloads in this run. Energy/electricity not measured.',
        'All three owned model services closed; no background experiment remains.','',
        'Decision: keep the 14-day goal active, but do not claim the separation method works.',
        'Preserve this negative development result. Next: protocol/transport audit, then',
        'freeze held-out structured confirmation and the independently selected second model.',
        'No prompt tweaking solely to make M3 win. No new task mining for a positive effect.',
        'Independent confirmation, model-family replication, E3 and final manuscript remain.','',
        'Limitations: two instances per template; purposive templates; formal Q supplied;',
        'explicitly solicited labels; same-family verifier; quantization; declared costs.',
        'Resampling bands are not population confidence or proof of zero risk.',
        'Label-action conflicts are checked, but all free-text justifications are not',
        'human-semantic-reviewed; do not infer private reasoning from a short basis string.','',
        'Manifest SHA256: '+record['manifest_sha256'],
        'Summary SHA256: '+record['summary_sha256'],
        'Main run: runtime/runs/reuse-development-prepared-20261027-001',
        'Audits: runtime/runs/reuse-development-audit-20261027-001 and -002',
        'Public aggregate record: '+str(public_record.relative_to(ROOT)).replace('\\','/')]
    with report.open('x',encoding='utf-8') as stream:stream.write('\n'.join(lines)+'\n')
    print(json.dumps({'public_record':str(public_record),'report':str(report),
        'summary_sha256':record['summary_sha256'],'goal_attempts':record['goal_new_attempts_total'],
        'physical_tokens':record['physical_tokens'],'price_contrasts':record['price_contrasts']},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('prepared','audit','replicate','planner','public-record','report'):
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();summarize(*[getattr(a,k).resolve() for k in ('prepared','audit','replicate','planner','public_record','report')])
