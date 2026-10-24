"""Full 23-step SCRIPTED replay with real reference tokenization/readonly SQL.

Not a model run. No API credentials, HTTP calls, private tasks or human answers.
Two declared cache prices are checked without claiming a behavioral effect.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_recovery_preview as preview
from runtime import cache_intent_tools as tools
from runtime.cache_intent_journal import Journal, verify
from runtime.cache_intent_prompts import CONTRACT_FIELDS
from runtime.cache_intent_staged_loop import run_recovery_trajectory
from runtime.cache_intent_observation_bound import ObservationBound
from runtime.deepseek_trajectory_adapter import Adapter, CostGuard
from runtime.deepseek_text_counter import ReferenceCounter


def run(reference, dependencies, output):
    sys.path.insert(0, str(Path(dependencies).resolve()))
    counter = ReferenceCounter(Path(reference) / 'tokenizer.json')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    database = output / 'synthetic.sqlite'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE items(id TEXT PRIMARY KEY)')
        conn.executemany('INSERT INTO items VALUES(?)', [('fixture-a',), ('fixture-b',)])
    conn.close()
    db_hash = audit.hash_file(database)
    sql = 'SELECT id FROM items ORDER BY id'
    cached = audit.run_child(database, sql)
    if cached.get('complete') is not True:
        raise ValueError('Fixture setup failed')
    cached['sql_sha256'] = audit.digest(sql)
    pair = preview.make_pair('List all unique item IDs', None,
        ['CREATE TABLE items(id TEXT PRIMARY KEY)'],
        {'historical_request':'List all item IDs', 'sql':sql}, db_hash)
    rows = []
    with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('No HTTP in replay')):
        for envelope, price in zip(pair, (1, 8)):
            root = output / f'fixture-price-{price}'
            log = Journal(root / 'trace')
            log('purpose', {'transport':'SCRIPTED_NOT_MODEL', 'model_calls':0,
                            'research_tasks':0, 'actual_api_cost_cny':0})
            session = tools.ToolSession(database_path=database, database_sha256=db_hash,
                history_sql=sql, cached_result=cached,
                input_receipt={'synthetic_software_fixture_only':True}, output=root / 'tools',
                materialized_available=True, cache_read_cost=price, budget_profile=tools.RECOVERY_PROFILE)
            responses = iter([json.dumps(dict.fromkeys(CONTRACT_FIELDS, 'fixture'))] +
                [json.dumps({'kind':'read_cache','cache_id':'v000'})]*20 +
                [json.dumps({'kind':'run_sql','sql':sql}),
                 json.dumps({'kind':'answer','text':'fixture-a, fixture-b'})])
            sent = []
            def transport(payload, timeout):
                content = next(responses)
                sent.append(deepcopy(payload))
                return {'model':'deepseek-flash', 'transport_kind':'SCRIPTED_NOT_MODEL',
                    'counts_source':'LOCAL_REFERENCE_NOT_PROVIDER_USAGE',
                    'usage':{'prompt_tokens':counter.chat(payload['messages']),
                             'completion_tokens':counter.text(content)},
                    'choices':[{'message':{'content':content}, 'finish_reason':'stop'}]}
            guard = CostGuard(ceiling_micro_cny=2_000_000, max_calls=23,
                               input_rate=2, output_rate=8)
            adapter = Adapter(counter=counter, transport=transport, save=log, guard=guard)
            try:
                result = run_recovery_trajectory(envelope, session, adapter, log,
                                                 ObservationBound(counter.chat))
            finally:
                tool_summary = session.close()
                log.seal()
            verify(session.output)
            if (result['status'] != 'ANSWERED' or len(sent) != 23
                    or tool_summary['spent_declared_units'] != 20*price+10
                    or tool_summary['sql_calls'] != 1):
                raise AssertionError('Full-horizon software replay failed')
            raw_cache = json.loads((log.directory / 't001.response.json').read_text('utf-8'))
            raw_sql = json.loads((log.directory / 't021.response.json').read_text('utf-8'))
            if raw_cache['observation']['rows'] != raw_sql['observation']['rows']:
                raise AssertionError('Cache/recompute mismatch in synthetic fixture')
            rows.append({'price':price, 'status':result['status'],
                'scripted_generations':len(sent), 'tool_attempts':tool_summary['attempts'],
                'real_readonly_sql_calls':tool_summary['sql_calls'],
                'declared_tool_units':tool_summary['spent_declared_units'],
                'largest_reference_input':max(counter.chat(p['messages']) for p in sent),
                'synthetic_cost_guard_only':guard.snapshot(), 'model_calls':0})
    observation = {'status':'EXECUTED','complete':True,'charged_units':1,
        'remaining_budget':209,'attempt':1,'rows':[['oversize-marker '+str(i)] for i in range(1000)]}
    exposure = ObservationBound(counter.chat).apply(observation)
    if (exposure['observation']['status'] != 'OBSERVATION_TOO_LARGE'
            or 'rows' in exposure['observation'] or exposure['rows_partially_exposed']):
        raise AssertionError('Oversize policy failed')
    if audit.hash_file(database) != db_hash:
        raise AssertionError('Read-only fixture changed')
    result = {'status':'OFFLINE_SCRIPTED_FULL_HORIZON_PASSED', 'rows':rows,
        'model_calls':0, 'research_samples':0, 'actual_api_cost_cny':0,
        'oversize_exposure_test':{'raw_tokens':exposure['raw_framed_tokens'],
            'shown_tokens':exposure['exposed_framed_tokens'],'whole_result_withheld':True},
        'source_sha256':{name:audit.hash_file(Path(__file__).resolve().parents[1]/name)
            for name in ('runtime/deepseek_text_counter.py','runtime/deepseek_trajectory_adapter.py',
                         'runtime/cache_intent_staged_loop.py','runtime/cache_intent_observation_bound.py',
                         'scripts/check_deepseek_trajectory_offline.py')},
        'live_batch_ready':False,
        'remaining':['Remote multi-turn tokenizer parity', 'Full-history capacity certificate',
                     'Live transport with hard deadline and hidden-key handling',
                     'Fixed paid-batch scope and cost approval', 'Independent semantic/program review']}
    audit.save_new(output/'summary.json', result)
    audit.save_new(output/'artifact_hashes.json', {p.relative_to(output).as_posix():audit.hash_file(p)
        for p in sorted(output.rglob('*')) if p.is_file()})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('reference','dependencies','output'): parser.add_argument('--'+name,required=True)
    print(json.dumps(run(**vars(parser.parse_args())),indent=2))
