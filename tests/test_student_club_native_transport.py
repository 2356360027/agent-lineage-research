"""Scripted transport checks only: no sockets, credentials or model calls."""
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from runtime import student_club_native_transport as transport
from runtime.student_club_native_wire import payload
from tests.test_student_club_definition_history import make

SYNTHETIC_KEY='unit-test-placeholder-not-a-credential'


def packet():
    h,_=make('ordinary')
    return dict(profile=transport.PROFILE,key=SYNTHETIC_KEY,payload=payload(h.begin()),timeout=15)


class Tests(unittest.TestCase):
    def test_bad_packet_never_builds_network_opener(self):
        for change in ({'profile':'legacy'},{'timeout':True},{'timeout':91},{'extra':1}):
            p=packet();p.update(change)
            with patch.object(transport.urllib.request,'build_opener') as build:
                with self.assertRaises(ValueError):transport.worker_request(p)
                build.assert_not_called()

    def test_fixed_endpoint_no_proxy_and_redacted_response(self):
        response=Mock();response.read.return_value=('echo '+SYNTHETIC_KEY).encode()
        opened=Mock();opened.__enter__=Mock(return_value=response);opened.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=opened
        with patch.object(transport.urllib.request,'build_opener',return_value=opener) as build:
            result=transport.worker_request(packet())
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.deepseek.com/beta/chat/completions')
        self.assertEqual(request.get_method(),'POST')
        self.assertEqual(build.call_args.args[0].proxies,{})
        self.assertIsInstance(build.call_args.args[1],transport.NoRedirect)
        self.assertNotIn(SYNTHETIC_KEY,str(result))
        self.assertEqual(result['response_text'],'echo [REDACTED]')

    def test_http_and_network_errors_preserve_uncertainty_without_details(self):
        for error,status in ((HTTPError('unused',401,SYNTHETIC_KEY,{},None),'HTTP_ERROR'),
                             (TimeoutError(SYNTHETIC_KEY),'TRANSPORT_ERROR')):
            opener=Mock();opener.open.side_effect=error
            with patch.object(transport.urllib.request,'build_opener',return_value=opener):
                result=transport.worker_request(packet())
            self.assertEqual(result['transport_status'],status)
            self.assertTrue(result['billing_unknown'])
            self.assertNotIn(SYNTHETIC_KEY,str(result))

    def test_secret_only_in_owned_stdin_packet(self):
        p=packet()
        with patch.object(transport,'run_owned',return_value={'transport_status':'WORKER_TIMEOUT','billing_unknown':True}) as run:
            transport.invoke(p['payload'],SYNTHETIC_KEY,15)
        command,stdin,timeout=run.call_args.args
        self.assertNotIn(SYNTHETIC_KEY,str(command))
        self.assertEqual(json.loads(stdin)['key'],SYNTHETIC_KEY)
        self.assertEqual(timeout,15)
        self.assertEqual(command[-2:],[transport.MODULE,'--worker'])

    def test_failure_no_retry_and_five_call_limit(self):
        records=[];p=packet()['payload']
        t=transport.Transport(SYNTHETIC_KEY,lambda name,value:records.append((name,value)))
        with patch.object(transport,'invoke',return_value={'transport_status':'WORKER_TIMEOUT','billing_unknown':True}) as invoke:
            with self.assertRaises(RuntimeError):t(p,15)
            with self.assertRaises(RuntimeError):t(p,15)
            self.assertEqual(invoke.call_count,1)
        self.assertEqual(len(records),1)
        t=transport.Transport(SYNTHETIC_KEY,lambda *args:None)
        with patch.object(transport,'invoke',return_value={'transport_status':'RESPONSE_RECEIVED','response_text':'{}'}) as invoke:
            for _ in range(5):self.assertEqual(t(p,15),'{}')
            with self.assertRaises(RuntimeError):t(p,15)
            self.assertEqual(invoke.call_count,5)


if __name__=='__main__':unittest.main()
