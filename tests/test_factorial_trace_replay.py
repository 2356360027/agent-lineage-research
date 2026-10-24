"""Record/replay consistency on scripted fixtures; no model evidence."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from runtime.factorial_trace_replay import replay,finished_batch
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime import cache_intent_tools as tools
from tests import test_factorial_batch as fixtures


class Tests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.Tests()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        with patch.object(tools.audit,'run_child',return_value=deepcopy(self.fixture.cache)):
            self.summary=self.fixture.execute()

    def records(self,job):
        root=self.fixture.root/'result'/job['id']
        def load(folder):
            return {p.stem:json.loads(p.read_text('utf-8')) for p in (root/folder).glob('*.json')
                    if p.name!='artifact_hashes.json'}
        return load('trace'),load('tools')

    def guard(self): return CostGuard(ceiling_micro_cny=1000000,max_calls=16,input_rate=2,output_rate=8)

    def whole(self,path=None):
        f=self.fixture
        return finished_batch(path or f.root/'result',f.plan,f.cache,f.counter,
            expected_plan_sha=tools.audit.digest(f.plan),mode='SCRIPTED_NOT_MODEL',
            ceiling_micro_cny=1000000,input_rate=2,output_rate=8)

    def test_whole_batch_seals_and_all_cells_replayed(self):
        with patch.object(tools.audit,'run_child',side_effect=AssertionError('Replay must not query')):
            result=self.whole()
        self.assertEqual(len(result['rows']),4)
        self.assertEqual(result['cost_guard'],self.summary['cost_guard'])
        self.assertEqual(result['new_SQL_executions'],0)

    def test_unsealed_or_modified_batch_refused(self):
        seal=self.fixture.root/'result/artifact_hashes.json'
        raw=seal.read_bytes(); seal.unlink()
        with self.assertRaisesRegex(ValueError,'Unsealed'): self.whole()
        seal.write_bytes(raw)
        (self.fixture.root/'result/summary.json').write_text('{}')
        with self.assertRaises(ValueError): self.whole()

    def test_sealed_stopped_batch_not_filtered_to_successes(self):
        self.fixture.responses=[TimeoutError()]
        target=self.fixture.root/'stopped-result'
        self.fixture.execute(output=target)
        with self.assertRaisesRegex(ValueError,'Not finished'): self.whole(target)

    def test_all_four_reconstruct_shared_costs_without_sql(self):
        guard=self.guard()
        with patch.object(tools.audit,'run_child',side_effect=AssertionError('Replay must not query')):
            for job,row in zip(self.fixture.plan['jobs'],self.summary['rows']):
                trace,records=self.records(job)
                result=replay(job,trace,records,self.fixture.cache,self.fixture.counter,guard,mode='SCRIPTED_NOT_MODEL')
                self.assertEqual(result['final_status'],row['status'])
                self.assertEqual(result['spent_declared_units'],row['tools']['spent_declared_units'])
                self.assertEqual(result['first_valid_action'],'read_cache')
                self.assertEqual(len(result['actions']),4)
                self.assertFalse(result['C_drift_assessed'])
        self.assertEqual(guard.snapshot(),self.summary['cost_guard'])

    def test_altered_answer_provider_or_price_rejected(self):
        for tamper in ('answer','provider','price'):
            job=deepcopy(self.fixture.plan['jobs'][0]); trace,records=self.records(job)
            if tamper=='answer': trace['g004.decision']['action']['text']='invented'
            elif tamper=='provider': trace['api-g004.provider']['raw']['choices'][0]['message']['content']='different'
            else: records['started']['configuration']['read_cost']=99
            with self.assertRaises(ValueError):
                replay(job,trace,records,self.fixture.cache,self.fixture.counter,self.guard(),mode='SCRIPTED_NOT_MODEL')

    def test_extra_and_missing_records_cannot_be_ignored(self):
        job=self.fixture.plan['jobs'][0]
        trace,records=self.records(job); trace['unexplained']={}
        with self.assertRaises(ValueError): replay(job,trace,records,self.fixture.cache,self.fixture.counter,self.guard(),mode='SCRIPTED_NOT_MODEL')
        trace,records=self.records(job); del trace['g002.pending']
        with self.assertRaises(ValueError): replay(job,trace,records,self.fixture.cache,self.fixture.counter,self.guard(),mode='SCRIPTED_NOT_MODEL')

    def test_different_history_sql_binding_rejected(self):
        job=self.fixture.plan['jobs'][0]; trace,records=self.records(job)
        cache=deepcopy(self.fixture.cache); cache['sql_sha256']='0'*64
        with self.assertRaises(ValueError): replay(job,trace,records,cache,self.fixture.counter,self.guard(),mode='SCRIPTED_NOT_MODEL')


if __name__=='__main__': unittest.main()
