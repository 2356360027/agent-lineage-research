"""No network: mocked HTTP and one owned sleeping subprocess timeout test."""
import io
import json
import subprocess
import sys
import unittest
from unittest.mock import patch, Mock
import urllib.error

from runtime import deepseek_official_transport as transport
from runtime.deepseek_trajectory_adapter import make_payload


class Tests(unittest.TestCase):
    def payload(self):
        return make_payload([{'role':'system','content':'Fixture JSON'},
                             {'role':'user','content':'Fixture only'}])

    def test_request_official_only_and_key_absent_from_body(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"model":"fixture"}')
        with patch('urllib.request.build_opener', return_value=opener) as build:
            result = transport.worker_request({'payload':self.payload(),
                'key':'FAKE_TEST_SECRET','timeout':1})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, transport.URL)
        self.assertNotIn(b'FAKE_TEST_SECRET', request.data)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], transport.NoRedirect)
        self.assertEqual(result['transport_status'], 'RESPONSE_RECEIVED')

    def test_redirects_refused(self):
        req = transport.urllib.request.Request(transport.URL)
        with self.assertRaises(urllib.error.HTTPError):
            transport.NoRedirect().redirect_request(req,None,302,'redirect',{},'https://example.org')

    def test_echo_redacted_and_http_error_body_not_logged(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"echo":"FAKE_TEST_SECRET"}')
        with patch('urllib.request.build_opener',return_value=opener):
            result = transport.worker_request({'payload':self.payload(),'key':'FAKE_TEST_SECRET','timeout':1})
        self.assertNotIn('FAKE_TEST_SECRET', json.dumps(result))
        opener.open.side_effect = urllib.error.HTTPError(transport.URL,401,'FAKE_TEST_SECRET',{},None)
        with patch('urllib.request.build_opener',return_value=opener):
            result = transport.worker_request({'payload':self.payload(),'key':'FAKE_TEST_SECRET','timeout':1})
        self.assertEqual(result['http_status'],401)
        self.assertNotIn('FAKE_TEST_SECRET',json.dumps(result))

    def test_key_in_pipe_not_arguments(self):
        with patch.object(transport,'run_owned',return_value={'transport_status':'WORKER_TIMEOUT'}) as run:
            transport.invoke(self.payload(),'FAKE_TEST_SECRET',1)
        command, packet, timeout = run.call_args.args
        self.assertNotIn('FAKE_TEST_SECRET',repr(command))
        self.assertEqual(json.loads(packet)['key'],'FAKE_TEST_SECRET')
        self.assertEqual(timeout,1)

    def test_owned_timeout_kills_and_waits_child(self):
        created = []
        original = subprocess.Popen
        def capture(*args, **kwargs):
            child = original(*args, **kwargs)
            created.append(child)
            return child
        with patch('subprocess.Popen', side_effect=capture):
            result = transport.run_owned([sys.executable,'-c','import time; time.sleep(5)'],b'',0.1)
        self.assertEqual(result['transport_status'],'WORKER_TIMEOUT')
        self.assertFalse(result['server_cancellation_verified'])
        self.assertEqual(len(created),1)
        self.assertIsNotNone(created[0].poll())

    def test_failure_or_malformed_reply_is_saved_once_no_retry(self):
        for reply in ({'transport_status':'WORKER_TIMEOUT','billing_unknown':True},
                      {'transport_status':'RESPONSE_RECEIVED','response_text':'not JSON'}):
            records = {}
            caller = transport.OfficialTransport('FAKE_TEST_SECRET',lambda name,r:records.update({name:r}))
            with patch.object(transport,'invoke',return_value=reply) as invoke:
                with self.assertRaises((ValueError,RuntimeError)): caller(self.payload(),1)
                with self.assertRaises(RuntimeError): caller(self.payload(),1)
            self.assertEqual(invoke.call_count,1)
            self.assertEqual(records['transport-001'],reply)

    def test_settings_cannot_enable_tools_or_another_provider(self):
        for key,value in [('model','other'),('max_tokens',1024),('tools',[]),('stream',True)]:
            p = self.payload(); p[key] = value
            with patch.object(transport,'run_owned') as run:
                with self.assertRaises(ValueError): transport.invoke(p,'FAKE_TEST_SECRET',1)
                run.assert_not_called()
        for timeout in (0,91,float('nan'),True):
            with self.assertRaises(ValueError): transport.invoke(self.payload(),'FAKE_TEST_SECRET',timeout)


if __name__ == '__main__': unittest.main()
