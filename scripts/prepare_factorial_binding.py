"""Bind the four reviewed inputs to archived history rows without executing SQL.

This creates private batch inputs, not a complete deployment or paid approval.
The archived result is copied unchanged except for a separately documented SQL
hash binding. No reference answer is put into model messages or batch jobs.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

from runtime import bird_readonly_audit as audit
from runtime.cache_action_factorial_batch import build_plan, validate
from runtime.cache_action_factorial_capacity import validate_cell
from runtime.cache_action_factorial_preview import SETTINGS
from runtime.cache_intent_tools import valid_result
from scripts.audit_amendment_semantics import verify_sealed
from scripts.audit_cache_action import no_links, snapshot

INPUT_SEAL='5eb5a9aa8ef132b6ac3411fc9925a7926bb7efac75812df4f8deb1a55446df78'
INPUT_PLAN_SHA='1ec6fe7bd3c6ab050c3dfe3399890d2bdf4921b0567ef387f0ea29b697173aa5'
SNAPSHOT_SEAL='164a6dd7fd2662a709ebec4188775a4d4f574de37bc4483ea598056485a21415'


def bind_history(record, registered_sql, cells):
    valid_result(record)
    if 'sql_sha256' in record:
        raise ValueError('Expected original unbound archive, not a previous derivative')
    if any(validate_cell(c)['history']['sql'] != registered_sql for c in cells):
        raise ValueError('Visible history SQL differs from archived execution design')
    result=deepcopy(record)
    result['sql_sha256']=audit.digest(registered_sql)
    return result


def run(project, output):
    project=Path(project).resolve(); output=Path(output).resolve(); no_links(output)
    if output.exists(): raise ValueError('Refuse overwrite')
    inputs=project/'runtime/runs/superhero-factorial-inputs-20261025-001'
    archive=project/'runtime/runs/superhero-snapshot-audit-20261025-001'
    verify_sealed(inputs,INPUT_SEAL); verify_sealed(archive,SNAPSHOT_SEAL)
    if audit.hash_file(inputs/'PRIVATE_plan.json') != INPUT_PLAN_SHA:
        raise ValueError('Unexpected reviewed input plan')
    source=json.loads((inputs/'PRIVATE_plan.json').read_text('utf-8'))
    admission=project/'configs/run_records/superhero_amendment_admission_20261025_001.json'
    if audit.hash_file(admission) != source['admission_receipt_sha256']:
        raise ValueError('Admission receipt changed')
    indexed={j['id']:j['envelope'] for j in source['jobs']}
    cells=[indexed[f'available{int(a)}-price{p}'] for a,p in SETTINGS]
    design=json.loads((archive/'design.json').read_text('utf-8'))
    if any(validate_cell(c)['context']['database_sha256'] != design['database_before']['sha256'] for c in cells):
        raise ValueError('Cache and requested database versions differ')
    original=json.loads((archive/'history_728.json').read_text('utf-8'))
    cache=bind_history(original,design['queries']['history_728'],cells)
    plan=build_plan(cells,cache,INPUT_PLAN_SHA)
    validate(plan,cache,audit.digest(plan))
    output.mkdir(parents=True,exist_ok=False)
    audit.save_new(output/'PRIVATE_cache.json',cache)
    audit.save_new(output/'PRIVATE_batch_plan.json',plan)
    receipt=dict(status='BOUND_INPUTS_NOT_DEPLOYABLE',input_plan_file_sha256=INPUT_PLAN_SHA,
        input_archive_seal=INPUT_SEAL,history_archive_seal=SNAPSHOT_SEAL,
        original_result_file_sha256=audit.hash_file(archive/'history_728.json'),
        original_result_object_sha256=audit.digest(original),bound_result_object_sha256=audit.digest(cache),
        transformation='Only added sql_sha256 from the exact history_728 query in the sealed execution design',
        sql_binding=cache['sql_sha256'],database_sha256=design['database_before']['sha256'],
        plan_object_sha256=audit.digest(plan),cells=4,independent_task_pairs=1,
        max_model_calls=16,max_tool_attempts=12,new_SQL_queries=0,model_calls=0,
        inference_authorized=False,source_closure_frozen=False,
        remaining='Freeze complete executable sources/dependencies, verify current official prices/settings, obtain fixed-batch authorization')
    audit.save_new(output/'binding.json',receipt)
    for name in ('scripts/prepare_factorial_binding.py','runtime/cache_action_factorial_batch.py',
                 'runtime/cache_action_factorial_loop.py'):
        shutil.copyfile(project/name,output/Path(name).name)
    audit.save_new(output/'artifact_hashes.json',snapshot(output))
    seal=audit.hash_file(output/'artifact_hashes.json'); verify_sealed(output,seal)
    return dict(status=receipt['status'],plan_object_sha256=receipt['plan_object_sha256'],
                archive_sha256=seal,model_calls=0,new_SQL_queries=0)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],args.output),indent=2))
