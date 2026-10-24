"""Offline input/capacity snapshot. No live execution option or authorization."""
import argparse
import json
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime.cache_action_preview import build_plan, validate, payload
from runtime.cache_action_capacity import ActionCapacity
from runtime.cache_documentation_inputs import build as documented_plan, HISTORIES
from runtime.cache_intent_entry import load_private_inputs, FIXED_INPUT_SHA, FIXED_PLAN_SHA
from runtime.cache_intent_tools import valid_result
from scripts.prepare_staged_contract_review import load_archive
from scripts.run_cache_workflow import counter_for
from scripts.audit_deepseek_cache_controls import PREPARED_SHA
from scripts.fetch_bird_fixed import no_link_ancestors

DOCUMENTED_INPUT_SHA = '9b81cd75f0b15d57887075da803ffa13ba3b3fc2bdc9431f63ed4670815b0210'
SOURCES = ('runtime/cache_action_preview.py','runtime/cache_action_horizon.py',
           'runtime/cache_action_loop.py','runtime/cache_action_capacity.py',
           'runtime/cache_action_adapter.py','runtime/cache_intent_tools.py',
           'scripts/prepare_cache_action_inputs.py')


def prepare(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    no_link_ancestors(output)
    if output.exists(): raise ValueError('Never overwrite prepared inputs')
    prior = project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450'
    docs = project/'runtime/runs/cache-documentation-inputs-20261024-001'
    load_archive(docs, DOCUMENTED_INPUT_SHA)
    if audit.hash_file(prior/'prepared_manifest.json') != PREPARED_SHA:
        raise ValueError('Prior preparation changed')
    manifest = json.loads((prior/'prepared_manifest.json').read_text('utf-8'))
    old, caches, _ = load_private_inputs(prior/'inputs', FIXED_INPUT_SHA, FIXED_PLAN_SHA)
    if documented_plan(old) != json.loads((docs/'PRIVATE_payloads.json').read_text('utf-8')):
        raise ValueError('Prior provenance-checked documentation inputs differ')
    provenance = json.loads((docs/'provenance.json').read_text('utf-8'))
    database = Path(manifest['database']); no_link_ancestors(database)
    db_sha = audit.hash_file(database)
    if (db_sha != manifest['database_sha256'] or db_sha != provenance['database_sha256']
            or any(Path(str(database)+s).exists() for s in ('-wal','-shm','-journal'))):
        raise ValueError('Database identity or sidecar mismatch')
    for hid in HISTORIES: valid_result(caches[hid])
    plan = build_plan(old)
    counter = counter_for(manifest)
    certificates = []
    for job in plan['jobs']:
        packet = validate(job['envelope'])
        if (packet['context']['database_sha256'] != db_sha
                or audit.digest(caches[job['history_id']]) != job['cached_result_sha256']):
            raise ValueError('Cache/database binding changed')
        capacity = ActionCapacity(job['envelope'], counter)
        actual = capacity(payload(job['envelope']['messages']))
        certificates.append(dict(id=job['id'], **capacity.certificate(),
                                 initial_payload_sha256=actual['payload_sha256']))
    for hid in HISTORIES:
        pair = sorted((j for j in plan['jobs'] if j['history_id']==hid), key=lambda j:j['available'])
        a, b = (validate(j['envelope']) for j in pair)
        if a['context']['cache']['materialized_available'] is not False or b['context']['cache']['materialized_available'] is not True:
            raise ValueError('Expected availability contrast')
        a['context']['cache']['materialized_available'] = True
        if a != b or pair[0]['envelope']['messages'][0] != pair[1]['envelope']['messages'][0]:
            raise ValueError('More than availability changed')
    output.mkdir(parents=True,exist_ok=False)
    audit.save_new(output/'PRIVATE_plan.json', plan)
    audit.save_new(output/'PRIVATE_caches.json', {h:caches[h] for h in HISTORIES})
    audit.save_new(output/'capacity.json', certificates)
    total_input = sum(c['total_input_bound'] for c in certificates)
    total_output = sum(c['total_output_bound'] for c in certificates)
    receipt = dict(status='OFFLINE_INPUTS_AND_CONDITIONAL_CAPACITY_CHECKED',
        prior_prepared_sha256=PREPARED_SHA, prior_input_sha256=FIXED_INPUT_SHA,
        prior_plan_sha256=FIXED_PLAN_SHA, documented_input_sha256=DOCUMENTED_INPUT_SHA,
        database_sha256=db_sha, plan_sha256=audit.digest(plan),
        source_hashes={name:audit.hash_file(project/name) for name in SOURCES},
        source_scope='Selected current components, NOT a complete executable source snapshot',
        jobs=len(plan['jobs']), histories=6, reused_development_tasks=2, max_calls=48,
        total_input_conditional_bound=total_input,total_output_bound=total_output,
        max_full_context_with_output=max(c['full_history_with_output_bound'] for c in certificates),
        reference_initial_input_min=min(c['initial_reference_tokens'] for c in certificates),
        reference_initial_input_max=max(c['initial_reference_tokens'] for c in certificates),
        historical_planning_rates_micro_cny_per_token=[2,8],
        conditional_micro_cny_at_historical_rates=total_input*2+total_output*8,
        pricing_status='Historical assumptions only; recheck current official prices before approval.',
        new_model_calls=0,new_SQL_queries=0,inference_authorized=False,
        remaining=['Full source/launcher and batch finalization freeze',
                   'Independent final-answer grading preparation',
                   'Current pricing verification and fresh bounded approval'])
    audit.save_new(output/'receipt.json',receipt)
    audit.save_new(output/'artifact_hashes.json',{
        p.name:audit.hash_file(p) for p in sorted(output.iterdir()) if p.is_file()})
    return dict(**receipt,artifact_manifest_sha256=audit.hash_file(output/'artifact_hashes.json'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parents[1],args.output),indent=2))
