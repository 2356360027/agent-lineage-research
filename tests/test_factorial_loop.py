"""Offline scripted software checks, never model or human research observations."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import unittest

from runtime.cache_action_factorial_preview import make_cells
from runtime.cache_action_factorial_capacity import FactorialCapacity
from runtime.cache_action_factorial_loop import run, initial_state
from runtime.cache_action_adapter import ActionAdapter
from runtime.deepseek_trajectory_adapter import CostGuard, MODEL
from tests.test_cache_action_adapter import Counter
from tests.test_cache_action_loop import READ, SQL, ANSWER
from runtime import cache_intent_tools as tools


def cells(database_sha='a'*64):
    return make_cells('List IDs', None, ['CREATE TABLE fixture(id INTEGER)'],
                      dict(historical_request='List IDs', evidence='IDs are records.',
                           sql='SELECT id FROM fixture'), database_sha)


class Session:
    def __init__(self, cell):
        self.state = initial_state(cell)
        self.requests = []

    def describe(self): return deepcopy(self.state)

    def call(self, name, arguments):
        self.requests.append((name, deepcopy(arguments)))
        self.state['attempts_used'] += 1
        available = name != 'read_cache' or self.state['materialized_available']
        cost = (self.state['read_cost'] if name == 'read_cache' else 10) if available else 0
        self.state['remaining_budget'] -= cost
        return dict(status='EXECUTED' if available else 'CACHE_UNAVAILABLE', complete=available,
                    charged_units=cost, remaining_budget=self.state['remaining_budget'],
                    attempt=self.state['attempts_used'])


class Tests(unittest.TestCase):
    def setUp(self):
        self.cell = cells()[3]
        self.records, self.dispatched = {}, []
        self.now = 0
        self.counter = Counter()
        self.session = Session(self.cell)
        self.script = iter([READ, ANSWER])
        # Test bookkeeping, not a current price quote or paid authorization.
        self.guard = CostGuard(ceiling_micro_cny=1000000,max_calls=4,input_rate=2,output_rate=8)

    def save(self, name, value):
        if name in self.records: raise FileExistsError(name)
        self.records[name] = deepcopy(value)

    def transport(self, payload, timeout):
        self.dispatched.append(deepcopy(payload))
        item = next(self.script)
        if isinstance(item, BaseException): raise item
        return dict(model=MODEL, usage=dict(prompt_tokens=self.counter.chat(payload['messages']),
                                            completion_tokens=self.counter.text(item)),
                    choices=[dict(message=dict(content=item),finish_reason='stop')])

    def execute(self, transport=None):
        cap = FactorialCapacity(self.cell, self.counter)
        self.adapter = ActionAdapter(counter=self.counter, capacity=cap, transport=transport or self.transport,
                                     save=self.save, guard=self.guard, deadline=600, clock=lambda:self.now)
        return run(self.cell,self.session,self.adapter,self.save,cap.observation_bound(),
                   deadline=600,clock=lambda:self.now)

    def test_all_four_cells_full_horizon_preserve_factors_and_budget(self):
        for cell in cells():
            with self.subTest(state=initial_state(cell)):
                self.setUp(); self.cell = cell; self.session = Session(cell)
                self.script = iter([READ, READ, SQL, ANSWER])
                summary = self.execute()
                self.assertEqual(summary['status'], 'ANSWERED')
                self.assertEqual((summary['generation_calls'],summary['tool_attempts']), (4,3))
                s = self.session.describe()
                charged = 2*s['read_cost'] if s['materialized_available'] else 0
                self.assertEqual(s['remaining_budget'],20-charged)
                self.assertEqual(self.dispatched[0]['messages'],cell['messages'])
                self.assertFalse(summary['contract_elicited'])

    def test_initial_price_availability_or_budget_mismatch_no_dispatch(self):
        for key, value in [('read_cost',1),('materialized_available',False),('initial_budget',100)]:
            self.setUp(); self.session.state[key] = value
            with self.assertRaises(ValueError): self.execute()
            self.assertEqual(self.dispatched,[])
            self.assertIn('failure',self.records)

    def test_invalid_output_and_recovery_tool_not_repaired(self):
        for script, status, attempts in [(['not json'],'INVALID_ACTION',0),
                                         ([READ,READ,READ],'RECOVERY_ACTION_VIOLATION',2),
                                         ([SQL,SQL,SQL,READ],'TOOL_HORIZON_VIOLATION',3)]:
            self.setUp(); self.script=iter(script)
            self.assertEqual(self.execute()['status'],status)
            self.assertEqual(len(self.session.requests),attempts)
            self.assertEqual(len(self.dispatched),len(script))

    def test_network_timeout_preserves_unknown_charge_no_retry(self):
        self.script=iter([TimeoutError()])
        with self.assertRaises(TimeoutError): self.execute()
        self.assertEqual(len(self.dispatched),1)
        self.assertIn('api-g001.failure',self.records)
        self.assertGreater(self.guard.snapshot()['uncertain_reserved_micro_cny'],0)
        self.assertTrue(self.adapter.terminal)
        self.assertTrue(self.records['failure']['terminal'])

    def test_late_result_is_retained_and_not_used(self):
        def late(payload,timeout):
            raw=self.transport(payload,timeout); self.now=601; return raw
        with self.assertRaises(TimeoutError): self.execute(late)
        self.assertIn('api-g001.provider',self.records)
        self.assertGreater(self.guard.accounted,0)
        self.assertEqual(self.session.requests,[])

    def test_expired_before_dispatch(self):
        self.now=600
        with self.assertRaises(ValueError): self.execute()
        self.assertEqual(self.dispatched,[])

    def test_tool_state_or_charge_drift_stops_before_second_generation(self):
        for mutate in ('price','charge','counter','environment'):
            self.setUp(); original=self.session.call
            def changed(name,args):
                observation=original(name,args)
                if mutate=='price': self.session.state['read_cost']=1
                elif mutate=='charge': observation['charged_units']=1
                elif mutate=='counter': observation['attempt']=2
                else: observation['status']='ENVIRONMENT_INVALID'
                return observation
            self.session.call=changed
            with self.assertRaises(ValueError): self.execute()
            self.assertIn('t001.response',self.records)
            self.assertNotIn('t001.exposure',self.records)
            self.assertEqual(len(self.dispatched),1)

    def test_failed_pending_journal_blocks_tool(self):
        original=self.save
        def fail(name,value):
            if name=='t001.pending': raise OSError('Scripted disk failure')
            original(name,value)
        self.save=fail
        with self.assertRaises(OSError): self.execute()
        self.assertEqual(self.session.requests,[])
        self.assertIn('failure',self.records)


class RealToolTests(unittest.TestCase):
    def test_four_cells_with_real_readonly_fixture_and_declared_charges(self):
        # Disposable synthetic SQLite only; never the research snapshot.
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs') as temp:
            root=Path(temp); db=root/'fixture.sqlite'
            connection=sqlite3.connect(db)
            try:
                connection.execute('CREATE TABLE fixture(id INTEGER)')
                connection.execute('INSERT INTO fixture VALUES (1)')
                connection.commit()
            finally: connection.close()
            sha=tools.audit.hash_file(db); sql='SELECT id FROM fixture'
            rows=[[['integer','1']]]
            cache=dict(status='EXECUTED',complete=True,columns=['id'],rows=rows,
                       sql_sha256=tools.audit.digest(sql),**tools.audit.fingerprints(rows))
            for index,cell in enumerate(cells(sha)):
                harness=Tests(); harness.setUp(); harness.cell=cell
                state=initial_state(cell)
                session=tools.ToolSession(database_path=db,database_sha256=sha,history_sql=sql,
                    cached_result=cache,input_receipt={'scripted_fixture':True},output=root/str(index),
                    materialized_available=state['materialized_available'],cache_read_cost=state['read_cost'],
                    budget_profile=tools.SHORT_RECOVERY_PROFILE)
                harness.session=session; harness.script=iter([READ,READ,SQL,ANSWER])
                try:
                    result=harness.execute()
                    self.assertEqual(result['status'],'ANSWERED')
                    self.assertEqual(harness.records['t003.response']['observation']['rows'],rows)
                    for turn in ('t001','t002'):
                        obs=harness.records[turn+'.response']['observation']
                        self.assertEqual(obs['charged_units'],state['read_cost'] if state['materialized_available'] else 0)
                        if not state['materialized_available']: self.assertNotIn('rows',obs)
                finally:
                    closed=session.close()
                self.assertEqual(closed['status'],'TOOL_SESSION_CLOSED')
                self.assertEqual(closed['spent_declared_units'],10+(2*state['read_cost'] if state['materialized_available'] else 0))
                self.assertEqual(tools.audit.hash_file(db),sha)


if __name__=='__main__': unittest.main()
