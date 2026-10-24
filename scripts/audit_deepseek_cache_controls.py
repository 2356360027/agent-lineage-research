"""Offline cache-availability check, NOT another agent trial or semantic judge.

Reconstruct first-read observations from sealed inputs without querying SQLite.
Only direct histories a are compared to prior same-snapshot witness bags.
Other histories are retained, not promoted to semantically valid answer routes.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_tools import valid_result
from runtime.cache_intent_observation_bound import ObservationBound
from runtime.deepseek_text_counter import ReferenceCounter
from scripts.prepare_staged_contract_review import load_archive

PREPARED_SHA = 'fb77253437f46a3b7875298b02bbe1061b50cd5e8605ce582d7cd48a2c339ce4'
INPUT_SHA = '4374a5e72939aafec5e4a02efacd5d290a4d6da7a7fbd7b4ee1633cec07c2b21'
EXPERIMENT_SHA = 'f871da06a2b123882d12ec30a0bf9b554638b5b0b31b5ec86586bdb0643536b6'
WITNESS_SHA = 'ad26ef33712ddcc71450ed33d308f8178ae2855d49090f7a0365f14fa38d1e55'


def first_read(cache, price):
    valid_result(cache)
    if type(price) is not int or price not in (1, 8):
        raise ValueError('Only frozen read prices')
    return dict(status='EXECUTED', complete=True, charged_units=price,
                remaining_budget=210-price, attempt=1,
                columns=deepcopy(cache['columns']), rows=deepcopy(cache['rows']))


def describe(cache, price, bound, reference_bag=None):
    observation = first_read(cache, price)
    exposure = bound.apply(observation)
    return dict(price=price, rows=len(cache['rows']),
        raw_framed_tokens=exposure['raw_framed_tokens'],
        visible_complete=exposure['observation'].get('complete') is True,
        visible_status=exposure['observation']['status'],
        raw_bag_equals_witness=(None if reference_bag is None else
                               cache['bag_sha256'] == reference_bag),
        raw_observation_sha256=exposure['raw_observation_sha256'])


def run(root, witness, output):
    root, witness, output = map(lambda p: Path(p).resolve(), (root,witness,output))
    if output.exists() or any(output == p or output.is_relative_to(p) for p in (root,witness)):
        raise ValueError('Use a new output outside input archives')
    if audit.hash_file(root/'prepared_manifest.json') != PREPARED_SHA:
        raise ValueError('Changed prepared manifest')
    prepared = json.loads((root/'prepared_manifest.json').read_text('utf-8'))
    project = Path(__file__).resolve().parents[1]
    names = ('runtime/cache_intent_tools.py','runtime/cache_intent_observation_bound.py',
             'runtime/deepseek_text_counter.py','runtime/bird_readonly_audit.py')
    for name in names:
        if audit.hash_file(project/name) != prepared['source_files'][name]:
            raise ValueError('Reconstruction differs from frozen source: '+name)
    _, inputs = load_archive(root/'inputs', INPUT_SHA)
    raw, _ = load_archive(root/'experiment', EXPERIMENT_SHA)
    _, witnesses = load_archive(witness, WITNESS_SHA)
    caches = json.loads((root/'inputs/PRIVATE_caches.json').read_text('utf-8'))
    plan = json.loads(raw['batch/plan.json'])
    if audit.hash_file(root/'inputs/PRIVATE_plan.json') != audit.digest(plan):
        raise ValueError('Experiment plan and prepared plan differ')
    expected = {f'h{t}{h}-p{p}' for t in (200,234) for h in 'abc' for p in (1,8)}
    if len(plan['jobs']) != 12 or {j['id'] for j in plan['jobs']} != expected:
        raise ValueError('Unexpected jobs')
    deps = Path(prepared['tokenizer_dependencies'])
    for name, sha in prepared['tokenizer_package_files'].items():
        if audit.hash_file(deps/name) != sha:
            raise ValueError('Changed tokenizer dependency')
    sys.path.insert(0,str(deps))
    counter = ReferenceCounter(prepared['tokenizer'])
    bound = ObservationBound(counter.chat)
    refs = {}
    for t in (200,234):
        name = f'h{t}c-p1-delay20/tools/attempt-021.completed.json'
        if audit.hash_file(witness/name) != witnesses[name]:
            raise ValueError('Witness changed')
        response = json.loads((witness/name).read_text('utf-8'))['response']
        valid_result(dict(response, **audit.fingerprints(response['rows'])))
        refs[t] = audit.fingerprints(response['rows'])['bag_sha256']
    report = json.loads((witness/'report.json').read_text('utf-8'))
    if (report['database_sha256_before'] != prepared['database_sha256'] or
            report['database_sha256_after'] != prepared['database_sha256']):
        raise ValueError('Different witness database')
    rows = []
    for job in plan['jobs']:
        hid = job['history_id']; cache = caches[hid]
        packet = json.loads(job['envelope']['messages'][1]['content'])
        if (job['cached_result_sha256'] != audit.digest(cache) or
                cache['sql_sha256'] != audit.digest(packet['history']['sql']) or
                packet['context']['database_sha256'] != prepared['database_sha256'] or
                packet['context']['cache']['materialized_available'] is not True or
                packet['context']['cache']['read_cost'] != job['read_price']):
            raise ValueError('Broken cache provenance or availability')
        target = int(hid[1:4])
        rows.append(dict(id=job['id'], history=hid,
            **describe(cache,job['read_price'],bound,refs[target] if hid.endswith('a') else None)))
    result = dict(status='OFFLINE_COUNTERFACTUAL_FIRST_READ_NOT_AGENT_BEHAVIOR',
        histories=6, distinct_development_tasks=2, rows=rows,
        input_manifest_sha256=INPUT_SHA, experiment_manifest_sha256=EXPERIMENT_SHA,
        witness_manifest_sha256=WITNESS_SHA,
        complete_first_reads=sum(r['visible_complete'] for r in rows),
        direct_control_bag_matches=sum(r['raw_bag_equals_witness'] is True for r in rows),
        model_calls=0, new_sql_queries=0, new_research_samples=0,
        limits=['First-read reconstruction only; no claim about arbitrary later context.',
                'Local frozen tokenizer reference, not new provider measurement.',
                'Typed bag equality on one snapshot is not general SQL equivalence.',
                'No claim that an agent noticed, trusted, or used the available route.',
                'Declared read units exclude inference, verification and maintenance cost.'])
    # Recheck inputs before sealing the derived record; never modify originals.
    load_archive(root/'inputs',INPUT_SHA)
    output.mkdir(parents=True,exist_ok=False)
    audit.save_new(output/'report.json', result)
    (output/'audit_source.py').write_bytes(Path(__file__).read_bytes())
    audit.save_new(output/'artifact_hashes.json', {
        p.name:audit.hash_file(p) for p in sorted(output.iterdir()) if p.is_file()})
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',required=True)
    parser.add_argument('--witness',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.prepared,args.witness,args.output),indent=2))
