"""Fixed-denominator E1/E2 scoring, separate from generation and private prompts.

No model transformation/final-answer success is inferred from an action label.
M4's shared M1 proposal is charged to its logical workflow, not free evidence.
"""
from collections import Counter
from statistics import mean

from runtime.reuse_protocol import PRICES, schedule
from runtime.reuse_separation import parse_response, route
from runtime.reuse_statistics import paired_price_rows, clustered_difference


def evaluate(cases, responses, measurements, planner_rows):
    jobs=schedule(cases)
    by_case={c['case_id']:c for c in cases}
    by_job={j['job_id']:j for j in jobs}
    if set(responses)-set(by_job) or set(measurements)-set(by_job):
        raise ValueError('Unplanned model record')
    planner={r['case_id']:r for r in planner_rows}
    if len(planner)!=len(planner_rows) or set(planner)!=set(by_case):
        raise ValueError('Exactly one planned classical record per cache is required')
    observations=[]
    decisions=[]
    physical=[]
    for job in jobs:
        case=by_case[job['case_id']]
        raw=responses.get(job['job_id'])
        pred=parse_response(raw,job['method']) if raw is not None else {
            'valid':False,'admissibility':'missing','action':'missing','basis':None,'failure':'missing'}
        gold=case['audit']['sufficient']
        physical.append(dict(job_id=job['job_id'],**pred))
        for price in PRICES if job['method']=='M3' else (job['price'],):
            row={'case_id':case['case_id'],'base_id':case['base_id'],
                'family':case['family'],'template':case['template'],'method':job['method'],
                'price':price,'sufficient':gold,'job_id':job['job_id'],**pred}
            observations.append(row)
            action=pred['action'] if job['method']=='M1' else route(pred['admissibility'],price)
            decisions.append(dict(row,action=action))
    for case in cases:
        for method in ('M5','M6'):
            verdict='not_assessed' if method=='M5' else planner[case['case_id']]['admissibility']
            for price in PRICES:
                decisions.append({'case_id':case['case_id'],'base_id':case['base_id'],
                    'family':case['family'],'template':case['template'],'method':method,
                    'price':price,'sufficient':case['audit']['sufficient'],'admissibility':verdict,
                    'valid':method=='M6' and planner[case['case_id']]['planner_status']=='PLANNED',
                    'job_id':None,'action':route(verdict,price)})
    for row in decisions:
        action,price,gold=row['action'],row['price'],row['sufficient']
        row.update(unsafe_reuse=action=='reuse' and not gold,
            useful_reuse=action=='reuse' and gold and price<10,
            expected_action=route('applicable' if gold else 'inapplicable',price),
            declared_route_cost=price if action=='reuse' else 10 if action=='recompute' else None,
            unresolved_action=action not in ('reuse','recompute'),
            explicit_judgment_action_conflict=(row['admissibility'] in ('inapplicable','unknown') and action=='reuse'))
    methods={}
    for method in ('M1','M2','M3','M4','M5','M6'):
        dr=[r for r in decisions if r['method']==method]
        jr=[r for r in observations if r['method']==method]
        unique={r['job_id']:r for r in jr}
        required=[j['job_id'] for j in jobs if j['method']==method]
        if method=='M4': required+=[j['proposal_id'] for j in jobs if j['method']=='M4']
        met=[measurements[k] for k in required if k in measurements]
        costs=[r['declared_route_cost'] for r in dr if r['declared_route_cost'] is not None]
        methods[method]={'planned_decision_cells':len(dr),'unique_planned_judgments':len(unique),
            'judgment_status_counts':dict(Counter(r['admissibility'] for r in unique.values())),
            'negative_decision_cells':sum(not r['sufficient'] for r in dr),
            'unsafe_reuse':sum(r['unsafe_reuse'] for r in dr),
            'useful_reuse':sum(r['useful_reuse'] for r in dr),
            'useful_reuse_opportunities':sum(r['sufficient'] and r['price']<10 for r in dr),
            'action_matches_oracle_policy':sum(r['action']==r['expected_action'] for r in dr),
            'unresolved_actions':sum(r['unresolved_action'] for r in dr),
            'judgment_action_conflicts':sum(r['explicit_judgment_action_conflict'] for r in dr),
            'known_declared_cost_cells':len(costs),
            'mean_declared_route_cost_known_cells_only':mean(costs) if costs else None,
            'missing_cost_cells':len(dr)-len(costs),
            'logical_workflow_calls_planned':len(required),
            'logical_workflow_responses_recorded':len(met),
            'logical_input_tokens_observed':sum(m['usage']['prompt_tokens'] for m in met),
            'logical_output_tokens_observed':sum(m['usage']['completion_tokens'] for m in met),
            'logical_generation_http_seconds_observed':sum(m['seconds'] for m in met)}
    bases=sorted({c['base_id'] for c in cases})
    price_contrasts={}
    for method in ('M1','M2'):
        rows=paired_price_rows(observations,method,bases)
        price_contrasts[method]={'paired_rows':rows,**clustered_difference(rows),
            'unresolved_low':sum(r['low_unresolved'] for r in rows),
            'unresolved_high':sum(r['high_unresolved'] for r in rows),
            'unresolved_worst_case_difference_bounds':[
                mean(r['conservative_difference_min'] for r in rows),
                mean(r['conservative_difference_max'] for r in rows)]}
    contrasts={}
    # Reporting fixed comparisons avoids selecting a retrospectively weak baseline.
    for baseline in ('M1','M2','M4','M5','M6'):
        for endpoint,sufficient in (('unsafe_reuse',False),('useful_reuse',True)):
            cells={(r['base_id'],r['method']):r for r in decisions
                   if r['price']==1 and r['sufficient']==sufficient}
            rows=[]
            for base in bases:
                a,b=cells[base,'M3'],cells[base,baseline]
                rows.append({'base_id':base,'template':a['template'],'family':a['family'],
                    'value':int(a[endpoint])-int(b[endpoint]),
                    'M3_unresolved':a['unresolved_action'],'baseline_unresolved':b['unresolved_action']})
            contrasts['M3_minus_'+baseline+'_'+endpoint]={'paired_rows':rows,**clustered_difference(rows)}
    return {'scope':'SYNTHETIC_DEVELOPMENT_E1_E2_ONLY','expected_calls':len(jobs),
        'responses_recorded':len(responses),'missing_calls':len(jobs)-len(responses),
        'planned_bases':len(bases),'structural_templates':len({c['template'] for c in cases}),
        'methods':methods,'price_contrasts':price_contrasts,'action_contrasts':contrasts,
        'physical_judgments':physical,'observations':observations,'decisions':decisions,
        'model_transformations_executed':0,'model_final_answers_scored':0,
        'warning':'Development only. M3 labels are shared across prices by design, not an observed '
          'invariance. Unknown/invalid/missing labels are not correct semantic judgments. Controllers '
          'fall back to recompute; that safety fallback is not valid semantic classification. '
          'Declared route costs exclude generation/planning/cache construction; actual tokens and '
          'times are separate. M4 proposal costs counted although physically shared with M1. '
          'Classical rewrites have separate executed audits, not agent E3 success. '
          'Resampling bands are not population confidence or zero-risk guarantees.'}
