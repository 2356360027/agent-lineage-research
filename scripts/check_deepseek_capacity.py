"""Offline capacity receipts for the fixed 12 development trajectories. No API."""
import argparse
from pathlib import Path
import sys
import json

from runtime import cache_intent_entry as entry
from runtime import bird_readonly_audit as audit
from runtime.deepseek_text_counter import ReferenceCounter
from runtime.deepseek_recovery_capacity import RecoveryCapacity


def run(inputs, reference, dependencies, output):
    plan,caches,_ = entry.load_private_inputs(inputs,entry.FIXED_INPUT_SHA,entry.FIXED_PLAN_SHA)
    sys.path.insert(0,str(Path(dependencies).resolve()))
    counter = ReferenceCounter(Path(reference)/'tokenizer.json')
    rows = []
    for job in plan['jobs']:
        capacity = RecoveryCapacity(job['envelope'],counter)
        # Fixed historical result only, not a new SQL execution or a model result.
        raw = dict(caches[job['history_id']], status='EXECUTED',charged_units=job['read_price'],
                   remaining_budget=210-job['read_price'],attempt=1)
        exposure = capacity.observation_bound().apply(raw)
        rows.append({'job_id':job['id'],**capacity.certificate(),
                     'historical_result_exposure_tokens':exposure['exposed_framed_tokens'],
                     'historical_result_complete':exposure['observation'].get('complete'),
                     'historical_result_rows_partially_exposed':exposure['rows_partially_exposed']})
    result = {'status':'OFFLINE_CONDITIONAL_CAPACITY_CHECK', 'rows':rows,
        'input_manifest_sha256':entry.FIXED_INPUT_SHA,'plan_sha256':entry.FIXED_PLAN_SHA,
        'model_calls':0,'sql_queries':0,'research_samples':0,'inference_authorized':False,
        'raw_task_or_review_material_in_receipt':False,
        'maximum_bound':max(r['full_history_bound_including_completion'] for r in rows),
        'total_input_bound':sum(r['total_input_bound'] for r in rows),
        'total_output_bound':sum(r['total_output_bound'] for r in rows),
        'conditional_peak_cost_cny':sum(r['total_input_bound']*2+r['total_output_bound']*8 for r in rows)/1_000_000,
        'cost_source':'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
        'cost_source_checked':'2026-10-24',
        'cost_assumes_peak_uncached_input_2_and_output_8_cny_per_million':True,
        'cost_is_not_provider_invoice_or_spending_cap':True,
        'limitations':['Conditional on every recorded runtime content/join check',
                      'Provider count parity must be checked at every response',
                      'Not a provider spending limit or proof of semantic correctness',
                      'No new paid batch authorization; no new independent tasks']}
    root = Path(output); root.mkdir(parents=True,exist_ok=False)
    audit.save_new(root/'summary.json',result)
    names = ['runtime/deepseek_recovery_capacity.py','runtime/deepseek_trajectory_adapter.py',
             'runtime/deepseek_text_counter.py','scripts/check_deepseek_capacity.py']
    repo = Path(__file__).resolve().parents[1]
    for name in names:
        target = root/'source'/name; target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream: stream.write((repo/name).read_bytes())
    audit.save_new(root/'artifact_hashes.json',{p.relative_to(root).as_posix():audit.hash_file(p)
        for p in sorted(root.rglob('*')) if p.is_file()})
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','reference','dependencies','output'): p.add_argument('--'+name,required=True)
    print(json.dumps(run(**vars(p.parse_args())),indent=2))
