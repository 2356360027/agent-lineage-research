"""Batch tests use scripted responses and a temporary SQLite fixture only."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from runtime import cache_action_batch as batch
from runtime import cache_action_preview as preview
from runtime import bird_readonly_audit as audit
from runtime.cache_intent_journal import verify
from runtime.deepseek_trajectory_adapter import MODEL

READ='{"kind":"read_cache","cache_id":"v000"}'
SQL='{"kind":"run_sql","sql":"SELECT id FROM fixture"}'
ANSWER='{"kind":"answer","text":"1"}'


class Counter:
    def text(self,s): return max(1,(len(s)+3)//4)
    def chat(self,messages): return 10+sum(self.text(m['content']) for m in messages)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name)
        self.db=self.root/'fixture.sqlite'; conn=sqlite3.connect(self.db)
        try:
            conn.execute('CREATE TABLE fixture(id INTEGER)')
            conn.execute('INSERT INTO fixture VALUES(1)'); conn.commit()
        finally: conn.close()
        self.sha=audit.hash_file(self.db); sql='SELECT id FROM fixture'
        rows=[[['integer','1']]]
        self.cache=dict(status='EXECUTED',complete=True,columns=['id'],rows=rows,
            sql_sha256=audit.digest(sql),**audit.fingerprints(rows))
        old={'jobs':[dict(history_id=h,read_price=1,cached_result_sha256=audit.digest(self.cache),
            envelope=preview.make_pair('List IDs',None,['CREATE TABLE fixture(id INTEGER)'],
            dict(historical_request='List IDs',sql=sql),self.sha)[1]) for h in preview.HISTORIES]}
        self.plan=preview.build_plan(old)
        self.caches={h:deepcopy(self.cache) for h in preview.HISTORIES}
        self.counter=Counter(); self.dispatched=[]; self.created=[]; self.now=0
        self.responses=None

    def factory(self,job_id,log):
        self.created.append(job_id)
        script=iter(self.responses or [READ if job_id.endswith('1') else SQL,ANSWER])
        def transport(p,timeout):
            self.dispatched.append(job_id)
            action=next(script)
            if isinstance(action,BaseException): raise action
            return dict(model=MODEL,usage=dict(prompt_tokens=self.counter.chat(p['messages']),
                completion_tokens=self.counter.text(action)),
                choices=[dict(message=dict(content=action),finish_reason='stop')])
        return transport

    def execute(self,**kw):
        settings=dict(expected_plan_sha=audit.digest(self.plan),database=self.db,
            output=self.root/'batch-output',counter=self.counter,transport_factory=self.factory,
            deadline=600,mode='SCRIPTED_NOT_MODEL',clock=lambda:self.now)
        settings.update(kw)
        with patch.object(batch.audit,'run_child',return_value=deepcopy(self.cache)):
            return batch.run(self.plan,self.caches,**settings)

    def test_complete_all_pairs_and_seal_every_artifact(self):
        summary=self.execute()
        self.assertEqual(summary['status'],'SCRIPTED_NOT_MODEL_FINISHED')
        self.assertEqual(summary['dispatch_attempts'],24)
        self.assertEqual(summary['answers_graded'],0)
        self.assertEqual(summary['model_calls'],0)
        self.assertEqual(len(self.created),12)
        root=self.root/'batch-output'
        hashes=json.loads((root/'artifact_hashes.json').read_text('utf-8'))
        for name,sha in hashes.items(): self.assertEqual(audit.hash_file(root/name),sha)
        verify(root/'batch')
        for job in self.plan['jobs']:
            verify(root/job['id']/'trace'); verify(root/job['id']/'tools')
        self.assertEqual(sum(r['tools']['sql_calls'] for r in summary['rows']),6)

    def test_transport_failure_stops_remaining_rows_not_retried(self):
        self.responses=[TimeoutError()]
        summary=self.execute()
        self.assertEqual(summary['status'],'SCRIPTED_NOT_MODEL_STOPPED')
        self.assertEqual(summary['stop_reason'],'TimeoutError')
        self.assertEqual(len(self.dispatched),1)
        self.assertEqual(sum(r['status']=='NOT_STARTED' for r in summary['rows']),11)
        self.assertGreater(summary['cost_guard']['uncertain_reserved_micro_cny'],0)

    def test_interrupt_preserved_and_finalized_without_resuming(self):
        self.responses=[KeyboardInterrupt()]
        summary=self.execute()
        self.assertEqual(summary['rows'][0]['status'],'INTERRUPTED')
        self.assertEqual(summary['stop_reason'],'KeyboardInterrupt')
        root=self.root/'batch-output'/self.plan['jobs'][0]['id']/'trace'
        self.assertTrue((root/'g001.failure.json').is_file())
        verify(root)
        self.assertEqual(len(self.created),1)

    def test_invalid_actions_keep_all_slots_without_replacement(self):
        self.responses=[READ+' extra']
        summary=self.execute()
        self.assertEqual(summary['dispatch_attempts'],12)
        self.assertTrue(all(r['status']=='INVALID_ACTION' for r in summary['rows']))
        self.assertEqual(sum(r['tools']['attempts'] for r in summary['rows']),0)

    def test_existing_output_refused_before_dispatch(self):
        root=self.root/'batch-output'; root.mkdir(); (root/'keep').write_text('keep')
        with self.assertRaises(FileExistsError): self.execute()
        self.assertEqual((root/'keep').read_text(),'keep')
        self.assertEqual(self.dispatched,[])

    def test_changed_cache_and_unsafe_job_rejected_before_output(self):
        self.caches['h200a']['sql_sha256']='0'*64
        with self.assertRaises(ValueError): self.execute()
        self.assertFalse((self.root/'batch-output').exists())
        self.caches['h200a']=deepcopy(self.cache)
        self.plan['jobs'][0]['id']='../unsafe'
        with self.assertRaises(ValueError): self.execute()
        self.assertEqual(self.dispatched,[])

    def test_deadline_between_jobs_stops_before_next_model(self):
        original=self.factory
        def factory(job,log):
            transport=original(job,log)
            def expired(p,t):
                result=transport(p,t); self.now=601; return result
            return expired
        summary=self.execute(transport_factory=factory)
        self.assertEqual(summary['stop_reason'],'TimeoutError')
        self.assertEqual(len(self.dispatched),1)
        self.assertEqual(sum(r['status']=='NOT_STARTED' for r in summary['rows']),11)

    def test_session_finalization_failure_stops_batch(self):
        close=batch.ToolSession.close
        def fail(session):
            close(session)
            raise OSError('scripted finalization failure')
        with patch.object(batch.ToolSession,'close',fail): summary=self.execute()
        self.assertEqual(summary['stop_reason'],'FINALIZATION_FAILED')
        self.assertEqual(summary['rows'][0]['status'],'FINALIZATION_FAILED')
        self.assertEqual(len(self.created),1)


if __name__=='__main__': unittest.main()
