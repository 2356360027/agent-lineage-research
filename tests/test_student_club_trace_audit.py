"""Cross-record replay of synthetic batch artifacts; auditor performs no SQL."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from runtime.student_club_trace_audit import replay
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime.student_club_native_batch import CEILING,MAX_CALLS
from runtime.bird_readonly_audit import digest
from tests import test_student_club_native_batch as fixture


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        f=fixture.Tests('test_all_twelve_full_horizons_shared_costs_and_seals')
        cls.f=f
        f.setUp();f.inputs();cls.summary=f.execute()
        cls.saved={}
        for j in f.plan['design']['jobs']:
            cls.saved[j['id']]={}
            for part in ('loop','api','tools'):
                root=f.root/'out'/j['id']/part
                cls.saved[j['id']][part]={p.stem:json.loads(p.read_text('utf-8')) for p in root.glob('*.json') if p.name!='artifact_hashes.json'}

    @classmethod
    def tearDownClass(cls):cls.f.doCleanups()

    def run_one(self,job,records,guard):
        f=self.f
        return replay(f.c,job,f.cache,loop_records=records['loop'],api_records=records['api'],
            tool_records=records['tools'],input_receipt=dict(plan_sha256=digest(f.plan),job_id=job['id'],backend='SCRIPTED_NOT_MODEL'),
            native_counter=f.counter,plain_counter=f.plain,guard=guard,mode='SCRIPTED_NOT_MODEL')

    def guard(self):return CostGuard(ceiling_micro_cny=CEILING,max_calls=MAX_CALLS,input_rate=2,output_rate=8)

    def test_full_replay_preserves_shared_cost_and_never_executes_sql(self):
        guard=self.guard();calls=tools=0
        with patch('runtime.student_club_readonly.run_child',side_effect=AssertionError('No SQL during audit')):
            for job in self.f.plan['design']['jobs']:
                result=self.run_one(job,deepcopy(self.saved[job['id']]),guard)
                self.assertEqual(result['loop_status'],'ANSWERED')
                calls+=result['calls'];tools+=result['tool_attempts']
        self.assertEqual((calls,tools),(56,36))
        self.assertEqual(guard.snapshot(),self.summary['cost_guard'])

    def test_backend_result_tampering_and_charge_changes_refused(self):
        job=self.f.plan['design']['jobs'][0]
        for mode in ('rows','cost','exposure'):
            r=deepcopy(self.saved[job['id']])
            if mode=='rows':r['tools']['attempt-002.completed']['private']['backend_result']['rows']=[[['integer','999']]]
            elif mode=='cost':r['tools']['attempt-002.pending']['charge']=0
            else:r['loop']['t002.exposure']['observation']['rows']=[[['integer','999']]]
            with self.assertRaises(ValueError):self.run_one(job,r,self.guard())

    def test_api_provider_counter_and_extra_entries_refused(self):
        job=self.f.plan['design']['jobs'][0]
        for mode in ('raw','counter','extra'):
            r=deepcopy(self.saved[job['id']])
            if mode=='raw':r['api']['api-g001.provider']['raw_response_text']='{}'
            elif mode=='counter':r['api']['api-g001.context']['reference_input_tokens']=101
            else:r['api']['extra']={'silently_added':True}
            with self.assertRaises((ValueError,KeyError)):self.run_one(job,r,self.guard())

    def test_model_definition_cannot_be_operator_replaced(self):
        jobs=self.f.plan['design']['jobs'];guard=self.guard()
        for job in jobs:
            records=deepcopy(self.saved[job['id']])
            if job['protocol']=='ordinary':self.run_one(job,records,guard);continue
            event=next(v for k,v in records['loop'].items() if k.startswith('e') and v['kind']=='definition_recorded')
            event['value']['text']='CORRECTED BY OPERATOR'
            with self.assertRaises(ValueError):self.run_one(job,records,guard)
            break


if __name__=='__main__':unittest.main()
