"""Scripted provider and disposable SQLite tests, not scientific samples."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from runtime import cache_route_symmetric_batch as batch
from runtime.deepseek_trajectory_adapter import MODEL
from tests.test_cache_action_adapter import Counter
from tests.test_cache_route_symmetric_loop import READ,ANSWER
from scripts.run_cache_action import verify_archive


class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(temp.cleanup);self.root=Path(temp.name);self.db=self.root/'fixture.sqlite'
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute('CREATE TABLE t(id INTEGER PRIMARY KEY)')
            connection.execute('INSERT INTO t VALUES (1)');connection.commit()
        common=dict(shared_contract='Shared',history_contract='History',current_contract='Current',
                    schema=['CREATE TABLE t(id INTEGER PRIMARY KEY)'],history_sql='SELECT id FROM t',
                    database_sha256=batch.audit.hash_file(self.db))
        rows=[[['integer','1']]]
        self.cache=dict(status='EXECUTED',complete=True,columns=['id'],rows=rows,
                        sql_sha256=batch.audit.digest(common['history_sql']),**batch.audit.fingerprints(rows))
        self.plan=batch.build_plan(common,self.cache,'a'*64)
        self.counter=Counter();self.calls=[];self.now=0;self.serial=0
        self.scripts={j['id']:[READ,'{"kind":"run_sql","sql":"SELECT id FROM t"}',READ,ANSWER]
                      for j in self.plan['jobs']}

    def factory(self,job_id,trace):
        script=iter(self.scripts[job_id])
        def transport(payload,timeout):
            self.calls.append(job_id);item=next(script)
            if isinstance(item,BaseException):raise item
            return dict(model=MODEL,usage=dict(prompt_tokens=self.counter.chat(payload['messages']),
                        completion_tokens=self.counter.text(item)),
                        choices=[dict(message=dict(content=item),finish_reason='stop')])
        return transport

    def execute(self,**overrides):
        self.serial+=1;self.output=self.root/f'run-{self.serial}'
        arguments=dict(expected_plan_sha=batch.audit.digest(self.plan),database=self.db,
            output=self.output,counter=self.counter,transport_factory=self.factory,deadline=600,
            mode='SCRIPTED_NOT_MODEL',ceiling_micro_cny=1000000,input_rate=2,output_rate=8,clock=lambda:self.now)
        return batch.run(self.plan,self.cache,**(arguments|overrides))

    def test_four_cell_real_SQL_and_sealed_journals(self):
        summary=self.execute()
        self.assertEqual(summary['status'],'SCRIPTED_NOT_MODEL_FINISHED')
        self.assertEqual(summary['dispatch_attempts'],16)
        self.assertEqual(summary['model_calls'],0)
        self.assertEqual(len(summary['rows']),4)
        self.assertTrue(all(row['status']=='ANSWERED' for row in summary['rows']))
        self.assertEqual([r['tools']['spent_declared_units'] for r in summary['rows']],[10,10,50,12])
        verify_archive(self.output,batch.audit.hash_file(self.output/'artifact_hashes.json'))
        self.assertEqual(batch.audit.hash_file(self.db),self.plan['common']['database_sha256'])

    def test_invalid_action_remains_outcome_and_other_cells_continue(self):
        self.scripts[self.plan['jobs'][0]['id']]=['not json']
        summary=self.execute()
        self.assertEqual(summary['status'],'SCRIPTED_NOT_MODEL_FINISHED')
        self.assertEqual(summary['rows'][0]['status'],'INVALID_ACTION')
        self.assertEqual(summary['dispatch_attempts'],13)

    def test_timeout_and_interrupt_leave_later_cells_not_started(self):
        for exc in (TimeoutError(),KeyboardInterrupt()):
            self.scripts[self.plan['jobs'][0]['id']]=[exc]
            summary=self.execute()
            self.assertEqual(summary['status'],'SCRIPTED_NOT_MODEL_STOPPED')
            self.assertEqual(summary['dispatch_attempts'],1)
            self.assertEqual([r['status'] for r in summary['rows'][1:]],['NOT_STARTED']*3)
            self.assertGreater(summary['cost_guard']['uncertain_reserved_micro_cny'],0)
            verify_archive(self.output,batch.audit.hash_file(self.output/'artifact_hashes.json'))

    def test_existing_directory_refused_no_dispatch_or_overwrite(self):
        self.scripts={j['id']:[ANSWER] for j in self.plan['jobs']};self.execute()
        existing=self.output;before=(existing/'summary.json').read_bytes();calls=len(self.calls)
        with self.assertRaises(FileExistsError):self.execute(output=existing)
        self.assertEqual(len(self.calls),calls)
        self.assertEqual((existing/'summary.json').read_bytes(),before)

    def test_plan_cache_or_global_budget_rejection_before_output(self):
        original=deepcopy(self.plan)
        self.plan['jobs'].reverse()
        with self.assertRaises(ValueError):self.execute()
        self.assertFalse(self.output.exists())
        self.plan=original
        with self.assertRaises(ValueError):self.execute(ceiling_micro_cny=1)
        self.assertFalse(self.output.exists())
        self.cache['rows'][0][0][1]='99'
        with self.assertRaises(ValueError):self.execute()
        self.assertFalse(self.output.exists());self.assertEqual(self.calls,[])

    def test_finalization_failure_stops_batch_and_preserves_failure_marker(self):
        original=batch.ToolSession.close
        def failure(session):
            original(session)
            raise OSError('fixture finalization failure')
        with patch.object(batch.ToolSession,'close',failure):summary=self.execute()
        self.assertEqual(summary['rows'][0]['status'],'FINALIZATION_FAILED')
        self.assertEqual([r['status'] for r in summary['rows'][1:]],['NOT_STARTED']*3)
        self.assertEqual(summary['status'],'SCRIPTED_NOT_MODEL_STOPPED')


if __name__=='__main__':unittest.main()
