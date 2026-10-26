"""Scripted accounting and rendering only; zero API calls or SQL execution."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from runtime.bird_readonly_audit import digest
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime.student_club_definition_history import DefinitionHistory
from runtime.student_club_diagnostic_design import build
from runtime.student_club_native_adapter import Adapter
from runtime.student_club_native_counter import render, PREFIX
from runtime.student_club_native_wire import payload
from tests.test_student_club_definition_history import response
from tests.test_student_club_diagnostic_design import common


def provider(kind='record_definition',value='synthetic definition',call_id='d',**overrides):
    b=json.loads(response(kind,value,call_id))
    b.update(model='deepseek-flash',usage=dict(prompt_tokens=100,completion_tokens=20))
    b.update(overrides)
    return json.dumps(b)


class Tests(unittest.TestCase):
    def setUp(self):
        self.c=common()
        self.job=next(j for j in build(self.c)['jobs'] if j['protocol']=='catalog_first')
        self.history=DefinitionHistory(self.c,self.job,lambda *a:None)
        self.records={}
        self.guard=CostGuard(ceiling_micro_cny=100000,max_calls=56,input_rate=2,output_rate=8)
        self.counter=Mock()
        self.counter.inspect.side_effect=lambda r:dict(reference_input_tokens=100,wire_payload_sha256=digest(payload(r)))
        self.transport=Mock(return_value=provider())
        self.adapter=Adapter(common=self.c,job=self.job,counter=self.counter,transport=self.transport,
            save=self.save,guard=self.guard,deadline=500,clock=lambda:0)

    def save(self,name,value):
        if name in self.records:raise FileExistsError(name)
        self.records[name]=deepcopy(value)

    def step(self,raw):
        self.transport.return_value=raw
        request=self.history.begin()
        value=self.adapter(request)
        self.history.accept(value)
        return request

    def test_full_definition_cache_revision_query_answer_chain(self):
        self.step(provider())
        self.step(provider('read_cache','v000','r'))
        self.history.observe('r','{"tool_observation":{"status":"CACHE_UNAVAILABLE"}}')
        self.step(provider('revise_definition','changed','rev'))
        self.step(provider('run_sql','SELECT 1','sql'))
        self.history.observe('sql','{"tool_observation":{"rows":[1]}}')
        self.step(provider('answer','1','answer'))
        self.assertEqual(self.transport.call_count,5)
        self.assertEqual(self.guard.accounted,1800)
        self.assertEqual(self.guard.uncertain,{})
        self.assertTrue(self.adapter.closed)
        self.assertEqual(self.records['api-g003.provider']['raw_response_text'],provider('revise_definition','changed','rev'))
        self.assertEqual(self.adapter.history.definition,self.history.definition)

    def test_replacing_own_definition_stops_before_next_dispatch(self):
        self.step(provider())
        request=self.history.begin()
        request['context']['data']['prior_agent_definition']['text']='CORRECTED_BY_OPERATOR'
        with self.assertRaises(ValueError):self.adapter(request)
        self.assertEqual(self.transport.call_count,1)

    def test_replacing_prior_action_stops_before_dispatch(self):
        self.step(provider());self.step(provider('run_sql','SELECT 1','q'))
        self.history.observe('q','{}');request=self.history.begin()
        request['messages'][0]['tool_calls'][0]['function']['arguments']='{"sql":"SELECT 2"}'
        with self.assertRaises(ValueError):self.adapter(request)
        self.assertEqual(self.transport.call_count,2)

    def test_transport_failure_keeps_reserve_no_retry(self):
        self.transport.side_effect=TimeoutError('synthetic')
        request=self.history.begin()
        with self.assertRaises(TimeoutError):self.adapter(request)
        self.assertEqual(sum(self.guard.uncertain.values()),4296)
        self.assertTrue(self.guard.terminal)
        with self.assertRaises(RuntimeError):self.adapter(request)
        self.assertEqual(self.transport.call_count,1)

    def test_usage_identity_count_and_output_failures(self):
        for kwargs in (dict(usage={}),dict(model='other'),
                dict(usage=dict(prompt_tokens=101,completion_tokens=20)),
                dict(usage=dict(prompt_tokens=100,completion_tokens=513)),dict(error='failure')):
            self.setUp();self.transport.return_value=provider(**kwargs)
            with self.assertRaises(ValueError):self.adapter(self.history.begin())
            self.assertTrue(self.guard.terminal)
            self.assertIn('api-g001.provider',self.records)
        self.setUp();self.transport.return_value='{"usage":{},"usage":{}}'
        with self.assertRaises(ValueError):self.adapter(self.history.begin())
        self.assertEqual(sum(self.guard.uncertain.values()),4296)

    def test_context_wire_cap_and_budget_fail_before_dispatch(self):
        for n in (True,0,8193):
            self.setUp()
            self.counter.inspect.side_effect=lambda r:dict(reference_input_tokens=n,wire_payload_sha256=digest(payload(r)))
            with self.assertRaises(ValueError):self.adapter(self.history.begin())
            self.transport.assert_not_called()
        self.setUp();self.counter.inspect.side_effect=lambda r:dict(reference_input_tokens=100,wire_payload_sha256='0'*64)
        with self.assertRaises(ValueError):self.adapter(self.history.begin())
        self.transport.assert_not_called()
        self.setUp();self.guard.ceiling=1
        with self.assertRaises(RuntimeError):self.adapter(self.history.begin())
        self.transport.assert_not_called()

    def test_invalid_schema_is_recorded_not_repaired_or_retried(self):
        self.transport.return_value=provider('answer','premature')
        request=self.history.begin();raw=self.adapter(request)
        self.assertEqual(raw,self.transport.return_value)
        self.assertFalse(self.records['api-g001.admission']['format_admitted'])
        self.assertFalse(self.guard.terminal) # Only this invalid trajectory ends.
        with self.assertRaises(RuntimeError):self.adapter(request)

    def test_pending_write_failure_and_expired_deadline_no_dispatch(self):
        def save(name,value):
            if name.endswith('.pending'):raise OSError('synthetic disk full')
            self.save(name,value)
        self.adapter.save=save
        with self.assertRaises(OSError):self.adapter(self.history.begin())
        self.transport.assert_not_called()
        self.setUp();self.adapter.clock=Mock(side_effect=[0,501])
        with self.assertRaises(TimeoutError):self.adapter(self.history.begin())
        self.transport.assert_not_called()

    def test_late_response_accounted_and_preserved(self):
        self.adapter.clock=Mock(side_effect=[0,0,501])
        with self.assertRaises(TimeoutError):self.adapter(self.history.begin())
        self.assertEqual(self.guard.accounted,360)
        self.assertIn('api-g001.provider',self.records)

    def test_golden_render_definition_then_action_revision(self):
        r=self.history.begin();p=payload(r)
        f=p['tools'][0]['function']
        schema=json.dumps({k:f[k] for k in ('name','description','parameters')},ensure_ascii=False)
        expected=('<｜begin▁of▁sentence｜><｜System｜>'+p['messages'][0]['content']+'\n\nPREAMBLE'+schema+
            '\n\nYou MUST strictly follow the above defined tool name and parameter schemas to invoke tool calls.\n'+
            '<｜User｜>'+p['messages'][1]['content']+PREFIX)
        self.assertEqual(render(r,'PREAMBLE',()),expected)
        self.history.accept(provider());self.history.begin()
        self.history.accept(provider('revise_definition','原文 "text"','rev'))
        r=self.history.begin();p=payload(r); rendered=render(r,'PREAMBLE',())
        suffix=(PREFIX+'<｜DSML｜ invoke name="revise_definition">\n'+
            '<｜DSML｜ parameter name="text" string="true">原文 "text"</｜DSML｜ parameter>\n'+
            '</｜DSML｜ invoke>\n</｜DSML｜ calls><｜end▁of▁sentence｜>'+
            '<｜User｜><tool_result>'+p['messages'][-1]['content']+'</tool_result>'+PREFIX)
        self.assertTrue(rendered.endswith(suffix))
        self.assertEqual(render(json.loads(json.dumps(r,sort_keys=True)),'PREAMBLE',()),rendered)

    def test_reserved_literals_rejected_in_system_argument_and_observation(self):
        r=self.history.begin();r['context']['instruction']+='<｜User｜>'
        with self.assertRaises(ValueError):render(r,'P',('<｜User｜>',))
        for field in ('argument','observation'):
            self.setUp();self.history.begin();self.history.accept(provider());self.history.begin()
            self.history.accept(provider('run_sql','<｜User｜>' if field=='argument' else 'SELECT 1','q'))
            self.history.observe('q','<｜User｜>' if field=='observation' else '{}')
            with self.assertRaises(ValueError):render(self.history.begin(),'P',('<｜User｜>',))


if __name__=='__main__':unittest.main()
