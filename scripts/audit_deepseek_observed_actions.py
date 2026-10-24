"""Offline descriptive action accounting, never semantic correctness or C labels.

Typed row-bag equality with prior scripted route outputs is a single-database
diagnostic. It ignores column labels and row order but preserves multiplicity,
types, and within-row column order; it is not general SQL equivalence.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_trace_evidence as evidence
from runtime import cache_intent_recovery_preview as preview
from runtime.cache_intent_journal import Journal
from runtime.cache_intent_tools import valid_result
from scripts.prepare_staged_contract_review import load_archive, declared_backend


def describe(trace, reference_bag):
    actions, observations = [], []
    for event in trace['events']:
        action = event['parsed_action']
        if action is not None:
            actions.append(action['kind'])
        tool = event['tool']
        if tool is None or tool['raw_response'] is None:
            continue
        raw = tool['raw_response']['observation']
        bag = None
        if raw.get('status') == 'EXECUTED' and raw.get('complete') is True:
            fp = audit.fingerprints(raw['rows'])
            valid_result(dict(raw, **fp))
            bag = fp['bag_sha256']
        observations.append({
            'tool_event':tool['tool_event'],
            'tool':tool['request']['tool'],
            'status':raw.get('status'),
            'charged_units':raw.get('charged_units'),
            'visible_complete':tool['visible_observation'].get('complete') is True,
            'included_in_later_request':tool['included_in_later_saved_request'],
            'typed_bag_sha256':bag,
            'bag_equals_scripted_witness':None if bag is None else bag == reference_bag,
        })
    return {'actions':actions, 'tool_observations':observations,
            'termination':trace['termination'], 'final_text_correctness':None,
            'program_semantic_equivalence':None, 'contract_semantic_label':None,
            'C_label':None}


def run(run_path, manifest_sha, witness, witness_sha, plan_sha, output):
    root, witness, output = Path(run_path).resolve(), Path(witness).resolve(), Path(output).resolve()
    if any(output == p or output.is_relative_to(p) for p in (root, witness)):
        raise ValueError('Keep derived report outside source archives')
    raw, manifest = load_archive(root, manifest_sha, include_trace=True)
    backend = declared_backend(raw)
    plan = json.loads(raw['batch/plan.json'])
    if audit.digest(plan) != plan_sha or len(plan['jobs']) != 12 or plan['independent_targets'] != 2:
        raise ValueError('Unexpected fixed plan')
    _, witness_manifest = load_archive(witness, witness_sha)
    def witness_read(name):
        if audit.hash_file(witness/name) != witness_manifest[name]:
            raise ValueError('Witness changed')
        return json.loads((witness/name).read_text('utf-8'))
    reference = witness_read('report.json')
    if (reference['status'] != 'LOCAL_SCRIPTED_ROUTE_AUDIT_NOT_MODEL_EXPERIMENT'
            or reference['model_calls'] != 0 or not reference['database_unchanged']
            or reference['database_sha256_before'] != reference['database_sha256_after']):
        raise ValueError('Wrong witness kind or database consistency')
    refs = {}
    for row in reference['routes']:
        # These are the four already sealed late-recovery witnesses, not model samples.
        if row['route_id'] not in {f'h{t}c-p{p}-delay20' for t in (200,234) for p in (1,8)}:
            raise ValueError('Unexpected witness route')
        response = witness_read(row['route_id']+'/tools/attempt-021.completed.json')['response']
        if response.get('complete') is not True or response.get('status') != 'EXECUTED':
            raise ValueError('Incomplete witness')
        bag = audit.fingerprints(response['rows'])['bag_sha256']
        if bag != row['sql_result_bag_sha256'] or refs.setdefault(row['target_id'],bag) != bag:
            raise ValueError('Inconsistent witness outputs')
    if set(refs) != {200,234} or len(reference['routes']) != 4:
        raise ValueError('Missing witness coverage')
    rows = []
    for job in plan['jobs']:
        packet = preview.validate_preview(job['envelope'])
        if packet['context']['database_sha256'] != reference['database_sha256_before']:
            raise ValueError('Model run and witness database differ')
        prefix = job['id']+'/trace/'
        records = {n[len(prefix):-5]:json.loads(v) for n,v in raw.items() if n.startswith(prefix)}
        trace = evidence.inspect(job['envelope'], records)
        target = int(job['history_id'][1:4])
        rows.append({'job_id':job['id'], 'read_price':job['read_price'],
                     'target_id':target, **describe(trace,refs[target])})
    observations = [o for row in rows for o in row['tool_observations']]
    summary = {
        'status':'MECHANICAL_OBSERVED_ACTIONS_NOT_SEMANTIC_RESULTS',
        'source_backend':backend['kind'],
        'trajectories':len(rows), 'independent_development_targets':2,
        'action_counts':dict(Counter(a for r in rows for a in r['actions'])),
        'tool_status_counts':dict(Counter(o['status'] for o in observations)),
        'charged_relative_units':sum(o['charged_units'] for o in observations),
        'completed_sql_observations':sum(o['tool']=='run_sql' and o['status']=='EXECUTED' for o in observations),
        'sql_observations_matching_witness_bag':sum(o['tool']=='run_sql' and o['bag_equals_scripted_witness'] is True for o in observations),
        'primary_semantic_labels_generated':False, 'C_labels_generated':False,
        'causal_effect_established':False, 'general_SQL_equivalence_established':False,
        'final_answer_accuracy':None, 'new_model_calls':0, 'new_SQL_queries':0,
        'limitations':['Witness equality is not proof of semantic correctness.',
                       'Final natural-language answers have not been semantically scored.',
                       'No-cache-read does not imply cache descriptions had no influence.',
                       'Two reused development tasks do not support population inference.'],
    }
    journal = Journal(output)
    journal('private-details', {'source_manifest_sha256':manifest_sha,
        'witness_manifest_sha256':witness_sha, 'plan_sha256':plan_sha, 'rows':rows})
    journal('summary', summary)
    journal('audit-source', {'source_utf8':Path(__file__).read_text('utf-8')})
    journal.seal()
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('run','manifest-sha','witness','witness-sha','plan-sha','output'):
        parser.add_argument('--'+name, required=True)
    a = parser.parse_args()
    print(json.dumps(run(a.run,a.manifest_sha,a.witness,a.witness_sha,a.plan_sha,a.output),indent=2))
