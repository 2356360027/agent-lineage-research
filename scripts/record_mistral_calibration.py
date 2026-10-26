"""Seal failed calibration and zero-generation diagnosis without erasing either."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_local_separation import save,sha
from scripts.run_reuse_development import verify_prepared,verify_index
from runtime.reuse_separation import parse_response


def main():
    run=ROOT/'runtime/runs/reuse-mistral-calibration-prepared-20261027-001'
    diag=ROOT/'runtime/runs/reuse-mistral-token-diagnostic-20261027-001'
    manifest='d4d255567d0ee46952f79360c2b0893dc22189e1d1a5224a61f734bce60a450b'
    verify_prepared(run,manifest);verify_index(run/'batch-00');verify_index(diag)
    term=json.loads((run/'batch-00/terminal.json').read_bytes())
    dt=json.loads((diag/'terminal.json').read_bytes())
    diagnosis=json.loads((diag/'diagnosis.json').read_bytes())
    raw=json.loads((run/'batch-00/000.response.json').read_bytes())
    prior=json.loads((ROOT/'configs/run_records/reuse_confirmation_qwen_20261027.json').read_bytes())
    if (term['status']!='FAILED' or term['dispatch_attempts']!=1 or term['responses_recorded']!=1
        or not term['owned_server_stopped'] or dt['status']!='COMPLETED'
        or not dt['owned_server_stopped'] or dt['new_model_generations']!=0
        or not diagnosis['all_true_tokens_equal_one_BOS_plus_false']
        or diagnosis['matching_add_special_true']!=raw['usage']['prompt_tokens']):
        raise ValueError('Unexpected calibration or diagnosis state')
    usage=raw['usage']
    output=ROOT/'configs/run_records/reuse_mistral_calibration_20261027.json'
    result={'status':'CALIBRATION_ABORTED_TOKEN_ACCOUNTING_DIAGNOSED_NOT_RESUMED',
        'prepared':str(run.relative_to(ROOT)).replace('\\','/'),'manifest_sha256':manifest,
        'planned_calls':40,'dispatch_attempts':1,'responses_recorded':1,'unattempted':39,
        'failure':'add_special=false preflight gave624; actual completion used625',
        'reported_usage':usage,'usage_validated_during_original_run':False,
        'usage_match_explained_by_later_zero_generation_diagnostic':True,
        'original_response_format_valid':parse_response(raw,'M1')['valid'],
        'original_response_finish_reason':raw['choices'][0]['finish_reason'],
        'raw_and_failed_manifest_preserved':True,'effect_estimate_not_reported':True,
        'calibration_owned_service_seconds':term['owned_server_seconds'],
        'diagnostic_owned_service_seconds':dt['owned_server_seconds'],
        'diagnostic_variants':diagnosis['variants_checked'],'diagnostic_model_calls':0,
        'diagnostic_archive':str(diag.relative_to(ROOT)).replace('\\','/'),
        'diagnostic_index_sha256':sha(diag/'sha256.json'),
        'failed_batch_index_sha256':sha(run/'batch-00/sha256.json'),
        'goal_new_attempts_total':prior['goal_new_attempts_total']+1,
        'goal_service_seconds_total':prior['goal_service_seconds_total']+term['owned_server_seconds']+dt['owned_server_seconds'],
        'automatic_retry':False,'replacement_of_failed_calls':False,'paid_API_cny':0,
        'next_gate':'No second40-call rerun. A separately frozen held-out matrix may use the corrected exact '
          'add_special=true count. All remaining39 calibration cells remain missing. Stop on any renewed count/transport failure; '
          'keep all invalid outputs. No prompt, model, seed or case changes based on response correctness.'}
    save(output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
