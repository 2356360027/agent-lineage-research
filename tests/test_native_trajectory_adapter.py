"""Injected accounting/provenance failure tests. No paid calls or SQL."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime.native_action_history import NativeHistory
from runtime.native_action_interface import build_candidate
from runtime.native_trajectory_adapter import NativeTrajectoryAdapter
from tests.test_native_action_interface import request
from tests.test_native_action_history import raw, native_initial


def provider(kind='read_cache',value='v000',call_id='c1',**overrides):
    body=json.loads(raw(kind,value,call_id))
    body.update(model='deepseek-flash',usage=dict(prompt_tokens=100,completion_tokens=20))
    body.update(overrides)
    return json.dumps(body)


class Tests(unittest.TestCase):
    def setUp(self):
        self.history=NativeHistory(native_initial(),observation_byte_limit=1024)
        self.records={}
        self.guard=CostGuard(ceiling_micro_cny=100000,max_calls=4,input_rate=2,output_rate=8)
        self.counter=Mock(); self.counter.inspect.return_value={'reference_input_tokens':100}
        self.transport=Mock(return_value=provider())
        self.adapter=NativeTrajectoryAdapter(counter=self.counter,transport=self.transport,
            save=self.save,guard=self.guard,deadline=600,clock=lambda:0)

    def save(self,name,value):
        if name in self.records: raise FileExistsError(name)
        self.records[name]=deepcopy(value)

    def test_two_calls_accounted_and_exact_raw_preserved(self):
        req=self.history.begin(); out=self.adapter(req); self.history.accept(out); self.history.observe('c1','[]')
        self.transport.return_value=provider('answer','done','c2')
        self.adapter(self.history.begin())
        self.assertEqual(self.guard.accounted,720)
        self.assertEqual(self.guard.uncertain,{})
        self.assertEqual(self.records['api-g001.provider']['raw_response_text'],out)
        self.assertTrue(self.adapter.closed)

    def test_history_cannot_replace_model_call(self):
        out=self.adapter(self.history.begin()); self.history.accept(out); self.history.observe('c1','[]')
        req=self.history.begin(); req['payload']['messages'][2]['tool_calls'][0]['function']['name']='run_sql'
        req['payload']['messages'][2]['tool_calls'][0]['function']['arguments']='{"sql":"SELECT 1"}'
        with self.assertRaises(ValueError): self.adapter(req)
        self.assertEqual(self.transport.call_count,1)

    def test_repeat_first_request_not_dispatched(self):
        req=self.history.begin(); self.adapter(req)
        with self.assertRaises(ValueError): self.adapter(req)
        self.assertEqual(self.transport.call_count,1)

    def test_transport_failure_reserve_and_no_retry(self):
        self.transport.side_effect=TimeoutError('fixture')
        req=self.history.begin()
        with self.assertRaises(TimeoutError): self.adapter(req)
        self.assertEqual(sum(self.guard.uncertain.values()),4296)
        with self.assertRaises(RuntimeError): self.adapter(req)
        self.assertEqual(self.transport.call_count,1)

    def test_count_mismatch_settled_then_stopped(self):
        self.transport.return_value=provider(usage=dict(prompt_tokens=101,completion_tokens=20))
        with self.assertRaises(ValueError): self.adapter(self.history.begin())
        self.assertEqual(self.guard.accounted,362)
        self.assertTrue(self.guard.terminal)

    def test_bad_usage_and_duplicate_json_keep_reserve(self):
        for value in (provider(usage={}),'{"usage":{},"usage":{}}',provider(error='failure')):
            self.setUp(); self.transport.return_value=value
            with self.assertRaises(ValueError): self.adapter(self.history.begin())
            self.assertEqual(sum(self.guard.uncertain.values()),4296)

    def test_late_result_accounted(self):
        self.adapter.clock=Mock(side_effect=[0,0,601])
        with self.assertRaises(TimeoutError): self.adapter(self.history.begin())
        self.assertEqual(self.guard.accounted,360)
        self.assertIn('api-g001.provider',self.records)

    def test_deadline_after_pending_prevents_call(self):
        self.adapter.clock=Mock(side_effect=[0,601])
        with self.assertRaises(TimeoutError): self.adapter(self.history.begin())
        self.transport.assert_not_called()

    def test_pending_write_failure_prevents_call(self):
        def save(name,value):
            if name.endswith('.pending'): raise OSError('fixture disk full')
            self.save(name,value)
        self.adapter.save=save
        with self.assertRaises(OSError): self.adapter(self.history.begin())
        self.transport.assert_not_called()

    def test_invalid_action_is_observed_not_retried(self):
        body=json.loads(provider()); body['choices'][0]['message']['tool_calls'][0]['function']['arguments']='{}'
        self.transport.return_value=json.dumps(body)
        req=self.history.begin(); self.assertEqual(self.adapter(req),self.transport.return_value)
        self.assertFalse(self.guard.terminal)
        self.assertFalse(self.records['api-g001.admission']['format_admitted'])
        with self.assertRaises(RuntimeError): self.adapter(req)

    def test_cap_and_budget_prevent_call(self):
        for n in (True,0,8193):
            self.setUp(); self.counter.inspect.return_value={'reference_input_tokens':n}
            with self.assertRaises(ValueError): self.adapter(self.history.begin())
            self.transport.assert_not_called()
        self.setUp(); self.guard.ceiling=1
        with self.assertRaises(RuntimeError): self.adapter(self.history.begin())
        self.transport.assert_not_called()

    def test_identity_and_overrun_stops(self):
        for value in (provider(model='other'),provider(usage=dict(prompt_tokens=100,completion_tokens=513))):
            self.setUp(); self.transport.return_value=value
            with self.assertRaises(ValueError): self.adapter(self.history.begin())
            self.assertTrue(self.guard.terminal)


if __name__=='__main__': unittest.main()
