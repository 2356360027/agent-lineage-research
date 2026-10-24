"""Offline interface feasibility for one pending candidate, never a live plan.

Reuses sealed results and local tokenizer only. Does not query SQLite, call a
model, emit semantic judgments, or change historical experimental interfaces.
"""
import argparse
from copy import deepcopy
import json
import shutil
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime import cache_action_preview as action
from runtime import cache_intent_recovery_preview as elicited
from runtime import cache_intent_prompts as base
from runtime.cache_action_capacity import ActionCapacity
from runtime.deepseek_recovery_capacity import RecoveryCapacity
from runtime.cache_intent_observation_bound import ObservationBound
from runtime.cache_intent_tools import valid_result
from runtime.arcwise_compatibility import verify_arcwise
from scripts.audit_native_555 import decode_scalar
from scripts.audit_cache_action import snapshot, no_links
from scripts.run_cache_workflow import counter_for, OLD_MANIFEST_SHA

SNAPSHOT_SEAL = '164a6dd7fd2662a709ebec4188775a4d4f574de37bc4483ea598056485a21415'


def verify_pair(pair, expected_field):
    left, right = [base._expanded(e) for e in pair]
    expected = ('messages', 1, 'content', 'context', 'cache', expected_field)
    diff = base._diff(left, right)
    if diff != [expected]:
        raise ValueError('Unexpected within-pair differences')
    return list(expected)


def observation(result, price, remaining, attempt):
    valid_result(result)
    return dict(status='EXECUTED', complete=True, charged_units=price,
                remaining_budget=remaining, attempt=attempt,
                columns=deepcopy(result['columns']), rows=deepcopy(result['rows']))


def run(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    no_links(output)
    if output.exists(): raise ValueError('Refuse overwrite')
    prior = project/'runtime/runs/superhero-snapshot-audit-20261025-001'
    no_links(prior)
    if audit.hash_file(prior/'artifact_hashes.json') != SNAPSHOT_SEAL:
        raise ValueError('Snapshot seal changed')
    actual = snapshot(prior); actual.pop('artifact_hashes.json')
    if actual != json.loads((prior/'artifact_hashes.json').read_text()):
        raise ValueError('Snapshot artifacts changed')
    manifest = project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450/prepared_manifest.json'
    no_links(manifest)
    if audit.hash_file(manifest) != OLD_MANIFEST_SHA: raise ValueError('Tokenizer manifest changed')
    tokenizer_manifest = json.loads(manifest.read_text())
    counter = counter_for(tokenizer_manifest)
    _, records = verify_arcwise(project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001')
    history = json.loads((prior/'history_728.json').read_text())
    current = json.loads((prior/'current_733.json').read_text())
    schema_result = json.loads((prior/'schema.json').read_text())
    if not schema_result['complete']: raise ValueError('Incomplete schema')
    schema = [decode_scalar(r[1]) for r in schema_result['rows']]
    database_sha = json.loads((prior/'design.json').read_text())['database_before']['sha256']
    inputs = dict(current_request=records[733]['question'], evidence=records[733]['evidence'],
                  schema=schema, history=dict(historical_request=records[728]['question'], sql=records[728]['SQL']),
                  database_sha256=database_sha)
    ordinary = action.make_pair(**inputs)
    instrumented = elicited.make_pair(**inputs)
    differences = {'ordinary_availability': verify_pair(ordinary, 'materialized_available'),
                   'elicited_cost': verify_pair(instrumented, 'read_cost')}
    output.mkdir(parents=True, exist_ok=False)
    modules = [__file__, action.__file__, elicited.__file__, base.__file__,
               'runtime/cache_intent_observation_bound.py', 'runtime/cache_action_capacity.py',
               'runtime/deepseek_recovery_capacity.py', 'runtime/deepseek_text_counter.py']
    for entry in modules:
        p = Path(entry)
        if not p.is_absolute(): p = project/p
        shutil.copyfile(p, output/p.name)
    audit.save_new(output/'design.json', {
        'purpose': 'OFFLINE_INTERFACE_DIAGNOSTIC_NOT_EXECUTION_PLAN',
        'snapshot_seal_sha256': SNAPSHOT_SEAL, 'tokenizer_manifest_sha256': OLD_MANIFEST_SHA,
        'tokenizer_metadata': {k: tokenizer_manifest[k] for k in ('tokenizer', 'tokenizer_dependencies', 'tokenizer_package_files')},
        'price_observations_to_check': [1, 8], 'counter_is_local_reference_only': True,
        'source_SQL_calls': 0, 'model_calls': 0, 'formal_tasks_admitted': 0,
        'native_history_evidence_gate': 'Existing history schema has only historical_request/sql. This preview does not claim to include historical evidence or constitute final native-input admission.',
    })
    try:
        audit.save_new(output/'PRIVATE_previews.json', {'ordinary_availability': ordinary, 'elicited_cost': instrumented})
        capacities = {'ordinary': [ActionCapacity(e, counter).certificate() for e in ordinary],
                      'elicited': [RecoveryCapacity(e, counter).certificate() for e in instrumented]}
        observations = {}
        bound = ObservationBound(counter.chat)
        # Initial observation states in each existing interface, not all arbitrary
        # future states, new trajectories, or a new full-horizon proof.
        states = [('ordinary_cache_1', history, 1, 29),
                  ('ordinary_recompute_10', current, 10, 20),
                  ('elicited_cache_1', history, 1, 209),
                  ('elicited_cache_8', history, 8, 202),
                  ('elicited_recompute_10', current, 10, 200)]
        for name, result, price, remaining in states:
            obs = observation(result, price, remaining, 1)
            exposed = bound.apply(obs)
            audit.save_new(output/(name+'.json'), exposed)
            observations[name] = {k: exposed[k] for k in ('raw_framed_tokens', 'exposed_framed_tokens', 'bound', 'rows_partially_exposed')}
            observations[name]['exposed_status'] = exposed['observation']['status']
        summary = dict(status='OFFLINE_INTERFACE_CHECK_COMPLETE', pair_differences=differences,
                       capacities=capacities, observations=observations,
                       new_SQL_calls=0, model_calls=0, formal_tasks_admitted=0,
                       ordinary_cost_only_protocol_implemented=False,
                       historical_evidence_in_preview=False,
                       inference_authorized=False)
        audit.save_new(output/'summary.json', summary)
    except BaseException as exc:
        audit.save_new(output/'failure.json', {'type': type(exc).__name__, 'message': str(exc)})
        raise
    finally:
        hashes = snapshot(output)
        audit.save_new(output/'artifact_hashes.json', hashes)
    actual = snapshot(output); actual.pop('artifact_hashes.json')
    if actual != hashes: raise ValueError('Archive changed')
    return {k: summary[k] for k in ('status', 'pair_differences', 'observations', 'new_SQL_calls', 'model_calls', 'formal_tasks_admitted', 'ordinary_cost_only_protocol_implemented', 'historical_evidence_in_preview', 'inference_authorized')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1], args.output), indent=2))
