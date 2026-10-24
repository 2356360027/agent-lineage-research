"""Full fixed batch with scripted responses and synthetic SQLite; no API."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import test_cache_intent_batch as fixtures
from tests.test_deepseek_trajectory import FixtureCounter
from runtime import bird_readonly_audit as audit
from runtime.deepseek_recovery_batch import run_batch
from runtime.cache_intent_journal import verify


class Tests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.BatchTests()
        self.addCleanup(self.f.doCleanups)
        temporary_directory = tempfile.TemporaryDirectory
        workspace = Path(__file__).resolve().parents[1]/'runtime/runs'
        with patch.object(fixtures.tempfile, 'TemporaryDirectory',
                          side_effect=lambda: temporary_directory(dir=workspace)):
            self.f.setUp()
        self.counter = FixtureCounter()
        self.calls = {}

    def run_script(self, response=None, clock=lambda:0):
        def factory(job,save):
            def transport(payload,timeout):
                n = self.calls.get(job,0)+1; self.calls[job] = n
                content = (self.f.contract if n==1 else
                    json.dumps({'kind':'read_cache','cache_id':'v000'}) if n<=21 else
                    json.dumps({'kind':'run_sql','sql':self.f.sql}) if n==22 else
                    json.dumps({'kind':'answer','text':'7'}))
                raw = {'model':'deepseek-flash','transport_kind':'SCRIPTED_NOT_MODEL',
                    'usage':{'prompt_tokens':self.counter.chat(payload['messages']),
                             'completion_tokens':self.counter.text(content)},
                    'choices':[{'message':{'content':content},'finish_reason':'stop'}]}
                return raw if response is None else response(raw)
            return transport
        with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No network')):
            return run_batch(self.f.plan,self.f.caches,expected_plan_sha=audit.digest(self.f.plan),
                database=self.f.db,output=self.f.root/'run',counter=self.counter,
                transport_factory=factory,deadline=1800,mode='SCRIPTED_NOT_MODEL',clock=clock)

    def test_full_twelve_with_readonly_recovery_and_seals(self):
        before = audit.hash_file(self.f.db)
        result = self.run_script()
        self.assertEqual(result['status'],'SCRIPTED_NOT_MODEL_FINISHED')
        self.assertEqual(result['dispatch_attempts'],276)
        self.assertEqual(result['model_calls'],0)
        self.assertEqual(sum(r['tools']['sql_calls'] for r in result['rows']),12)
        self.assertTrue(all(r['status']=='ANSWERED' for r in result['rows']))
        self.assertEqual(audit.hash_file(self.f.db),before)
        verify(self.f.root/'run/batch')
        with self.assertRaises(FileExistsError): self.run_script()

    def test_provider_error_preserves_denominator_and_raw(self):
        result = self.run_script(lambda raw:dict(raw,model='wrong'))
        self.assertEqual(result['dispatch_attempts'],1)
        self.assertEqual(sum(r['status']=='NOT_STARTED' for r in result['rows']),11)
        self.assertTrue((self.f.root/'run/h200a-p1/trace/api-g001.provider.json').exists())
        verify(self.f.root/'run/batch')

    def test_invalid_output_stays_in_all_twelve_rows(self):
        def invalid(raw): raw['choices'][0]['message']['content']='not-json'; return raw
        result = self.run_script(invalid)
        self.assertEqual(result['dispatch_attempts'],12)
        self.assertEqual([r['status'] for r in result['rows']],['INVALID_CONTRACT']*12)
        self.assertEqual(result['semantic_judgments'],0)

    def test_global_deadline_preserves_late_response(self):
        now=[0]
        def late(raw): now[0]=1801; return raw
        result=self.run_script(late,clock=lambda:now[0])
        self.assertEqual(result['stop_reason'],'TimeoutError')
        self.assertEqual(result['dispatch_attempts'],1)
        self.assertEqual(sum(r['status']=='NOT_STARTED' for r in result['rows']),11)


if __name__ == '__main__': unittest.main()
