"""Scripted records only; offline replay must never query a database/provider."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from runtime.cache_route_symmetric_replay import replay
from runtime.cache_route_symmetric_readout import summarize
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime import cache_intent_tools as tools
from tests import test_cache_route_symmetric_batch as fixtures
from tests.test_cache_route_symmetric_readout import reference


class Tests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.Tests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        # Disposable fixture SQL for record creation only, no research DB.
        self.summary=self.fixture.execute()

    def records(self,job):
        root=self.fixture.output/job['id']
        def load(folder):
            return {p.stem:json.loads(p.read_text('utf-8')) for p in (root/folder).glob('*.json')
                    if p.name!='artifact_hashes.json'}
        return load('trace'),load('tools')

    def guard(self):return CostGuard(ceiling_micro_cny=1000000,max_calls=16,input_rate=2,output_rate=8)

    def one(self,job,trace,records,guard):
        f=self.fixture
        return replay(job,f.plan['common'],trace,records,f.cache,f.counter,guard,mode='SCRIPTED_NOT_MODEL')

    def test_all_four_shared_costs_and_no_queries(self):
        guard=self.guard();results=[]
        with patch.object(tools.audit,'run_child',side_effect=AssertionError('No replay SQL')):
            for job,row in zip(self.fixture.plan['jobs'],self.summary['rows']):
                trace,records=self.records(job)
                result=self.one(job,trace,records,guard);results.append(result)
                self.assertEqual(result['final_status'],row['status'])
                self.assertEqual(result['spent_declared_units'],row['tools']['spent_declared_units'])
                self.assertEqual(result['first_decision']['action']['kind'],'read_cache')
                self.assertEqual(result['tool_attempts'],3)
        self.assertEqual(guard.snapshot(),self.summary['cost_guard'])
        # Scoring integration on artificial answers, no empirical claim.
        readout=summarize(results,reference(1))
        self.assertEqual(sum(readout['counts'].values()),4)
        self.assertFalse(readout['C_drift_assessed'])

    def test_cross_record_answer_provider_and_configuration_tampering(self):
        for field in ('answer','provider','price','exposure','cost'):
            job=self.fixture.plan['jobs'][0];trace,records=self.records(job)
            if field=='answer':trace['g004.decision']['action']['text']='forged'
            elif field=='provider':trace['api-g004.provider']['raw']['choices'][0]['message']['content']='forged'
            elif field=='price':records['started']['configuration']['read_cost']=20
            elif field=='exposure':trace['t001.exposure']['observation']['remaining_budget']=0
            else:trace['api-g001.cost']['accounted_micro_cny']+=1
            with self.assertRaises(ValueError):self.one(job,trace,records,self.guard())

    def test_missing_and_extra_records_rejected(self):
        job=self.fixture.plan['jobs'][0]
        trace,records=self.records(job);trace['unexplained']={}
        with self.assertRaises(ValueError):self.one(job,trace,records,self.guard())
        trace,records=self.records(job);del trace['g002.pending']
        with self.assertRaises(ValueError):self.one(job,trace,records,self.guard())

    def test_cache_binding_rejected(self):
        job=self.fixture.plan['jobs'][0];trace,records=self.records(job)
        self.fixture.cache['sql_sha256']='0'*64
        with self.assertRaises(ValueError):self.one(job,trace,records,self.guard())

    def test_invalid_first_output_is_preserved(self):
        job=self.fixture.plan['jobs'][0]
        self.fixture.scripts[job['id']]=['not json'];self.fixture.execute()
        trace,records=self.records(job)
        result=self.one(job,trace,records,self.guard())
        self.assertEqual(result['final_status'],'INVALID_ACTION')
        self.assertIsNone(result['final_answer'])
        self.assertIsNone(result['first_decision']['action'])
        self.assertEqual(result['dispatch_attempts'],1)

    def test_saved_tool_rows_not_trusted_without_reconstruction(self):
        job=next(j for j in self.fixture.plan['jobs'] if j['id']=='cell-2')
        trace,records=self.records(job)
        records['attempt-001.completed']['response']['rows']=[[['integer','999']]]
        # Costs before this cell need not match: its tool reconstruction must
        # independently reject the fabricated response as well.
        from runtime.cache_route_symmetric_replay import SavedSymmetricTools
        session=SavedSymmetricTools(job,self.fixture.plan['common'],records,self.fixture.cache)
        with self.assertRaises(ValueError):session.call('read_cache',{'cache_id':'v000'})


if __name__=='__main__':unittest.main()
