"""Freeze admitted development inputs offline; no executable API launcher.

All four cells share reviewed history, current request, schema and horizon.
Result rows and semantic admission labels are not initial model messages.
"""
import argparse
import json
from pathlib import Path
import shutil

from runtime import bird_readonly_audit as audit
from runtime.arcwise_compatibility import verify_arcwise
from runtime.cache_action_factorial_preview import make_cells, audit_cells
from runtime.cache_action_factorial_capacity import FactorialCapacity
from runtime.cache_action_preview import payload
from scripts.audit_amendment_semantics import verify_sealed, AMENDMENT_SEAL
from scripts.audit_cache_action import no_links, snapshot
from scripts.audit_superhero_interface import SNAPSHOT_SEAL, observation
from scripts.audit_superhero_snapshot import DB_SHA
from scripts.audit_native_positive_relation import SOURCE_SHA
from scripts.audit_native_555 import decode_scalar
from scripts.run_cache_workflow import counter_for, OLD_MANIFEST_SHA

ADMISSION = 'configs/run_records/superhero_amendment_admission_20261025_001.json'
SEED = 'superhero-factorial-feasibility-v1'


def run(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    no_links(output)
    if output.exists(): raise ValueError('Refuse overwrite')
    amended = project/'runtime/runs/superhero-amendment-review-20261025-001'
    previous = project/'runtime/runs/superhero-snapshot-audit-20261025-001'
    verify_sealed(amended, AMENDMENT_SEAL)
    verify_sealed(previous, SNAPSHOT_SEAL)
    adaptation = json.loads((amended/'adaptation.json').read_text('utf-8'))
    admission = json.loads((project/ADMISSION).read_text('utf-8'))
    if (admission['status'] != 'SEMANTIC_GATE_PASSED_FOR_ADAPTED_DEVELOPMENT_PAIR'
            or admission['reviewed_packet_manifest_sha256'] != AMENDMENT_SEAL
            or admission['history_variant'] != adaptation['version']
            or not admission['review']['reviewer_1_record_ID_count_condition_resolved_by_user_forwarded_answer']):
        raise ValueError('Missing or mismatched semantic admission')
    source = project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001'
    _, records = verify_arcwise(source)
    if audit.hash_file(source/'arcwise_plat_full_with_diff.json') != SOURCE_SHA:
        raise ValueError('Unexpected source bytes')
    for key, qid in (('original_728',728), ('unchanged_733',733)):
        if adaptation[key] != {k:records[qid][k] for k in ('question','evidence')}:
            raise ValueError('Reviewed original text differs')
    db = project/'runtime/runs/datasets/superhero-fixed-20261025-002/databases/superhero/superhero.sqlite'
    no_links(db)
    if audit.hash_file(db) != DB_SHA or any(Path(str(db)+s).exists() for s in ('-wal','-shm','-journal')):
        raise ValueError('Database changed or has sidecars')
    manifest = project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450/prepared_manifest.json'
    if audit.hash_file(manifest) != OLD_MANIFEST_SHA: raise ValueError('Tokenizer manifest changed')
    counter = counter_for(json.loads(manifest.read_text('utf-8')))
    schema_record = json.loads((previous/'schema.json').read_text('utf-8'))
    schema = [decode_scalar(row[1]) for row in schema_record['rows']]
    history = dict(historical_request=records[728]['question']+'\n\n'+adaptation['additional_researcher_instruction'],
                   evidence=records[728]['evidence'], sql=records[728]['SQL'])
    cells = make_cells(records[733]['question'],records[733]['evidence'],schema,history,DB_SHA)
    factor_check = audit_cells(cells)
    history_result = json.loads((previous/'history_728.json').read_text('utf-8'))
    current_result = json.loads((previous/'current_733.json').read_text('utf-8'))
    jobs = []
    for cell in cells:
        packet = json.loads(cell['messages'][1]['content'])
        settings = packet['context']['cache']
        available, price = settings['materialized_available'], settings['read_cost']
        capacity = FactorialCapacity(cell,counter)
        initial_check = capacity(payload(cell['messages']))
        exposures = {}
        for name, record, charge in (('history',history_result,price),('current',current_result,10)):
            exposure = capacity.observation_bound().apply(observation(record,charge,30-charge,1))
            if exposure['rows_partially_exposed'] or exposure['observation']['status'] != 'EXECUTED':
                raise ValueError('Full trusted result not exposed: '+name)
            exposures[name] = {k:exposure[k] for k in ('raw_framed_tokens','exposed_framed_tokens','rows_partially_exposed')}
        jobs.append(dict(id=f'available{int(available)}-price{price}', envelope=cell,
                         capacity=capacity.certificate(), initial_check=initial_check,
                         first_observation_checks=exposures))
    jobs.sort(key=lambda j:audit.digest(SEED+j['id']))
    plan = dict(status='INPUTS_FROZEN_NOT_EXECUTION_READY', seed=SEED, jobs=jobs,
                independent_task_pairs=1, development_only=True, max_model_calls=16,
                max_tool_attempts=12, max_output_tokens_per_call=512,
                admission_receipt_sha256=audit.hash_file(project/ADMISSION),
                amendment_seal=AMENDMENT_SEAL, snapshot_seal=SNAPSHOT_SEAL,
                database_sha256=DB_SHA, inference_authorized=False,
                purpose='Ordinary-workflow sufficient-history positive-control feasibility; no direct identification of prior intent drift')
    output.mkdir(parents=True, exist_ok=False)
    for name in ('scripts/prepare_superhero_factorial_inputs.py','runtime/cache_action_factorial_preview.py',
                 'runtime/cache_action_factorial_capacity.py','runtime/cache_action_capacity.py',
                 'runtime/cache_action_preview.py',ADMISSION):
        shutil.copyfile(project/name,output/Path(name).name)
    audit.save_new(output/'PRIVATE_plan.json',plan)
    audit.save_new(output/'factor_audit.json',factor_check)
    summary = dict(status=plan['status'],task_pairs=1,cells=4,max_model_calls=16,max_tool_attempts=12,
                   total_input_bound=sum(j['capacity']['total_input_bound'] for j in jobs),
                   total_output_bound=sum(j['capacity']['total_output_bound'] for j in jobs),
                   initial_tokens=[j['capacity']['initial_reference_tokens'] for j in jobs],
                   all_first_results_fully_exposed=True, new_SQL_queries=0, model_calls=0,
                   financial_quote_verified=False, inference_authorized=False,
                   remaining='Matching loop/batch/transport/deadline/fee enforcement, failure tests, frozen launcher, current pricing and explicit authorization. This is not a complete deployable code snapshot.')
    audit.save_new(output/'summary.json',summary)
    audit.save_new(output/'artifact_hashes.json',snapshot(output))
    verify_sealed(output,audit.hash_file(output/'artifact_hashes.json'))
    return dict(summary,plan_sha256=audit.hash_file(output/'PRIVATE_plan.json'),
                manifest_sha256=audit.hash_file(output/'artifact_hashes.json'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],args.output),indent=2))
