"""Scripted batch tests on disposable SQLite; no network/model requests."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from runtime import cache_action_factorial_batch as batch
from runtime.cache_action_factorial_preview import make_cells
from runtime.cache_intent_journal import verify
from runtime.deepseek_trajectory_adapter import MODEL
from tests.test_cache_action_batch import Counter, READ, SQL, ANSWER


class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(temp.cleanup); self.root=Path(temp.name); self.db=self.root/'fixture.sqlite'
        conn=sqlite3.connect(self.db)
        try:
            conn.execute('CREATE TABLE fixture(id INTEGER)')
            conn.execute('INSERT INTO fixture VALUES (1)'); conn.commit()
        finally: conn.close()
        rows=[[['integer','1']]]; sql='SELECT id FROM fixture'
        self.cache=dict(status='EXECUTED',complete=True,columns=['id'],rows=rows,
                        sql_sha256=batch.audit.digest(sql),**batch.audit.fingerprints(rows))
        self.cells=make_cells('List IDs',None,['CREATE TABLE fixture(id INTEGER)'],
            dict(historical_request='List IDs',evidence='IDs are records.',sql=sql),batch.audit.hash_file(self.db))
        self.plan=batch.build_plan(self.cells,self.cache,'a'*64)
        self.counter=Counter(); self.dispatched=[]; self.created=[]; self.now=0
        self.responses=[READ,READ,SQL,ANSWER]

    def factory(self,job,trace):
        self.created.append(job); script=iter(self.responses)
        def transport(payload,timeout):
            self.dispatched.append(job); action=next(script)
            if isinstance(action,BaseException): raise action
            return dict(model=MODEL,usage=dict(prompt_tokens=self.counter.chat(payload['messages']),
                                              completion_tokens=self.counter.text(action)),
                        choices=[dict(message=dict(content=action),finish_reason='stop')])
        return transport

    def execute(self,**kw):
        settings=dict(expected_plan_sha=batch.audit.digest(self.plan),database=self.db,
            output=self.root/'result',counter=self.counter,transport_factory=self.factory,
            deadline=600,mode='SCRIPTED_NOT_MODEL',clock=lambda:self.now,
            ceiling_micro_cny=1000000,input_rate=2,output_rate=8)
        settings.update(kw)
        return batch.run(self.plan,self.cache,**settings)

    def test_full_four_cells_real_sql_and_all_journal_hashes(self):
        summary=self.execute()
        self.assertEqual(summary['status'],'SCRIPTED_NOT_MODEL_FINISHED')
        self.assertEqual(summary['dispatch_attempts'],16)
        self.assertEqual(summary['model_calls'],0)
        self.assertEqual(summary['independent_task_pairs'],1)
        self.assertEqual(summary['answers_graded'],0)
        self.assertEqual(len(self.created),4)
        self.assertEqual(sum(r['tools']['attempts'] for r in summary['rows']),12)
        self.assertEqual(sum(r['tools']['sql_calls'] for r in summary['rows']),4)
        for row in summary['rows']:
            expected=10+(2*int(row['id'][-1]) if row['id'].startswith('available1') else 0)
            self.assertEqual(row['tools']['spent_declared_units'],expected)
        root=self.root/'result'
        hashes=json.loads((root/'artifact_hashes.json').read_text('utf-8'))
        self.assertEqual(set(hashes),{p.relative_to(root).as_posix() for p in root.rglob('*')
                                     if p.is_file() and p != root/'artifact_hashes.json'})
        for name,sha in hashes.items(): self.assertEqual(batch.audit.hash_file(root/name),sha)
        verify(root/'batch')
        for job in self.plan['jobs']:
            verify(root/job['id']/'trace'); verify(root/job['id']/'tools')

    def test_timeout_retains_uncertain_reservation_and_three_unstarted(self):
        self.responses=[TimeoutError()]
        summary=self.execute()
        self.assertEqual(summary['stop_reason'],'TimeoutError')
        self.assertEqual(len(self.dispatched),1)
        self.assertEqual(sum(r['status']=='NOT_STARTED' for r in summary['rows']),3)
        self.assertGreater(summary['cost_guard']['uncertain_reserved_micro_cny'],0)
        verify(self.root/'result'/self.created[0]/'trace')

    def test_interrupt_keeps_failure_and_no_replacement(self):
        self.responses=[KeyboardInterrupt()]
        summary=self.execute()
        self.assertEqual(summary['rows'][0]['status'],'INTERRUPTED')
        self.assertEqual(len(self.created),1)
        self.assertTrue((self.root/'result'/self.created[0]/'trace/g001.failure.json').is_file())

    def test_invalid_model_output_remains_outcome_not_infrastructure_retry(self):
        self.responses=['bad JSON']
        summary=self.execute()
        self.assertEqual(summary['dispatch_attempts'],4)
        self.assertTrue(all(r['status']=='INVALID_ACTION' for r in summary['rows']))
        self.assertEqual(sum(r['tools']['attempts'] for r in summary['rows']),0)

    def test_reused_directory_refused(self):
        root=self.root/'result'; root.mkdir(); keep=root/'keep'; keep.write_bytes(b'preserve')
        with self.assertRaises(FileExistsError): self.execute()
        self.assertEqual(keep.read_bytes(),b'preserve'); self.assertEqual(self.dispatched,[])

    def test_changed_cache_order_duplicate_or_unsafe_id_preflight_rejected(self):
        original=deepcopy(self.plan)
        variants=[]
        x=deepcopy(original); x['jobs'].reverse(); variants.append(x)
        x=deepcopy(original); x['jobs'][0]=deepcopy(x['jobs'][1]); variants.append(x)
        x=deepcopy(original); x['jobs'][0]['id']='../outside'; variants.append(x)
        x=deepcopy(original); x['max_model_calls']=17; variants.append(x)
        for variant in variants:
            self.plan=variant
            with self.assertRaises(ValueError): self.execute()
        self.plan=original; self.cache['sql_sha256']='0'*64
        with self.assertRaises(ValueError): self.execute()
        self.assertFalse((self.root/'result').exists()); self.assertEqual(self.dispatched,[])

    def test_cap_deadline_rate_and_mode_preflight_rejected(self):
        for changes in (dict(ceiling_micro_cny=1),dict(deadline=float('nan')),
                        dict(input_rate=True),dict(mode='unspecified'),dict(expected_plan_sha='0'*64)):
            with self.assertRaises(ValueError): self.execute(**changes)
        self.assertFalse((self.root/'result').exists()); self.assertEqual(self.dispatched,[])

    def test_late_answer_accounted_and_stops_remaining(self):
        original=self.factory
        def factory(job,trace):
            transport=original(job,trace)
            def late(payload,timeout):
                result=transport(payload,timeout); self.now=601; return result
            return late
        summary=self.execute(transport_factory=factory)
        self.assertEqual(summary['stop_reason'],'TimeoutError')
        self.assertEqual(len(self.created),1)
        self.assertGreater(summary['cost_guard']['accounted_micro_cny'],0)
        self.assertEqual(summary['rows'][0]['tools']['attempts'],0)

    def test_finalization_failure_stops_other_cells(self):
        self.responses=[ANSWER]; original=batch.ToolSession.close
        def fail(session):
            original(session); raise OSError('Scripted finalization failure')
        with patch.object(batch.ToolSession,'close',fail): summary=self.execute()
        self.assertEqual(summary['stop_reason'],'FINALIZATION_FAILED')
        self.assertEqual(len(self.created),1)

    def test_missing_database_stops_without_transport(self):
        summary=self.execute(database=self.root/'absent.sqlite')
        self.assertEqual(summary['rows'][0]['status'],'FAILED')
        self.assertEqual(self.dispatched,[])
        self.assertEqual(sum(r['status']=='NOT_STARTED' for r in summary['rows']),3)


if __name__=='__main__': unittest.main()
