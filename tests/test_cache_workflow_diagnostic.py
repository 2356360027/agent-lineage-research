from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import cache_workflow_diagnostic as w
from scripts import run_cache_workflow as launcher


def old_plan():
    jobs=[]
    for hid in ('h200a','h234a'):
        data=dict(current_request='fixture request '+hid,evidence='fixture',schema=['fixture'],
                  history={'sql':'SELECT fixture'},context={'cache':{'read_cost':1,'materialized_available':True}})
        jobs.append(dict(history_id=hid,read_price=1,envelope={'messages':[
            {'role':'system','content':'Shared correctness and cost.\nStage 1: fixture contract.'},
            {'role':'user','content':json.dumps(data)}]}))
    return {'jobs':jobs}


class Counter:
    def chat(self,m): return 100
    def text(self,t): return 10


def response(p,*,invalid_contract=False):
    first_contract=len(p['messages'])==2 and 'Stage 1:' in p['messages'][0]['content']
    content=json.dumps(dict.fromkeys(w.CONTRACT_FIELDS,'fixture')) if first_contract else '{"kind":"read_cache","cache_id":"v000"}'
    if first_contract and invalid_contract: content='{}'
    return dict(model='deepseek-flash',usage={'prompt_tokens':100,'completion_tokens':10},
                choices=[{'finish_reason':'stop','message':{'content':content}}])


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)/'trial'
        self.plan=w.build_plan(old_plan())

    def run_fake(self,transport,**kwargs):
        with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No network in tests')):
            return w.run(self.plan,counter=Counter(),transport_factory=lambda ident,log:transport,
                         output=self.out,mode='SCRIPTED_NOT_MODEL',**kwargs)

    def test_pair_matching_no_schema_leak_and_raw_contract(self):
        self.assertEqual(self.plan,w.build_plan(old_plan()))
        for h in ('h200a','h234a'):
            for f in ('contract_first','action_first'):
                pair=[j for j in self.plan['jobs'] if j['history']==h and j['workflow']==f]
                a,b=map(deepcopy,pair)
                for j in (a,b):
                    j.pop('id'); j['available']=False
                    j['packet']['context']['cache']['materialized_available']=False
                self.assertEqual(a,b)
        direct=next(j for j in self.plan['jobs'] if j['workflow']=='action_first')
        self.assertNotIn('Stage 1',w.payload(direct)['messages'][0]['content'])
        staged=next(j for j in self.plan['jobs'] if j['workflow']=='contract_first')
        raw=json.dumps(dict.fromkeys(w.CONTRACT_FIELDS,'unchanged fixture'),indent=3)
        self.assertEqual(w.payload(staged,raw)['messages'][2]['content'],raw)
        changed=deepcopy(self.plan); changed['jobs'][0]['packet']['evidence']='gold fixture'
        with self.assertRaises(ValueError): w.validate_plan(changed,old_plan())

    def test_twelve_calls_eight_slots_and_no_tools(self):
        r=self.run_fake(lambda p,t:response(p))
        self.assertEqual(r['dispatch_attempts'],12)
        self.assertEqual([v['status'] for v in r['rows']],['ACTION_RECORDED']*8)
        self.assertEqual(sum(v['unavailable_cache_requested'] for v in r['rows']),4)
        self.assertEqual(r['model_calls'],0)
        self.assertEqual(r['tool_executions'],0)
        self.assertEqual(r['cost_guard']['accounted_micro_cny'],12*280)
        with self.assertRaises(FileExistsError): self.run_fake(lambda p,t:response(p))

    def test_invalid_contracts_not_replaced(self):
        r=self.run_fake(lambda p,t:response(p,invalid_contract=True))
        self.assertEqual(r['dispatch_attempts'],8)
        self.assertEqual(sum(v['status']=='INVALID_CONTRACT' for v in r['rows']),4)

    def test_transport_failure_keeps_reserve_and_missing_slots(self):
        def fail(p,t): raise RuntimeError('FAKE_TRANSPORT_FAILURE')
        r=self.run_fake(fail)
        self.assertEqual(r['dispatch_attempts'],1)
        self.assertEqual(sum(v['status']=='NOT_STARTED' for v in r['rows']),7)
        self.assertGreater(r['cost_guard']['uncertain_reserved_micro_cny'],0)

    def test_keyboard_interrupt_is_terminal_not_retry(self):
        def interrupt(p,t): raise KeyboardInterrupt()
        r=self.run_fake(interrupt)
        self.assertEqual(r['stop_reason'],'KeyboardInterrupt')
        self.assertEqual(r['dispatch_attempts'],1)

    def test_late_response_preserved_but_stops_batch(self):
        tick=[0]
        def late(p,t):
            tick[0]=601
            return response(p)
        r=self.run_fake(late,clock=lambda:tick[0])
        self.assertEqual(r['stop_reason'],'TimeoutError')
        self.assertEqual(r['dispatch_attempts'],1)
        self.assertEqual(r['cost_guard']['uncertain_reserved_micro_cny'],0)
        self.assertEqual(len(list(self.out.rglob('*.provider.json'))),1)

    def test_count_mismatch_stops_after_saved_provider(self):
        def wrong(p,t):
            r=response(p); r['usage']['prompt_tokens']=101
            return r
        r=self.run_fake(wrong)
        self.assertEqual(r['dispatch_attempts'],1)
        self.assertIn('STOPPED',r['status'])

    def test_no_approval_never_asks_secret(self):
        with patch.object(launcher.getpass,'getpass') as secret:
            with self.assertRaises(ValueError): launcher.execute(self.out,'0'*64,None)
            secret.assert_not_called()


if __name__=='__main__': unittest.main()
