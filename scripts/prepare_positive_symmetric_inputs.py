"""Offline remapping of an admitted positive control; no API or SQL execution.

This is an input/capacity archive, not a launchable or authorized experiment.
Historical batch output is never used to select cells or alter task wording.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

from runtime import bird_readonly_audit as audit
from runtime.cache_route_symmetric_design import build, audit as audit_cells
from runtime.cache_route_symmetric_batch import build_plan
from runtime.cache_route_symmetric_capacity import SymmetricCapacity
from runtime.cache_intent_tools import valid_result
from scripts.run_cache_action import verify_archive
from scripts.run_cache_workflow import counter_for, OLD_MANIFEST_SHA
from scripts.fetch_bird_fixed import no_link_ancestors, absent, read_json
from scripts.audit_superhero_interface import SNAPSHOT_SEAL, observation
from scripts.audit_amendment_semantics import AMENDMENT_SEAL

INPUT_SEAL = '5eb5a9aa8ef132b6ac3411fc9925a7926bb7efac75812df4f8deb1a55446df78'
DATABASE_SHA = '75e94a2c3236ee3bb2c01fb97a1c4b4c1c269bcefd4eab1d04be323d2d0825b1'
ADMISSION = 'configs/run_records/superhero_amendment_admission_20261025_001.json'


def remap(plan):
    """Preserve every admitted task/evidence byte; discard only old interface state."""
    jobs = plan['jobs']
    if len(jobs) != 4 or len({j['id'] for j in jobs}) != 4:
        raise ValueError('Four distinct source cells required')
    packets = [json.loads(j['envelope']['messages'][1]['content']) for j in jobs]
    expected = {'context','current_request','evidence','history','schema'}
    if any(set(p) != expected for p in packets):
        raise ValueError('Unexpected source fields')
    stripped = [{k:v for k,v in p.items() if k != 'context'} for p in packets]
    if any(p != stripped[0] for p in stripped):
        raise ValueError('Source common information differs between cells')
    if any(p['context']['database_sha256'] != DATABASE_SHA for p in packets):
        raise ValueError('Source snapshot differs')
    p = packets[0]; h = p['history']
    if set(h) != {'historical_request','evidence','sql'}:
        raise ValueError('Unexpected history fields')
    return dict(shared_contract='Use the same fixed database snapshot for both tasks.',
        history_contract=h['historical_request']+'\n\nEvidence:\n'+h['evidence'],
        current_contract=p['current_request']+'\n\nEvidence:\n'+p['evidence'],
        schema=deepcopy(p['schema']),history_sql=h['sql'],database_sha256=DATABASE_SHA)


def run(project, output):
    project = Path(project).resolve(); output = Path(output).absolute()
    no_link_ancestors(output); absent(output)
    prior = project/'runtime/runs/superhero-factorial-inputs-20261025-001'
    snapshot = project/'runtime/runs/superhero-snapshot-audit-20261025-001'
    amended = project/'runtime/runs/superhero-amendment-review-20261025-001'
    for path, seal in [(prior,INPUT_SEAL),(snapshot,SNAPSHOT_SEAL),(amended,AMENDMENT_SEAL)]:
        verify_archive(path,seal)
    source_plan = read_json(prior/'PRIVATE_plan.json')
    admission = read_json(project/ADMISSION)
    if (audit.hash_file(project/ADMISSION) != source_plan['admission_receipt_sha256']
            or admission['status'] != 'SEMANTIC_GATE_PASSED_FOR_ADAPTED_DEVELOPMENT_PAIR'):
        raise ValueError('Source admission changed')
    common = remap(source_plan)
    adaptation = read_json(amended/'adaptation.json')
    expected_history = adaptation['original_728']['question']+'\n\n'+adaptation['additional_researcher_instruction']+'\n\nEvidence:\n'+adaptation['original_728']['evidence']
    expected_current = adaptation['unchanged_733']['question']+'\n\nEvidence:\n'+adaptation['unchanged_733']['evidence']
    if common['history_contract'] != expected_history or common['current_contract'] != expected_current:
        raise ValueError('Reviewed wording differs')
    db = project/'runtime/runs/datasets/superhero-fixed-20261025-002/databases/superhero/superhero.sqlite'
    no_link_ancestors(db)
    if audit.hash_file(db) != DATABASE_SHA or any(Path(str(db)+s).exists() for s in ('-wal','-shm','-journal')):
        raise ValueError('Changed database or sidecars')
    cache = read_json(snapshot/'history_728.json'); reference = read_json(snapshot/'current_733.json')
    snapshot_design = read_json(snapshot/'design.json')
    if (snapshot_design['queries']['history_728'] != common['history_sql']
            or snapshot_design['database_before']['sha256'] != DATABASE_SHA):
        raise ValueError('Archived result/SQL binding differs')
    valid_result(cache); valid_result(reference)
    cache['sql_sha256'] = audit.digest(common['history_sql'])
    plan = build_plan(common,cache,audit.hash_file(prior/'PRIVATE_plan.json'))
    tokenizer_manifest = project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450/prepared_manifest.json'
    if audit.hash_file(tokenizer_manifest) != OLD_MANIFEST_SHA: raise ValueError('Tokenizer metadata changed')
    counter = counter_for(read_json(tokenizer_manifest))
    capacities = []; exposures = []
    for job in plan['jobs']:
        capacity = SymmetricCapacity(job['envelope'],common,counter)
        capacities.append(dict(id=job['id'],**capacity.certificate()))
        for name, result, cost in [('cache',cache,20),('SQL',reference,10)]:
            # Local serialization only: not a live read_cache or run_sql action.
            raw = observation(result,cost,60-cost,1)
            exposed = capacity.observation_bound().apply(raw)
            if exposed['observation'] != raw: raise ValueError('Complete result does not fit')
            exposures.append(dict(id=job['id'],kind=name,framed_tokens=exposed['exposed_framed_tokens']))
    summary = dict(status='OFFLINE_POSITIVE_SYMMETRIC_INPUTS_CHECKED_NOT_LAUNCHABLE',
        independent_development_pairs=1,cells=4,model_calls=0,new_SQL_queries=0,
        paid_execution_authorized=False,common_wording_preserved=True,
        total_conditional_input_tokens=sum(c['total_input_bound'] for c in capacities),
        total_conditional_output_tokens=sum(c['total_output_bound'] for c in capacities),
        complete_results_fit=True,pricing_verified=False,
        previous_model_outputs_read=False,
        limitation='Same symmetric protocol, different task and schema content; cross-task differences are not randomized causal effects. Old asymmetric results are not pooled.',
        remaining=['Full live readonly binding and scripted route checks','Frozen launcher/source closure and prospective scoring','Current price check and explicit new paid approval'])
    output.mkdir(parents=True,exist_ok=False)
    for name,value in [('PRIVATE_common.json',common),('PRIVATE_plan.json',plan),
            ('PRIVATE_cache.json',cache),('PRIVATE_reference.json',reference),('summary.json',summary),
            ('PRIVATE_source_queries.json',snapshot_design['queries']),
            ('capacities.json',capacities),('exposures.json',exposures),
            ('factor_audit.json',audit_cells(build(common),common)),
            ('binding.json',dict(source_input_seal=INPUT_SEAL,snapshot_seal=SNAPSHOT_SEAL,
                 amendment_seal=AMENDMENT_SEAL,admission_sha256=audit.hash_file(project/ADMISSION),
                 source_plan_sha256=audit.hash_file(prior/'PRIVATE_plan.json'),database_sha256=DATABASE_SHA))]:
        audit.save_new(output/name,value)
    sources = {Path(__file__).resolve()}
    for module in tuple(sys.modules.values()):
        name = getattr(module,'__file__',None)
        if name:
            path = Path(name).resolve()
            if path.is_relative_to(project) and path.suffix == '.py': sources.add(path)
    for path in sources:
        dest = output/'source'/path.relative_to(project)
        dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(path,dest)
    audit.save_new(output/'artifact_hashes.json',{p.relative_to(output).as_posix():audit.hash_file(p)
        for p in sorted(output.rglob('*')) if p.is_file()})
    return dict(summary,archive_sha256=audit.hash_file(output/'artifact_hashes.json'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],args.output),indent=2))
