"""Disposable SQLite/scripted-provider integration, never research samples."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from runtime.bird_readonly_audit import hash_file, digest, fingerprints
from runtime.cache_route_symmetric_design import build
from runtime.cache_route_symmetric_loop import ObservationBound
from runtime.cache_route_symmetric_tools import ToolSession
from runtime.native_symmetric_loop import run
from runtime.native_trajectory_adapter import NativeTrajectoryAdapter
from runtime.deepseek_trajectory_adapter import CostGuard
from tests.test_native_action_history import raw


class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name); self.db=self.root/'fixture.sqlite'
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('CREATE TABLE t(id INTEGER PRIMARY KEY)')
            c.execute('INSERT INTO t VALUES (1)'); c.commit()
        self.sha=hash_file(self.db)
        self.common=dict(shared_contract='same snapshot',history_contract='list IDs',current_contract='count IDs',
            schema=['CREATE TABLE t(id INTEGER PRIMARY KEY)'],history_sql='SELECT id FROM t',database_sha256=self.sha)
        self.rows=[[['integer','1']]]
        self.cache=dict(status='EXECUTED',complete=True,columns=['id'],rows=self.rows,
            sql_sha256=digest(self.common['history_sql']),**fingerprints(self.rows))
        self.bound=ObservationBound(lambda ms:len(ms[1]['content'])+20)  # fixture char count, not tokens
        self.records={}; self.requests=[]; self.now=0

    def session(self, cell):
        a,c=((False,1),(False,20),(True,1),(True,20))[cell]
        return ToolSession(database_path=self.db,database_sha256=self.sha,history_sql=self.common['history_sql'],
            cached_result=self.cache,input_receipt={'software_fixture':True},output=self.root/f'session-{cell}',
            materialized_available=a,cache_read_cost=c)

    def save(self,name,value):
        if name in self.records: raise FileExistsError(name)
        self.records[name]=deepcopy(value)

    def execute(self, session, cell, script, generate=None):
        iterator=iter(script)
        def scripted(req):
            self.requests.append(deepcopy(req))
            item=next(iterator)
            if isinstance(item,BaseException): raise item
            return item
        return run(build(self.common)[cell],self.common,session,generate or scripted,
                   self.save,self.bound,deadline=600,clock=lambda:self.now)

    def close_check(self,s):
        result=s.close()
        self.assertEqual(result['status'],'TOOL_SESSION_CLOSED')
        manifest=json.loads((s.output/'artifact_hashes.json').read_text())
        for name,sha in manifest.items(): self.assertEqual(hash_file(s.output/name),sha)
        self.assertEqual(hash_file(self.db),self.sha)
        return result

    def test_all_four_conditions_real_readonly_sql_and_third_cache(self):
        for cell in range(4):
            self.records={}; self.requests=[]; s=self.session(cell)
            try:
                result=self.execute(s,cell,[raw(call_id='c1'),raw('run_sql','SELECT id FROM t','c2'),
                                            raw(call_id='c3'),raw('answer','1','c4')])
                self.assertEqual(result['status'],'ANSWERED')
                self.assertEqual(self.records['t002.response']['observation']['rows'],self.rows)
                self.assertEqual(self.records['t003.response']['observation']['status'],
                    'EXECUTED' if cell>=2 else 'CACHE_UNAVAILABLE')
                self.assertEqual(self.requests[2]['stage'],'symmetric_tool_opportunity')
                self.assertEqual(self.requests[1]['payload']['messages'][-1]['role'],'tool')
                self.assertEqual(self.requests[1]['payload']['messages'][-1]['tool_call_id'],'c1')
            finally: closed=self.close_check(s)
            self.assertEqual(closed['spent_declared_units'],10+(0 if cell<2 else 2*(1 if cell==2 else 20)))

    def test_delete_rejected_and_original_database_unchanged(self):
        s=self.session(2)
        try:
            self.execute(s,2,[raw('run_sql','DELETE FROM t','c1'),raw('abstain','denied','c2')])
            self.assertEqual(self.records['t001.response']['observation']['status'],'DENIED')
        finally: self.close_check(s)

    def test_malformed_and_duplicate_calls_never_execute(self):
        s=self.session(2)
        try:
            b=json.loads(raw()); b['choices'][0]['message']['tool_calls']*=2
            r=self.execute(s,2,[json.dumps(b)])
            self.assertEqual(r['status'],'INVALID_ACTION_RESPONSE')
            self.assertEqual(s.attempts,0)
        finally: self.close_check(s)

    def test_pending_write_failure_prevents_tool(self):
        s=self.session(2); original=self.save
        def failed(name,value):
            if name=='t001.pending': raise OSError('fixture')
            original(name,value)
        self.save=failed
        try:
            with self.assertRaises(OSError): self.execute(s,2,[raw()])
            self.assertEqual(s.attempts,0)
        finally: self.close_check(s)

    def test_late_response_saved_but_never_executed(self):
        s=self.session(2)
        def late(req): self.now=601; return raw()
        try:
            with self.assertRaises(TimeoutError): self.execute(s,2,[],late)
            self.assertIn('g001.response',self.records)
            self.assertEqual(s.attempts,0)
        finally: self.close_check(s)

    def test_initial_session_mismatch_before_generation(self):
        s=self.session(0)
        try:
            with self.assertRaises(ValueError): self.execute(s,2,[raw()])
            self.assertEqual(self.requests,[])
        finally: self.close_check(s)

    def test_transport_failure_no_retry(self):
        s=self.session(2)
        try:
            with self.assertRaises(TimeoutError): self.execute(s,2,[TimeoutError('fixture')])
            self.assertEqual(len(self.requests),1)
            self.assertIn('g001.failure',self.records)
            self.assertEqual(s.attempts,0)
        finally: self.close_check(s)

    def test_fourth_tool_proposal_never_executed(self):
        s=self.session(2)
        try:
            r=self.execute(s,2,[raw(call_id=f'c{i}') for i in range(4)])
            self.assertEqual(r['status'],'TOOL_HORIZON_VIOLATION')
            self.assertEqual(s.attempts,3)
        finally: self.close_check(s)

    def test_no_journal_resume(self):
        s=self.session(2)
        try:
            self.execute(s,2,[raw('answer','done')])
            with self.assertRaises(FileExistsError): self.execute(s,2,[raw('answer','again')])
            self.assertEqual(len(self.requests),1)
        finally: self.close_check(s)

    def test_accounting_adapter_and_real_tool_composed(self):
        s=self.session(2)
        guard=CostGuard(ceiling_micro_cny=100000,max_calls=4,input_rate=2,output_rate=8)
        scripted=iter([raw('run_sql','SELECT id FROM t','c1'),raw('answer','1','c2')])
        def transport(p,timeout):
            b=json.loads(next(scripted))
            b.update(model='deepseek-flash',usage={'prompt_tokens':100,'completion_tokens':20})
            return json.dumps(b)
        counter=Mock(); counter.inspect.return_value={'reference_input_tokens':100}
        adapter=NativeTrajectoryAdapter(counter=counter,transport=transport,save=self.save,
            guard=guard,deadline=600,clock=lambda:self.now)
        try:
            result=self.execute(s,2,[],adapter)
            self.assertEqual(result['status'],'ANSWERED')
            self.assertEqual(self.records['t001.response']['observation']['rows'],self.rows)
            self.assertEqual(guard.accounted,720)  # Synthetic accounting fixture, not money spent.
            self.assertEqual(guard.uncertain,{})
            self.assertEqual(adapter.count,2)
        finally: self.close_check(s)

    def test_usage_mismatch_stops_before_proposed_sql(self):
        s=self.session(2)
        guard=CostGuard(ceiling_micro_cny=100000,max_calls=4,input_rate=2,output_rate=8)
        body=json.loads(raw('run_sql','SELECT id FROM t','c1'))
        body.update(model='deepseek-flash',usage={'prompt_tokens':101,'completion_tokens':20})
        transport=Mock(return_value=json.dumps(body))
        counter=Mock(); counter.inspect.return_value={'reference_input_tokens':100}
        adapter=NativeTrajectoryAdapter(counter=counter,transport=transport,save=self.save,
            guard=guard,deadline=600,clock=lambda:self.now)
        try:
            with self.assertRaises(ValueError): self.execute(s,2,[],adapter)
            self.assertEqual(s.attempts,0)
            self.assertEqual(transport.call_count,1)
            self.assertTrue(guard.terminal)
            self.assertIn('api-g001.provider',self.records)
        finally: self.close_check(s)


if __name__=='__main__': unittest.main()
