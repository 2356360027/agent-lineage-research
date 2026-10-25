"""Injected and mocked transport only. No paid model or SQL calls."""
import io
import json
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error

from runtime import deepseek_native_transport as transport
from runtime.deepseek_official_transport import validate_payload as legacy_validate
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime.native_action_interface import build_candidate
from runtime.native_first_response_adapter import NativeFirstAdapter
from tests.test_native_action_interface import request, response


def payload(): return build_candidate(request())['payload']


def provider():
    result = response()
    result.update(model='deepseek-flash', usage={'prompt_tokens':100, 'completion_tokens':20})
    return result


def harness(raw=None, ceiling=100000, clock=lambda:0):
    records = {}
    def save(name, value):
        if name in records: raise FileExistsError(name)
        records[name] = value
    counter = Mock()
    counter.inspect.return_value = {'reference_input_tokens':100, 'inference_authorized':False}
    generate = Mock(return_value=json.dumps(provider()) if raw is None else raw)
    guard = CostGuard(ceiling_micro_cny=ceiling, max_calls=8, input_rate=2, output_rate=8)
    adapter = NativeFirstAdapter(counter=counter, transport=generate, save=save,
                                 guard=guard, deadline=600, clock=clock)
    return adapter, records, generate, guard


class TransportTests(unittest.TestCase):
    def test_official_beta_only_no_proxy_or_body_secret(self):
        opener = Mock(); opener.open.return_value = io.BytesIO(b'{}')
        with patch('urllib.request.build_opener', return_value=opener) as build:
            result = transport.worker_request({'payload':payload(), 'key':'FIXTURE_SECRET', 'timeout':1})
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://api.deepseek.com/beta/chat/completions')
        self.assertNotIn(b'FIXTURE_SECRET', req.data)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], transport.NoRedirect)
        self.assertEqual(result['transport_status'], 'RESPONSE_RECEIVED')

    def test_secret_stdin_only_and_result_redaction(self):
        with patch.object(transport, 'run_owned', return_value={'echo':'FIXTURE_SECRET'}) as run:
            result = transport.invoke(payload(), 'FIXTURE_SECRET', 1)
        command, packet, timeout = run.call_args.args
        self.assertNotIn('FIXTURE_SECRET', repr(command))
        self.assertEqual(json.loads(packet)['key'], 'FIXTURE_SECRET')
        self.assertNotIn('FIXTURE_SECRET', json.dumps(result))
        self.assertEqual(timeout, 1)

    def test_validation_precedes_dispatch_and_legacy_unchanged(self):
        with self.assertRaises(ValueError): legacy_validate(payload())
        for change in ({'tool_choice':'auto'}, {'stream':True}, {'url':'https://example.org'}):
            p = payload(); p.update(change)
            with patch.object(transport,'run_owned') as run:
                with self.assertRaises(ValueError): transport.invoke(p,'FIXTURE_SECRET',1)
                run.assert_not_called()
        for timeout in (True, 0, 91, float('nan')):
            with self.assertRaises(ValueError): transport.invoke(payload(), 'FIXTURE_SECRET', timeout)

    def test_raw_preserved_and_failed_transport_terminal(self):
        records = {}
        caller = transport.NativeTransport('FIXTURE_SECRET', lambda n,r:records.update({n:r}))
        record = {'transport_status':'RESPONSE_RECEIVED','response_text':'bad json kept verbatim'}
        with patch.object(transport,'invoke',return_value=record):
            self.assertEqual(caller(payload(),1),record['response_text'])
        self.assertEqual(records['transport-001'], record)
        failed = transport.NativeTransport('FIXTURE_SECRET', lambda n,r:records.update({n:r}))
        with patch.object(transport,'invoke',return_value={'transport_status':'WORKER_TIMEOUT'}) as run:
            with self.assertRaises(RuntimeError): failed(payload(),1)
            with self.assertRaises(RuntimeError): failed(payload(),1)
            self.assertEqual(run.call_count,1)

    def test_http_failure_does_not_log_error_text(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(transport.ENDPOINT,401,'FIXTURE_SECRET',{},None)
        with patch('urllib.request.build_opener',return_value=opener):
            result = transport.worker_request({'payload':payload(),'key':'FIXTURE_SECRET','timeout':1})
        self.assertEqual(result['http_status'],401)
        self.assertNotIn('FIXTURE_SECRET',json.dumps(result))
        self.assertTrue(result['billing_unknown'])

    def test_owned_child_timeout_does_not_claim_server_cancelled(self):
        created = []; original = subprocess.Popen
        def capture(*args, **kwargs):
            child = original(*args, **kwargs); created.append(child); return child
        with patch('subprocess.Popen',side_effect=capture):
            result = transport.run_owned([sys.executable,'-c','import time; time.sleep(5)'],b'',0.1)
        self.assertEqual(result['transport_status'],'WORKER_TIMEOUT')
        self.assertFalse(result['server_cancellation_verified'])
        self.assertIsNotNone(created[0].poll())


class AdapterTests(unittest.TestCase):
    def test_success_cost_and_no_execution(self):
        adapter, records, generate, guard = harness()
        score = adapter(payload())
        self.assertTrue(score['admitted'])
        self.assertFalse(score['task_correctness_assessed'])
        self.assertEqual(score['SQL_executions'],0)
        self.assertEqual(guard.accounted,360)
        self.assertEqual(guard.uncertain,{})
        self.assertEqual(list(records),['context','pending','provider','cost','score'])
        with self.assertRaises(RuntimeError): adapter(payload())
        self.assertEqual(generate.call_count,1)

    def test_malformed_arguments_are_observed_not_repaired(self):
        raw = provider()
        raw['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = '{"text":"a"}{"text":"b"}'
        adapter, records, generate, guard = harness(json.dumps(raw))
        self.assertFalse(adapter(payload())['admitted'])
        self.assertFalse(guard.terminal)
        self.assertEqual(records['provider']['raw_response_text'],json.dumps(raw))

    def test_double_calls_do_not_dispatch_or_choose_first(self):
        raw = provider(); raw['choices'][0]['message']['tool_calls'] *= 2
        adapter, records, generate, guard = harness(json.dumps(raw))
        self.assertFalse(adapter(payload())['admitted'])

    def test_reference_count_mismatch_stops_after_usage_recorded(self):
        raw = provider(); raw['usage']['prompt_tokens'] = 101
        adapter, records, generate, guard = harness(json.dumps(raw))
        with self.assertRaises(ValueError): adapter(payload())
        self.assertTrue(guard.terminal)
        self.assertEqual(guard.accounted,362)
        self.assertIn('cost',records)
        self.assertNotIn('score',records)

    def test_missing_invalid_or_ambiguous_usage_keeps_reserve(self):
        cases = [dict(provider(), usage={}), dict(provider(), usage={'prompt_tokens':True,'completion_tokens':20})]
        raw_cases = [json.dumps(r) for r in cases]+['{"usage":{},"usage":{}}','not json']
        for raw in raw_cases:
            with self.subTest(raw=raw):
                adapter, records, generate, guard = harness(raw)
                with self.assertRaises(ValueError): adapter(payload())
                self.assertTrue(guard.terminal)
                self.assertEqual(sum(guard.uncertain.values()),4296)
                self.assertIn('provider',records)

    def test_transport_timeout_keeps_reserve_and_no_retry(self):
        adapter, records, generate, guard = harness()
        generate.side_effect = TimeoutError('fixture')
        with self.assertRaises(TimeoutError): adapter(payload())
        self.assertEqual(sum(guard.uncertain.values()),4296)
        with self.assertRaises(RuntimeError): adapter(payload())
        self.assertEqual(generate.call_count,1)

    def test_budget_and_deadline_prevent_dispatch(self):
        for ceiling, clock in ((1, lambda:0), (100000, lambda:601)):
            adapter, records, generate, guard = harness(ceiling=ceiling,clock=clock)
            with self.assertRaises((RuntimeError,TimeoutError)): adapter(payload())
            generate.assert_not_called()

    def test_late_response_kept_and_accounted(self):
        times = iter((0,601))
        adapter, records, generate, guard = harness(clock=lambda:next(times))
        with self.assertRaises(TimeoutError): adapter(payload())
        self.assertIn('provider',records); self.assertIn('cost',records)
        self.assertEqual(guard.accounted,360)

    def test_overrun_recorded_and_stops(self):
        raw = provider(); raw['usage']['completion_tokens'] = 513
        adapter, records, generate, guard = harness(json.dumps(raw))
        with self.assertRaises(ValueError): adapter(payload())
        self.assertEqual(guard.accounted,4304)
        self.assertTrue(guard.terminal)

    def test_failed_pending_write_prevents_dispatch(self):
        adapter, records, generate, guard = harness()
        def bad_save(name, value):
            if name == 'pending': raise OSError('fixture full disk')
            records[name] = value
        adapter.save = bad_save
        with self.assertRaises(OSError): adapter(payload())
        generate.assert_not_called()
        self.assertTrue(guard.terminal)


if __name__ == '__main__': unittest.main()
