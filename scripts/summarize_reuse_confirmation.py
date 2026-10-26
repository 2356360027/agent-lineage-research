"""Publish aggregate confirmation evidence after two identical frozen rescores.

No inference, prompt edits, SQL execution or raw model text is exported here.
The inferential statistics are read from the pre-outcome frozen scoring output.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_local_separation import sha,save


def stripped(contrasts):
    return {key:{k:v for k,v in value.items() if k!='paired_rows'} for key,value in contrasts.items()}


def export(prepared,audit,repeat,prior,record,readout,model):
    prepared,audit,repeat=[p.resolve() for p in (prepared,audit,repeat)]
    for name in ('summary.json','input_hashes.json'):
        if sha(audit/name)!=sha(repeat/name):raise ValueError('Frozen rescoring is not identical')
    data=json.loads((audit/'summary.json').read_bytes())
    conf=json.loads((prepared/'config.json').read_bytes())
    prior_data=json.loads(prior.read_bytes())
    if (data['scope']!='SYNTHETIC_CONFIRMATION_E1_E2_ONLY' or data['expected_calls']!=1920
        or data['planned_bases']!=96 or data['structural_templates']!=12
        or len(data['terminals'])!=10 or any(t['status']!='COMPLETED' for t in data['terminals'])
        or not data['all_started_servers_closed'] or not data['all_actual_prompts_match_freeze']
        or not data['input_manifest_reverified']):raise ValueError('Complete verified confirmation required')
    if sha(prepared/'manifest.json')!=data['manifest_sha256']:raise ValueError('Manifest mismatch')
    if conf['max_attempts_new_goal_before']!=prior_data['goal_new_attempts_total']:
        raise ValueError('Resource ledger predecessor mismatch')
    observations=data['observations'];decisions=data['decisions']
    labels={};actions={};families={}
    for method in ('M1','M2','M3','M4'):
        labels[method]={}
        for price in (1,10,20):
            selected=[r for r in observations if r['method']==method and r['price']==price]
            negatives=[r for r in selected if not r['sufficient']]
            positives=[r for r in selected if r['sufficient']]
            labels[method][str(price)]={'negative_n':len(negatives),'positive_n':len(positives),
                'negative_labels':dict(Counter(r['admissibility'] for r in negatives)),
                'positive_labels':dict(Counter(r['admissibility'] for r in positives)),
                'false_applicable':sum(r['admissibility']=='applicable' for r in negatives),
                'positive_applicable':sum(r['admissibility']=='applicable' for r in positives)}
    for method in ('M1','M2','M3','M4','M5','M6'):
        selected=[r for r in decisions if r['method']==method and r['price']==1]
        actions[method]={'negative_n':sum(not r['sufficient'] for r in selected),
            'positive_n':sum(r['sufficient'] for r in selected),
            'unsafe_reuse':sum(r['unsafe_reuse'] for r in selected),
            'useful_reuse':sum(r['useful_reuse'] for r in selected),
            'unresolved_actions':sum(r['unresolved_action'] for r in selected)}
    for family in sorted({r['family'] for r in decisions}):
        families[family]={}
        for method in actions:
            selected=[r for r in decisions if r['family']==family and r['method']==method and r['price']==1]
            families[family][method]={'negative_n':sum(not r['sufficient'] for r in selected),
                'positive_n':sum(r['sufficient'] for r in selected),
                'unsafe_reuse':sum(r['unsafe_reuse'] for r in selected),
                'useful_reuse':sum(r['useful_reuse'] for r in selected)}
    indexed={(r['case_id'],r['method'],r['price']):r for r in observations}
    agreement=[r for r in observations if r['method']=='M4']
    agreed=sum(r['admissibility']==indexed[r['case_id'],'M1',r['price']]['admissibility'] for r in agreement)
    usage=Counter();prompt_counts=[];measurement_count=0
    for batch in sorted(prepared.glob('batch-*')):
        for path in batch.glob('*.measurement.json'):
            m=json.loads(path.read_bytes());measurement_count+=1
            for key in ('prompt_tokens','completion_tokens','total_tokens'):
                if type(m['usage'].get(key)) is not int:raise ValueError('Incomplete physical token accounting')
                usage[key]+=m['usage'][key]
            if m['usage']['total_tokens']!=m['usage']['prompt_tokens']+m['usage']['completion_tokens']:
                raise ValueError('Inconsistent physical token accounting')
            prompt_counts.append(m['preflight_prompt_tokens'])
    if measurement_count!=1920:raise ValueError('Missing physical usage measurements')
    physical_status=Counter(r['admissibility'] for r in data['physical_judgments'])
    classical=json.loads((prepared/'classical_baseline.json').read_bytes())
    result={'status':'CONFIRMATION_COMPLETED_FROZEN_RESCORE_IDENTICAL','model':model,
        'scope':data['scope'],'date':'2026-10-27','prepared':str(prepared.relative_to(ROOT)).replace('\\','/'),
        'manifest_sha256':data['manifest_sha256'],'summary_sha256':sha(audit/'summary.json'),
        'input_hashes_sha256':sha(audit/'input_hashes.json'),'responses':data['responses_recorded'],
        'dispatch_attempts':data['dispatch_attempts'],'missing':data['missing_calls'],
        'planned_bases':96,'structural_templates':12,'families':6,
        'physical_judgment_status_counts':dict(physical_status),'labels':labels,'low_price_actions':actions,
        'by_family_low_price':families,'methods':data['methods'],
        'price_contrasts':stripped(data['price_contrasts']),
        'action_contrasts':stripped(data['action_contrasts']),
        'H2_M3_minus_M2_false_applicability':stripped(data['H2_M3_minus_M2_false_applicability']),
        'M4_M1_label_agreement':{'same':agreed,'planned':len(agreement),'internal_mechanism_proven':False},
        'classical':{k:v for k,v in classical.items() if k!='rows'},
        'physical_tokens':dict(usage),'prompt_token_range':[min(prompt_counts),max(prompt_counts)],
        'server_seconds':data['server_seconds'],
        'cold_start_seconds':[t['cold_start_seconds'] for t in data['terminals']],
        'seconds_including_preflight':sum(t['seconds_including_preflight'] for t in data['terminals']),
        'goal_new_attempts_total':prior_data['goal_new_attempts_total']+data['dispatch_attempts'],
        'goal_service_seconds_total':prior_data['goal_service_seconds_total']+data['server_seconds'],
        'all_services_closed':True,'source_rescoring_repeated':True,'actual_prompts_reconstructed':True,
        'model_E3_calls':0,'actual_API_cny':0,'energy_measured':False,
        'warning':data['warning']}
    save(record,result)
    text=[f'{model} — held-structure CONFIRMATION E1/E2 readout',
        '1920/1920 calls; 96 bases, 12 purposively selected structural templates, six families.',
        'Not1920 independent tasks. Eight controlled instances per template are correlated.',
        'Raw requests/responses archived, frozen-source rescoring repeated exactly; no automatic retry.',
        'Current SQL supplied to ALL methods. Not a natural-language intent-understanding benchmark.',
        'No E3 model transformation or final-answer success is inferred from these actions.','',
        'Semantic labels (each negative/positive denominator96):',
        'Method price false-applicable positive-applicable negative-label-statuses']
    for method,values in labels.items():
        for price,cell in values.items():
            text.append(f'{method} {price:>2} {cell["false_applicable"]}/96 {cell["positive_applicable"]}/96 {cell["negative_labels"]}')
    text+=['','Low-price action counts: method unsafe/96-negative useful/96-positive unresolved/192']
    for method,row in actions.items():
        text.append(f'{method}: {row["unsafe_reuse"]}/96 {row["useful_reuse"]}/96 {row["unresolved_actions"]}/192')
    text+=['','H1: low-minus-high false-applicability; BOTH preregistered output formats reported.']
    for method in ('M1','M2'):
        c=result['price_contrasts'][method]
        text.append(method+': '+json.dumps(c,ensure_ascii=False))
    text+=['','H2: price-isolated M3 minus price-visible M2 labels at low price:',
        json.dumps(result['H2_M3_minus_M2_false_applicability']['1'],ensure_ascii=False),
        '', 'H3/H4: M3 differences in unsafe AND useful low-price reuse:']
    for key,value in result['action_contrasts'].items():text.append(key+': '+json.dumps(value,ensure_ascii=False))
    text+=['',f'M4 agrees with M1 labels in {agreed}/{len(agreement)} cells. Agreement is not proof of anchoring.',
        'M3 invariance across prices is imposed sharing, not an empirical discovery.',
        'Do not combine high-price controller savings with low-price semantic accuracy.',
        'Do not call lower cost from unsafe reuse an efficiency gain.',
        'The configured classical baseline is actual Calcite/rules, not all possible optimizers.',
        'Zero observed classical error and zero-width bands do not prove population safety.',
        '', 'Resources: '+json.dumps({k:result[k] for k in ('physical_tokens','server_seconds',
            'goal_new_attempts_total','goal_service_seconds_total')},ensure_ascii=False),
        'No paid API or training. Electricity not measured. All10 owned services stopped.',
        '', 'Limits and next work:',
        'Keep development/confirmation and model families separate. No effect-selected task expansion.',
        'Model-specific source/template/transport are pinned. Cross-model conclusions require separate records.',
        'E3 actual transformations, preselected label control/verifier ablation, literature, figures,',
        'full paper and clean reproduction remain. Do not claim the14-day goal is complete.',
        '', 'Manifest SHA256: '+result['manifest_sha256'],'Summary SHA256: '+result['summary_sha256']]
    with readout.open('x',encoding='utf-8') as f:f.write('\n'.join(text)+'\n')
    print(json.dumps({k:result[k] for k in ('status','model','dispatch_attempts','summary_sha256',
        'physical_judgment_status_counts','low_price_actions','goal_new_attempts_total','goal_service_seconds_total')},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('prepared','audit','repeat','prior','record','readout'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--model',required=True)
    a=p.parse_args();export(a.prepared,a.audit,a.repeat,a.prior,a.record,a.readout,a.model)
