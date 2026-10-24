"""Offline fake-response checks only. No real API key or requests."""
import json
from pathlib import Path
import tempfile
import unittest
from scripts import deepseek_official_probe as probe


def reply(index):
    return {'transport_status': 'RESPONSE_RECEIVED', 'response_text': json.dumps({
        'model': 'documented-alias-return', 'usage': {'prompt_tokens': 50, 'completion_tokens': 12},
        'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(probe.EXPECTED[index])}}]})}


class ProbeTests(unittest.TestCase):
    def test_fixed_safe_payload(self):
        self.assertEqual(probe.URL, 'https://api.deepseek.com/v1/chat/completions')
        self.assertEqual(probe.plan()['maximum_calls'], 2)
        for i in range(2):
            p=probe.payload(i)
            self.assertEqual(p['thinking'], {'type': 'disabled'})
            self.assertEqual(p['max_tokens'], 128)
            self.assertNotIn('seed', p)
            self.assertNotIn('tools', p)
            self.assertEqual(probe.assess(reply(i), i)['status'], 'INTERFACE_CHECK_PASSED')

    def test_missing_usage_and_truncation_fail(self):
        for field in ('usage', 'choices'):
            record=reply(0); raw=json.loads(record['response_text']); raw.pop(field)
            record['response_text']=json.dumps(raw)
            self.assertEqual(probe.assess(record,0)['status'], 'STOP_INVALID_RESPONSE_OR_USAGE')

    def test_failure_preserved_without_retry_and_secret_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'new'; calls=[]
            def fake(index,key):
                calls.append(index)
                return {'transport_status':'HTTP_ERROR','unsafe_echo':key}
            result=probe.execute('FAKE_TEST_SECRET',output,fake)
            self.assertEqual(calls,[0])
            self.assertEqual(result['status'],'PROBE_STOPPED_NO_RETRY')
            self.assertTrue((output/'call-1.pending.json').exists())
            for p in output.glob('*.json'):
                self.assertNotIn('FAKE_TEST_SECRET',p.read_text('utf-8'))
            with self.assertRaises(FileExistsError): probe.execute('FAKE_TEST_SECRET',output,fake)

    def test_success_is_not_research_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=probe.execute('FAKE_TEST_SECRET',Path(tmp)/'new',lambda i,k:reply(i))
            self.assertEqual(result['attempted_calls'],2)
            self.assertEqual(result['research_samples'],0)
            self.assertEqual(result['status'],'PROBE_FINISHED')


if __name__=='__main__': unittest.main()
