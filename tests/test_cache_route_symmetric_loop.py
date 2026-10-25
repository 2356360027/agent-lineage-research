"""Scripted workflow tests; predictions below are NOT model observations."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from runtime.cache_route_symmetric_design import build
from runtime.cache_route_symmetric_loop import run, initial_state, ObservationBound
from runtime.cache_intent_observation_bound import ObservationBound as LegacyBound
from runtime.cache_route_symmetric_tools import ToolSession
from runtime import bird_readonly_audit as audit

READ = '{"kind":"read_cache","cache_id":"v000"}'
SQL = '{"kind":"run_sql","sql":"SELECT 1"}'
ANSWER = '{"kind":"answer","text":"scripted answer"}'


class Session:
    def __init__(self, envelope, common):
        self.state = initial_state(envelope, common)
        self.requests = []

    def describe(self): return deepcopy(self.state)

    def call(self, name, arguments):
        self.requests.append((name, deepcopy(arguments)))
        self.state['attempts_used'] += 1
        available = name == 'run_sql' or self.state['materialized_available']
        cost = (10 if name == 'run_sql' else self.state['read_cost']) if available else 0
        self.state['remaining_budget'] -= cost
        response = dict(status='EXECUTED' if available else 'CACHE_UNAVAILABLE', complete=available,
                        charged_units=cost, remaining_budget=self.state['remaining_budget'],
                        attempt=self.state['attempts_used'])
        if available: response.update(columns=['id'], rows=[[['integer','1']]])
        return response


class Tests(unittest.TestCase):
    def setUp(self):
        self.common = dict(shared_contract='Shared', history_contract='History', current_contract='Current',
                           schema=['CREATE TABLE t(id INTEGER)'], history_sql='SELECT id FROM t',
                           database_sha256='a'*64)
        self.cell = build(self.common)[3]
        self.session = Session(self.cell, self.common)
        self.records, self.requests = {}, []
        self.script = iter([READ, READ, READ, ANSWER])
        self.now = 0
        # Explicit mock counter; not a tokenizer/capacity certificate.
        self.bound = ObservationBound(lambda messages: 50)

    def save(self, name, value):
        if name in self.records: raise FileExistsError(name)
        self.records[name] = deepcopy(value)

    def generate(self, request):
        self.requests.append(deepcopy(request))
        item = next(self.script)
        if isinstance(item, BaseException): raise item
        return dict(content=item, finish_reason='stop')

    def execute(self):
        return run(self.cell, self.common, self.session, self.generate, self.save, self.bound,
                   deadline=600, clock=lambda:self.now)

    def test_all_cells_third_slot_allows_either_tool_and_fourth_is_terminal(self):
        for cell in build(self.common):
            for third in (READ, SQL):
                self.setUp(); self.cell=cell; self.session=Session(cell,self.common)
                self.script=iter([READ, READ, third, ANSWER])
                result=self.execute()
                self.assertEqual((result['generation_calls'],result['tool_attempts']), (4,3))
                self.assertEqual(result['status'],'ANSWERED')
                self.assertFalse(result['contract_elicited'])
                self.assertEqual(self.requests[2]['stage'], 'symmetric_tool_opportunity')
                self.assertNotIn('Reserved recovery', json.dumps(self.requests))
                self.assertEqual(self.requests[3]['stage'], 'terminal')
                self.assertEqual(self.requests[0]['messages'],cell['messages'])

    def test_fourth_tool_request_denied_without_dispatch(self):
        for final in (READ, SQL):
            self.setUp(); self.script=iter([SQL, SQL, SQL, final])
            self.assertEqual(self.execute()['status'],'TOOL_HORIZON_VIOLATION')
            self.assertEqual(len(self.session.requests),3)

    def test_envelope_tampering_and_session_mismatch_before_generation(self):
        for key in ('envelope','state'):
            self.setUp()
            if key=='envelope': self.cell['messages'][0]['content']+=' changed'
            else: self.session.state['read_cost']=1
            with self.assertRaises(ValueError): self.execute()
            self.assertEqual(self.requests,[])
            self.assertIn('failure',self.records)

    def test_rejects_legacy_bound_before_generation(self):
        self.bound=LegacyBound(lambda m: 50)
        with self.assertRaises(TypeError): self.execute()
        self.assertEqual(self.requests,[])

    def test_early_answer_abstain_malformed_and_truncated_response(self):
        for raw, status in [(ANSWER,'ANSWERED'), ('{"kind":"abstain","text":"x"}','ABSTAINED'),
                            ('not json','INVALID_ACTION')]:
            self.setUp(); self.script=iter([raw])
            self.assertEqual(self.execute()['status'],status)
            self.assertEqual(self.session.requests,[])
        self.setUp(); self.generate=lambda r: dict(content=ANSWER,finish_reason='length')
        self.assertEqual(self.execute()['status'],'INVALID_ACTION_RESPONSE')

    def test_timeout_and_interrupt_retained_no_retry(self):
        for exc in (TimeoutError(), KeyboardInterrupt()):
            self.setUp(); self.script=iter([exc])
            with self.assertRaises(type(exc)): self.execute()
            self.assertEqual(len(self.requests),1)
            self.assertIn('g001.pending',self.records)
            self.assertIn('g001.failure',self.records)
            self.assertEqual(self.session.requests,[])
            self.assertTrue(self.records['failure']['terminal'])

    def test_late_response_preserved_without_tool_dispatch(self):
        original=self.generate
        def late(request):
            value=original(request); self.now=601; return value
        self.generate=late
        with self.assertRaises(TimeoutError): self.execute()
        self.assertIn('g001.response',self.records)
        self.assertNotIn('g001.decision',self.records)
        self.assertEqual(self.session.requests,[])

    def test_state_drift_and_unavailable_row_leak_stop_before_exposure(self):
        for mode in ('price','charge','rows'):
            self.setUp()
            if mode=='rows': self.cell=build(self.common)[0]; self.session=Session(self.cell,self.common)
            original=self.session.call
            def drift(name,args):
                value=original(name,args)
                if mode=='price': self.session.state['read_cost']=1
                elif mode=='charge': value['charged_units']=0
                else: value['rows']=['should not be exposed']
                return value
            self.session.call=drift
            with self.assertRaises(ValueError): self.execute()
            self.assertEqual(len(self.requests),1)
            self.assertIn('t001.response',self.records)
            self.assertNotIn('t001.exposure',self.records)

    def test_pending_write_failure_prevents_tool(self):
        original=self.save
        def failed(name,value):
            if name=='t001.pending': raise OSError('fixture')
            original(name,value)
        self.save=failed
        with self.assertRaises(OSError): self.execute()
        self.assertEqual(self.session.requests,[])

    def test_reentry_same_journal_does_not_generate_again(self):
        self.script=iter([ANSWER]); self.execute()
        with self.assertRaises(FileExistsError): self.execute()
        self.assertEqual(len(self.requests),1)


class BoundTests(unittest.TestCase):
    def observation(self):
        return dict(status='EXECUTED',complete=True,charged_units=20,remaining_budget=40,
                    attempt=1,columns=['text'],rows=[[['text','untrusted text']]])

    def test_boundary_and_full_omission_not_partial_rows(self):
        for size in (1024,1025):
            raw=self.observation()
            bound=ObservationBound(lambda m: size if 'rows' in m[1]['content'] else 50)
            result=bound.apply(raw)
            self.assertEqual(result['bound'],1024)
            self.assertFalse(result['rows_partially_exposed'])
            if size==1024: self.assertEqual(result['observation'],raw)
            else:
                self.assertEqual(result['observation']['status'],'OBSERVATION_TOO_LARGE')
                self.assertNotIn('rows',result['observation'])
                self.assertEqual(result['observation']['charged_units'],20)
            self.assertIn('rows',raw)

    def test_invalid_environment_not_masked_and_bad_counter_fails(self):
        raw=self.observation() | {'status':'ENVIRONMENT_INVALID','complete':False}
        bound=ObservationBound(lambda m: 2000 if 'rows' in m[1]['content'] else 50)
        self.assertEqual(bound.apply(raw)['observation']['status'],'ENVIRONMENT_INVALID')
        for count in (True,0,1025):
            with self.assertRaises(ValueError): ObservationBound(lambda m:count).apply(raw)


class IntegrationTests(unittest.TestCase):
    def test_real_tool_and_loop_across_four_cells(self):
        # No research database/model: disposable source, deterministic script.
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); db=root/'fixture.sqlite'
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('CREATE TABLE t(id INTEGER PRIMARY KEY)')
                conn.execute('INSERT INTO t VALUES (1)'); conn.commit()
            fingerprint=audit.hash_file(db)
            for index in range(4):
                harness=Tests(); harness.setUp()
                harness.common['database_sha256']=fingerprint
                harness.cell=build(harness.common)[index]
                state=initial_state(harness.cell,harness.common)
                rows=[[['integer','1']]]
                cache=dict(status='EXECUTED',complete=True,columns=['id'],rows=rows,
                           sql_sha256=audit.digest(harness.common['history_sql']),**audit.fingerprints(rows))
                session=ToolSession(database_path=db,database_sha256=fingerprint,
                    history_sql=harness.common['history_sql'],cached_result=cache,
                    input_receipt={'software_fixture_only':True},output=root/str(index),
                    materialized_available=state['materialized_available'],cache_read_cost=state['read_cost'])
                harness.session=session
                # A real SQL call followed by a third-opportunity cache read.
                harness.script=iter([READ,'{"kind":"run_sql","sql":"SELECT id FROM t"}',READ,ANSWER])
                try:
                    result=harness.execute()
                    self.assertEqual(result['status'],'ANSWERED')
                    self.assertEqual(harness.records['t002.response']['observation']['rows'],rows)
                    self.assertEqual(harness.records['t003.response']['observation']['status'],
                                     'EXECUTED' if state['materialized_available'] else 'CACHE_UNAVAILABLE')
                finally:
                    summary=session.close()
                self.assertEqual(summary['status'],'TOOL_SESSION_CLOSED')
                self.assertEqual(summary['spent_declared_units'],10+(
                    2*state['read_cost'] if state['materialized_available'] else 0))
                self.assertEqual(audit.hash_file(db),fingerprint)


if __name__=='__main__': unittest.main()
