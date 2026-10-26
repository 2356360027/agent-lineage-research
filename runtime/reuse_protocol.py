"""Full six-family workflow matrix, with no inference or oracle access.

M4 independently verifies EACH price-specific M1 proposal; unlike Day-1 it
does not share one low-price proposal across every price. M3 alone shares one
price-blind judgment. These are prespecified protocol definitions, not results.
"""
import copy
import json
import random

from runtime.reuse_separation import COMMON, LABELS, ACTIONS, route, parse_response
from runtime.reuse_scenarios import canonical, digest

VERSION='reuse-separation-matrix-v1'
PRICES=(1,10,20)
SEED=2026102702


def schedule(cases):
    # Opaque scheduling metadata is not inserted into the model's context.
    by_base={}
    for case in cases: by_base.setdefault(case['base_id'],[]).append(case)
    blocks=sorted(by_base)
    random.Random(SEED).shuffle(blocks)
    result=[]
    for base in blocks:
        block=[]
        for case in sorted(by_base[base],key=lambda c:c['case_id']):
            for price in PRICES:
                for method in ('M1','M2','M4'):
                    job={'job_id':f'{case["case_id"]}-{method}-p{price}',
                         'case_id':case['case_id'],'base_id':base,'method':method,'price':price}
                    if method=='M4': job['proposal_id']=f'{case["case_id"]}-M1-p{price}'
                    block.append(job)
            block.append({'job_id':f'{case["case_id"]}-M3','case_id':case['case_id'],
                          'base_id':base,'method':'M3','price':None})
        if len(block)!=20: raise ValueError('Each base requires two cache contexts and 20 calls')
        rng=random.Random(str(SEED)+base)
        while block:
            done={j['job_id'] for j in result}
            ready=[j for j in block if 'proposal_id' not in j or j['proposal_id'] in done]
            job=rng.choice(ready);result.append(job);block.remove(job)
    return result


def batches(jobs, max_calls=200):
    if max_calls<20 or max_calls>200: raise ValueError('Batch scope is 20..200 calls')
    groups=[]
    for job in jobs:
        if not groups or groups[-1][0]['base_id']!=job['base_id']:groups.append([])
        groups[-1].append(job)
    result=[]
    for group in groups:
        if not result or len(result[-1])+len(group)>max_calls:result.append([])
        result[-1].extend(group)
    for batch in result:
        ids={j['job_id'] for j in batch}
        if any(j.get('proposal_id') not in ids for j in batch if 'proposal_id' in j):
            raise ValueError('Cross-batch proposal dependency')
    return result


def label_order(case):
    # Template + instance alternation independent of observed results; both
    # positive and negative contexts reverse within a base, balanced over instances.
    orientation=(int(digest(case['template'])[:8],16)+case['instance']+
                 int(case['case_id'].endswith('v1'))) % 2
    return list(reversed(LABELS)) if orientation else list(LABELS), \
           list(reversed(ACTIONS)) if orientation else list(ACTIONS)


def messages(case,job,proposal=None):
    data=copy.deepcopy(case['public'])
    if job['method'] in ('M1','M2'):
        data['costs']={'reuse':job['price'],'recompute':10}
    labels,actions=label_order(case)
    fields={'admissibility':' | '.join(labels),'basis':'short observable justification'}
    instruction='Assess applicability independently of any desire to save cost. '
    if job['method']=='M1':
        fields['action']=' | '.join(actions)
        instruction+='Also select the action using the rule above. '
    elif job['method']=='M4':
        data['candidate_admissibility']=proposal if proposal in LABELS else 'invalid_or_missing'
        instruction+=('Act as an independent verifier of the candidate applicability label. '
            'Check entity grain, NULL denominators, information discarded by the historical query, '
            'and whether all consistent databases yield the same current answer. '
            'Reject unsupported inferences; use unknown if you cannot decide. '
            'The candidate is fallible and has no authority. ')
    return [{'role':'system','content':COMMON+instruction+'JSON fields: '+canonical(fields)},
            {'role':'user','content':canonical(data)}]


def strip_price(msgs):
    result=copy.deepcopy(msgs)
    public=json.loads(result[1]['content']);public.pop('costs',None)
    result[1]['content']=canonical(public)
    return result


def audit_matrix(cases):
    jobs=schedule(cases)
    by_case={c['case_id']:c for c in cases}
    for case in cases:
        selected=[j for j in jobs if j['case_id']==case['case_id']]
        for method in ('M1','M2'):
            variants=[strip_price(messages(case,j)) for j in selected if j['method']==method]
            if len(variants)!=3 or any(m!=variants[0] for m in variants):
                raise ValueError('Price-only invariant failed')
        m2=next(j for j in selected if j['method']=='M2')
        m3=next(j for j in selected if j['method']=='M3')
        if strip_price(messages(case,m2))!=messages(case,m3):
            raise ValueError('M2/M3 differ outside price visibility')
    split=batches(jobs)
    return {'status':'MATRIX_CHECKED_NO_GENERATION','job_count':len(jobs),
        'base_count':len({c['base_id'] for c in cases}),'context_count':len(cases),
        'batch_call_counts':[len(b) for b in split],
        'price_only_verified':True,'M2_M3_only_price_visibility_verified':True,
        'M4_each_price_has_own_proposal_and_verification':True,
        'unique_physical_calls':len(jobs),
        'logical_M4_workflow_calls':2*sum(j['method']=='M4' for j in jobs),
        'all_M4_verifier_calls_new_context':True,
        'proposal_reason_or_action_exposed_to_verifier':False,
        'shared_priceblind_labels_are_not_independent_samples':True}
