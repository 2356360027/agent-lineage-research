"""Mocked transport security/profile checks. No external requests or keys."""
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import Mock, patch
import urllib.error

from runtime import deepseek_native_trajectory_transport as transport
from runtime.deepseek_native_transport import encode_payload as legacy_encode
from runtime.deepseek_official_transport import NoRedirect
from tests.test_native_trajectory_counter import at

DUMMY = 'fixture-only-not-a-real-credential'


def packet(turn=2):
    return dict(profile=transport.PROFILE, key=DUMMY, payload=at(turn), timeout=20)


class Tests(unittest.TestCase):
    def test_all_four_histories_encoded_without_mutation(self):
        for turn in range(1, 5):
            payload = at(turn)
            before = deepcopy(payload)
            self.assertEqual(json.loads(transport.encode_payload(payload)), before)
            self.assertEqual(payload, before)

    def test_legacy_first_transport_still_rejects_continuations(self):
        legacy_encode(at(1))
        with self.assertRaises(ValueError):
            legacy_encode(at(2))

    def test_bad_profile_settings_and_history_prevent_network(self):
        variants = []
        p = packet(); p.pop('profile'); variants.append(p)
        p = packet(); p['profile'] = 'implicit'; variants.append(p)
        p = packet(); p['endpoint'] = 'https://example.invalid'; variants.append(p)
        p = packet(); p['payload']['messages'][-1]['tool_call_id'] = 'wrong'; variants.append(p)
        p = packet(); p['payload']['messages'][-1]['role'] = 'system'; variants.append(p)
        p = packet(); p['payload']['max_tokens'] = 1024; variants.append(p)
        for timeout in (0, 91, True, float('nan'), float('inf')):
            p = packet(); p['timeout'] = timeout; variants.append(p)
        for p in variants:
            with self.subTest(p=p), patch.object(transport.urllib.request, 'build_opener') as opener:
                with self.assertRaises(ValueError): transport.worker_request(p)
                opener.assert_not_called()

    def test_fixed_endpoint_no_proxy_bounded_read_and_redaction(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = ('{"echo":"' + DUMMY + '"}').encode()
        opener = Mock(); opener.open.return_value = response
        with patch.object(transport.urllib.request, 'build_opener', return_value=opener) as build:
            out = transport.worker_request(packet())
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], NoRedirect)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, transport.ENDPOINT)
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(json.loads(req.data), at(2))
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 20)
        response.read.assert_called_once_with(transport.MAX_RESPONSE + 1)
        self.assertNotIn(DUMMY, json.dumps(out))
        self.assertIn('[REDACTED]', out['response_text'])

    def test_redirect_refused(self):
        req = transport.urllib.request.Request(transport.ENDPOINT)
        with self.assertRaises(urllib.error.HTTPError):
            NoRedirect().redirect_request(req, None, 302, 'redirect', {}, 'https://example.invalid')

    def test_errors_remain_uncertain_and_do_not_expose_details(self):
        for error in (urllib.error.HTTPError(transport.ENDPOINT, 429, DUMMY, {}, None),
                      TimeoutError(DUMMY)):
            opener = Mock(); opener.open.side_effect = error
            with patch.object(transport.urllib.request, 'build_opener', return_value=opener):
                out = transport.worker_request(packet())
            self.assertTrue(out['billing_unknown'])
            self.assertNotIn(DUMMY, json.dumps(out))
            opener.open.assert_called_once()

    def test_oversized_or_non_utf8_response_not_repaired(self):
        for raw in (b'x' * (transport.MAX_RESPONSE + 1), b'\xff'):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = raw
            opener = Mock(); opener.open.return_value = response
            with patch.object(transport.urllib.request, 'build_opener', return_value=opener):
                out = transport.worker_request(packet())
            self.assertTrue(out['billing_unknown'])
            self.assertNotIn('response_text', out)

    def test_owned_worker_key_only_in_stdin(self):
        result = dict(transport_status='RESPONSE_RECEIVED', response_text='echo ' + DUMMY)
        with patch.object(transport, 'run_owned', return_value=result) as owned:
            out = transport.invoke(at(2), DUMMY, 12)
        argv, stdin, timeout = owned.call_args.args
        self.assertNotIn(DUMMY, repr(argv))
        self.assertEqual(argv[-3:], ['-m', transport.MODULE, '--worker'])
        self.assertEqual(json.loads(stdin)['key'], DUMMY)
        self.assertEqual(json.loads(stdin)['profile'], transport.PROFILE)
        self.assertEqual(timeout, 12)
        self.assertNotIn(DUMMY, json.dumps(out))

    def test_packet_limit_prevents_worker(self):
        with patch.object(transport, 'MAX_PACKET', 4096), patch.object(transport, 'run_owned') as owned:
            with self.assertRaises(ValueError): transport.invoke(at(2), DUMMY, 12)
            owned.assert_not_called()

    def test_journal_preserves_raw_and_cap_four(self):
        records = {}
        instance = transport.NativeTrajectoryTransport(DUMMY, lambda n, v: records.__setitem__(n, v))
        raw = '{ malformed unparsed response'
        with patch.object(transport, 'invoke', return_value=dict(
                transport_status='RESPONSE_RECEIVED', response_text=raw)) as invoke:
            for turn in range(1, 5): self.assertEqual(instance(at(turn), 10), raw)
            with self.assertRaises(RuntimeError): instance(at(4), 10)
            self.assertEqual(invoke.call_count, 4)
        self.assertEqual(records['transport-001']['response_text'], raw)

    def test_failure_or_journal_error_prevents_retry(self):
        for journal_fails in (False, True):
            def save(name, value):
                if journal_fails: raise OSError('fixture')
            instance = transport.NativeTrajectoryTransport(DUMMY, save)
            result = (dict(transport_status='RESPONSE_RECEIVED', response_text='{}') if journal_fails
                      else dict(transport_status='WORKER_TIMEOUT', billing_unknown=True))
            with patch.object(transport, 'invoke', return_value=result) as invoke:
                with self.assertRaises((OSError, RuntimeError)): instance(at(1), 10)
                with self.assertRaises(RuntimeError): instance(at(1), 10)
                invoke.assert_called_once()

    def test_worker_profile_rejection_output_safe(self):
        invalid = packet(); invalid['profile'] = 'wrong'
        stream = Mock(); stream.buffer = io.BytesIO(json.dumps(invalid).encode())
        output = io.StringIO()
        with patch.object(transport.sys, 'argv', ['module', '--worker']), \
                patch.object(transport.sys, 'stdin', stream), \
                patch.object(transport.sys, 'stdout', output), \
                patch.object(transport.urllib.request, 'build_opener') as opener:
            transport.main()
        result = json.loads(output.getvalue())
        self.assertFalse(result['request_dispatched'])
        self.assertFalse(result['billing_unknown'])
        self.assertNotIn(DUMMY, output.getvalue())
        opener.assert_not_called()

    def test_accounting_history_transport_composition(self):
        from runtime.deepseek_trajectory_adapter import CostGuard
        from runtime.native_action_history import NativeHistory
        from runtime.native_trajectory_adapter import NativeTrajectoryAdapter
        from tests.test_native_trajectory_adapter import provider
        records = {}
        def save(name, value):
            if name in records: raise FileExistsError(name)
            records[name] = deepcopy(value)
        history = NativeHistory(at(1), observation_byte_limit=1024)
        counter = Mock(); counter.inspect.return_value = {'reference_input_tokens': 100}
        guard = CostGuard(ceiling_micro_cny=10000, max_calls=4, input_rate=2, output_rate=8)
        client = transport.NativeTrajectoryTransport(DUMMY, save)
        adapter = NativeTrajectoryAdapter(counter=counter, transport=client, save=save,
                                          guard=guard, deadline=600, clock=lambda: 0)
        opener = Mock()
        responses = []
        for raw in (provider(), provider('answer', 'done', 'c2')):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = raw.encode()
            responses.append(response)
        opener.open.side_effect = responses
        # Exercise the real request encoder and worker HTTP construction, with
        # both process creation and network replaced. No billable dispatch.
        def owned(argv, stdin, timeout):
            return transport.worker_request(json.loads(stdin))
        with patch.object(transport, 'run_owned', side_effect=owned), \
                patch.object(transport.urllib.request, 'build_opener', return_value=opener):
            history.accept(adapter(history.begin()))
            history.observe('c1', '[]')
            final = history.accept(adapter(history.begin()))
        self.assertTrue(final['stop'])
        self.assertEqual(guard.accounted, 720)  # synthetic usage, not actual cost
        self.assertEqual(guard.uncertain, {})
        second = json.loads(opener.open.call_args_list[1].args[0].data)
        self.assertEqual(second['messages'][-1],
                         dict(role='tool', tool_call_id='c1', content='[]'))
        self.assertEqual(records['transport-002']['response_text'],
                         records['api-g002.provider']['raw_response_text'])
        self.assertNotIn(DUMMY, json.dumps(records))


if __name__ == '__main__': unittest.main()
