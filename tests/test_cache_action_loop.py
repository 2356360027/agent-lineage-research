"""Scripted software tests, not model data or research-database queries."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from runtime import cache_action_preview as preview
from runtime.cache_action_loop import run, RECOVERY, TERMINAL
from runtime.cache_intent_observation_bound import ObservationBound
from runtime import cache_intent_tools as tools

READ = '{"kind":"read_cache","cache_id":"v000"}'
SQL = '{"kind":"run_sql","sql":"SELECT id FROM fixture"}'
ANSWER = '{"kind":"answer","text":"fixture answer"}'


def pair():
    return preview.make_pair('List IDs', None, ['CREATE TABLE fixture(id INTEGER)'],
        dict(historical_request='List IDs', sql='SELECT id FROM fixture'), 'a'*64)


class FakeSession:
    def __init__(self, envelope):
        self.state = preview.initial_state(envelope)
        self.requests = []

    def describe(self):
        return deepcopy(self.state)

    def call(self, kind, args):
        self.requests.append((kind, deepcopy(args)))
        self.state['attempts_used'] += 1
        unavailable = kind == 'read_cache' and not self.state['materialized_available']
        cost = 0 if unavailable else 1 if kind == 'read_cache' else 10
        self.state['remaining_budget'] -= cost
        response = dict(status='CACHE_UNAVAILABLE' if unavailable else 'EXECUTED',
                        complete=not unavailable, charged_units=cost,
                        remaining_budget=self.state['remaining_budget'], attempt=len(self.requests))
        if not unavailable:
            response.update(columns=['id'], rows=[[['integer','1']]])
        return response


class PreviewTests(unittest.TestCase):
    def source_plan(self):
        return {'jobs':[dict(history_id=h, read_price=1, envelope=pair()[1],
                             cached_result_sha256='b'*64) for h in preview.HISTORIES]}

    def test_plan_includes_every_history_and_only_availability_changes(self):
        source = self.source_plan(); before = deepcopy(source)
        plan = preview.build_plan(source)
        self.assertEqual(source, before)
        self.assertEqual(plan, preview.build_plan(source))
        self.assertEqual(len(plan['jobs']), 12)
        self.assertFalse(plan['authorization'])
        for hid in preview.HISTORIES:
            jobs = sorted((j for j in plan['jobs'] if j['history_id'] == hid), key=lambda j:j['available'])
            self.assertEqual(len(jobs), 2)
            packets = [preview.validate(j['envelope']) for j in jobs]
            packets[0]['context']['cache']['materialized_available'] = True
            self.assertEqual(packets[0], packets[1])
            self.assertNotIn('cached_result_sha256', packets[0])
            self.assertEqual(jobs[0]['envelope']['messages'][0], jobs[1]['envelope']['messages'][0])

    def test_plan_rejects_missing_or_duplicated_history(self):
        source = self.source_plan(); source['jobs'].pop()
        with self.assertRaises(ValueError): preview.build_plan(source)
        source = self.source_plan(); source['jobs'].append(deepcopy(source['jobs'][0]))
        with self.assertRaises(ValueError): preview.build_plan(source)

    def test_pair_only_availability_diff_and_no_contract_suffix(self):
        a, b = pair()
        x, y = preview.validate(a), preview.validate(b)
        x['context']['cache']['materialized_available'] = True
        self.assertEqual(x, y)
        self.assertEqual(a['messages'][0], b['messages'][0])
        p = preview.payload(a['messages'])
        self.assertEqual(p['messages'], a['messages'])
        for unwanted in ('Stage 1:', '20-attempt', '200 exploration', 'Contract field definitions'):
            self.assertNotIn(unwanted, a['messages'][0]['content'])
        self.assertEqual(x['context']['max_attempts'], 3)

    def test_common_edit_and_boolean_alias_rejected(self):
        for value in (1, 'true'):
            a = pair()[1]
            packet = preview.validate(a)
            packet['context']['cache']['materialized_available'] = value
            a['messages'][1]['content'] = json.dumps(packet)
            with self.assertRaises(ValueError): preview.validate(a)
        a = pair()[0]; a['messages'][0]['content'] += ' altered'
        with self.assertRaises(ValueError): preview.validate(a)


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.envelope = pair()[1]
        self.session = FakeSession(self.envelope)
        self.records, self.requests = {}, []
        # Character count is a conservative scripted stand-in, not model tokens.
        self.bound = ObservationBound(lambda messages: 10+sum(len(m['content']) for m in messages))

    def save(self, name, value):
        if name in self.records: raise FileExistsError(name)
        self.records[name] = deepcopy(value)

    def run_script(self, outputs):
        source = iter(outputs)
        def generate(request):
            self.requests.append(deepcopy(request))
            value = next(source)
            if isinstance(value, BaseException): raise value
            return value if type(value) is dict else dict(content=value, finish_reason='stop')
        return run(self.envelope, self.session, generate, self.save, self.bound)

    def test_read_then_answer_has_no_intervening_contract(self):
        result = self.run_script([READ, ANSWER])
        self.assertEqual(result['status'], 'ANSWERED')
        self.assertEqual(result['generation_calls'], 2)
        self.assertFalse(result['contract_elicited'])
        self.assertEqual(self.records['t001.pending']['generation_id'], 'g001')
        self.assertEqual(self.requests[0]['stage'], 'exploration')
        self.assertEqual(self.requests[1]['messages'][2]['content'], READ)

    def test_unavailable_read_consumes_attempt_and_recovery_remains(self):
        self.envelope = pair()[0]; self.session = FakeSession(self.envelope)
        result = self.run_script([READ, READ, SQL, ANSWER])
        self.assertEqual(result['tool_attempts'], 3)
        self.assertEqual(self.records['t001.response']['observation']['status'], 'CACHE_UNAVAILABLE')
        self.assertNotIn('rows', self.records['t001.response']['observation'])
        self.assertEqual(self.requests[2]['messages'][-1]['content'], RECOVERY)
        self.assertEqual(self.requests[3]['messages'][-1]['content'], TERMINAL)
        self.assertEqual(self.session.requests[-1][0], 'run_sql')

    def test_prohibited_recovery_read_never_dispatched(self):
        result = self.run_script([READ, READ, READ])
        self.assertEqual(result['status'], 'RECOVERY_ACTION_VIOLATION')
        self.assertEqual(len(self.session.requests), 2)

    def test_terminal_tool_never_dispatched(self):
        result = self.run_script([SQL, SQL, SQL, SQL])
        self.assertEqual(result['status'], 'TOOL_HORIZON_VIOLATION')
        self.assertEqual(len(self.session.requests), 3)

    def test_invalid_response_preserved_and_not_retried(self):
        result = self.run_script([READ+' extra'])
        self.assertEqual(result['status'], 'INVALID_ACTION')
        self.assertEqual(self.records['g001.response']['response']['content'], READ+' extra')
        self.assertEqual(self.session.requests, [])

    def test_length_finish_not_parsed_or_executed(self):
        result = self.run_script([dict(content=READ, finish_reason='length')])
        self.assertEqual(result['status'], 'INVALID_ACTION_RESPONSE')
        self.assertEqual(self.session.requests, [])

    def test_transport_interrupt_retained_no_retry_or_summary(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_script([READ, KeyboardInterrupt()])
        self.assertIn('g002.pending', self.records)
        self.assertIn('g002.failure', self.records)
        self.assertNotIn('summary', self.records)
        self.assertEqual(len(self.requests), 2)

    def test_failed_pending_save_prevents_dispatch(self):
        original = self.save
        def fail(name, value):
            if name == 't001.pending': raise OSError('scripted failure')
            original(name, value)
        self.save = fail
        with self.assertRaises(OSError): self.run_script([READ])
        self.assertEqual(self.session.requests, [])

    def test_environment_failure_stops_without_next_generation(self):
        original = self.session.call
        def invalid(kind, args):
            return original(kind,args) | dict(status='ENVIRONMENT_INVALID', complete=False)
        self.session.call = invalid
        with self.assertRaises(ValueError): self.run_script([READ, ANSWER])
        self.assertIn('t001.response', self.records)
        self.assertNotIn('g002.pending', self.records)

    def test_oversize_result_retained_privately_but_not_partially_exposed(self):
        original = self.session.call
        def large(kind,args):
            return original(kind,args) | dict(rows=[[['text','x'*2000]]])
        self.session.call = large
        self.run_script([READ, ANSWER])
        self.assertEqual(len(self.records['t001.response']['observation']['rows'][0][0][1]), 2000)
        shown = json.loads(self.requests[1]['messages'][-1]['content'])['tool_observation']
        self.assertEqual(shown['status'], 'OBSERVATION_TOO_LARGE')
        self.assertNotIn('rows', shown)

    def test_mismatched_session_stops_before_model_or_journal(self):
        self.session.state['initial_budget'] = 210
        with self.assertRaises(ValueError): self.run_script([READ])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.records, {})


class RealToolProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root/'fixture.sqlite'
        conn = sqlite3.connect(self.db)
        try:
            conn.execute('CREATE TABLE fixture(id INTEGER)')
            conn.execute('INSERT INTO fixture VALUES (1)')
            conn.commit()
        finally:
            conn.close()
        self.sha = tools.audit.hash_file(self.db)
        self.sql = 'SELECT id FROM fixture'
        rows = [[['integer','1']]]
        self.cache = dict(status='EXECUTED', complete=True, columns=['id'], rows=rows,
                          sql_sha256=tools.audit.digest(self.sql), **tools.audit.fingerprints(rows))

    def session(self, available):
        return tools.ToolSession(database_path=self.db, database_sha256=self.sha,
            history_sql=self.sql, cached_result=self.cache, input_receipt={'scripted_fixture':True},
            output=self.root/'tools', materialized_available=available, cache_read_cost=1,
            budget_profile=tools.SHORT_RECOVERY_PROFILE)

    def test_two_failed_sqls_still_leave_reserved_sql_and_no_fourth_call(self):
        session = self.session(True)
        with patch.object(tools.audit, 'run_child', return_value=dict(status='EXECUTION_ERROR',complete=False)) as backend:
            for index in range(3):
                self.assertEqual(session.call('run_sql', {'sql':self.sql})['remaining_budget'], 20-10*index)
            with self.assertRaises(RuntimeError): session.call('run_sql', {'sql':self.sql})
            self.assertEqual(backend.call_count, 3)
        self.assertEqual(session.close()['spent_declared_units'], 30)

    def test_unavailable_reads_do_not_call_backend_and_third_sql_is_real(self):
        session = self.session(False)
        with patch.object(tools.audit, 'run_child', wraps=tools.audit.run_child) as backend:
            for _ in range(2):
                r = session.call('read_cache', {'cache_id':'v000'})
                self.assertEqual(r['status'], 'CACHE_UNAVAILABLE')
                self.assertEqual(r['remaining_budget'], 30)
                self.assertNotIn('rows', r)
            backend.assert_not_called()
            final = session.call('run_sql', {'sql':self.sql})
            self.assertEqual(final['rows'], self.cache['rows'])
            self.assertEqual(backend.call_count, 1)
        self.assertEqual(tools.audit.hash_file(self.db), self.sha)
        self.assertEqual(session.close()['status'], 'TOOL_SESSION_CLOSED')

    def test_reserved_cache_rejected_even_if_called_outside_loop(self):
        session = self.session(True)
        for _ in range(2): session.call('read_cache', {'cache_id':'v000'})
        result = session.call('read_cache', {'cache_id':'v000'})
        self.assertEqual(result['status'], 'RECOVERY_SQL_ONLY')
        self.assertEqual(result['charged_units'], 0)
        self.assertNotIn('rows', result)
        self.assertEqual(session.close()['attempts'], 3)

    def test_real_initial_state_matches_preview(self):
        session = self.session(True)
        envelope = preview.make_pair('List IDs', None, ['CREATE TABLE fixture(id INTEGER)'],
            dict(historical_request='List IDs', sql=self.sql), self.sha)[1]
        preview.assert_initial_session(envelope, session.describe())
        session.close()


if __name__ == '__main__':
    unittest.main()
