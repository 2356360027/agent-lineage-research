"""Scripted accounting fixtures only; no provider, SQL or real charges."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from runtime.deepseek_trajectory_adapter import CostGuard
from runtime.native_trajectory_adapter import NativeTrajectoryAdapter
from runtime.native_action_history import NativeHistory
from runtime.native_accounting_replay import replay_completed, IncompleteTrajectory
from tests.test_native_action_history import native_initial
from tests.test_native_trajectory_adapter import provider


def guard():
    return CostGuard(ceiling_micro_cny=100000,max_calls=32,input_rate=2,output_rate=8)


def fixture(shared=None, script=None):
    source_guard=shared or guard()
    api,loop={},{}
    counter=Mock(); counter.inspect.return_value={'reference_input_tokens':100}
    history=NativeHistory(native_initial(),observation_byte_limit=1024)
    scripts=iter(script or [provider(),provider('answer','done','c2')])
    count=0
    def transport(payload,timeout):
        nonlocal count
        count+=1; raw=next(scripts)
        api[f'transport-{count:03d}']=dict(transport_status='RESPONSE_RECEIVED',response_text=raw)
        return raw
    def save(name,value):
        if name in api: raise FileExistsError(name)
        api[name]=deepcopy(value)
    adapter=NativeTrajectoryAdapter(counter=counter,transport=transport,save=save,
                                    guard=source_guard,deadline=600,clock=lambda:0)
    for i in range(1,5):
        request=history.begin(); loop[f'g{i:03d}.pending']=deepcopy(request)
        raw=adapter(request); loop[f'g{i:03d}.response']={'raw_response_text':raw}
        try: decision=history.accept(raw)
        except ValueError: break
        if decision['stop']: break
        history.observe(decision['call_id'],'[]')
    loop['summary']={'generation_calls':i}
    return api,loop,counter


class Tests(unittest.TestCase):
    def setUp(self): self.api,self.loop,self.counter=fixture()

    def replay(self,api=None,loop=None,g=None):
        return replay_completed(self.api if api is None else api,
            loop_records=self.loop if loop is None else loop,counter=self.counter,guard=g or guard())

    def test_complete_accounting_and_totals(self):
        result=self.replay()
        self.assertEqual(result['reported_prompt_tokens'],200)
        self.assertEqual(result['reported_completion_tokens'],40)
        self.assertEqual(result['cumulative_guard']['accounted_micro_cny'],720)
        self.assertFalse(result['provider_invoice_verified'])

    def test_shared_guard_across_two_trajectories(self):
        source=guard(); first=fixture(source); second=fixture(source)
        check=guard()
        self.replay(first[0],first[1],check)
        result=self.replay(second[0],second[1],check)
        self.assertEqual(result['cumulative_guard']['attempts'],4)
        self.assertEqual(result['cumulative_guard']['accounted_micro_cny'],1440)
        with self.assertRaises(ValueError): self.replay(second[0],second[1])

    def test_tampered_raw_transport_or_loop_detected(self):
        for kind in ('transport','provider','loop'):
            api,loop=deepcopy(self.api),deepcopy(self.loop)
            if kind=='transport': api['transport-001']['response_text']='{}'
            elif kind=='provider': api['api-g001.provider']['raw_response_text']='{}'
            else: loop['g001.response']['raw_response_text']='{}'
            with self.subTest(kind=kind),self.assertRaises(ValueError): self.replay(api,loop)

    def test_changed_count_cost_admission_or_payload(self):
        for kind in ('context','cost','admission','pending'):
            api=deepcopy(self.api)
            if kind=='context': api['api-g001.context']['reference_input_tokens']=101
            elif kind=='cost': api['api-g001.cost']['accounted_micro_cny']=0
            elif kind=='admission': api['api-g001.admission']['semantic_correctness_assessed']=True
            else: api['api-g001.pending']['payload']['messages'][0]['content']+='altered'
            with self.subTest(kind=kind),self.assertRaises(ValueError): self.replay(api)

    def test_invalid_ticket_and_timeout(self):
        for field,value in (('ticket',True),('ticket',2),('timeout_seconds',float('nan')),
                            ('timeout_seconds',91),('automatic_retry',True)):
            api=deepcopy(self.api); api['api-g001.pending'][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError): self.replay(api)

    def test_partial_extra_or_failed_records_not_certified(self):
        for kind in ('missing','extra','failed','loop_failure'):
            api,loop=deepcopy(self.api),deepcopy(self.loop)
            if kind=='missing': api.pop('api-g002.cost')
            elif kind=='extra': api['transport-003']={}
            elif kind=='failed': api['adapter_failure']={}
            else: loop['failure']={}
            with self.subTest(kind=kind),self.assertRaises(IncompleteTrajectory): self.replay(api,loop)

    def test_observed_bad_format_still_accounted(self):
        import json
        body=json.loads(provider());body['choices'][0]['message']['tool_calls'][0]['function']['arguments']='{}'
        api,loop,counter=fixture(script=[json.dumps(body)])
        result=self.replay(api,loop)
        self.assertEqual(result['calls'],1)
        self.assertEqual(result['cumulative_guard']['accounted_micro_cny'],360)
        self.assertFalse(api['api-g001.admission']['format_admitted'])


if __name__=='__main__':unittest.main()
