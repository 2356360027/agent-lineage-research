import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.cache_documentation_inputs import build
from runtime import cache_documentation_runner as runner
from scripts import run_cache_documentation as launcher
from tests.test_cache_documentation_inputs import fixture


class Counter:
    def chat(self, messages): return 100
    def text(self, text): return 10


def response(text='{"kind":"read_cache","cache_id":"v000"}', n=100, finish='stop'):
    return dict(model='deepseek-flash', usage=dict(prompt_tokens=n,completion_tokens=10),
                choices=[dict(finish_reason=finish,message=dict(content=text))])


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(self.tmp.cleanup)
        self.output=Path(self.tmp.name)/'out'; self.plan=build(fixture())

    def fake(self, transport, **kwargs):
        with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No network in tests')):
            return runner.run(self.plan,counter=Counter(),transport_factory=lambda ident,log:transport,
                              output=self.output,mode='SCRIPTED_NOT_MODEL',**kwargs)

    def test_twelve_calls_no_tools_and_no_overwrite(self):
        r=self.fake(lambda p,t:response())
        self.assertEqual(r['dispatch_attempts'],12)
        self.assertEqual([x['status'] for x in r['rows']], ['ACTION_RECORDED']*12)
        self.assertEqual(r['tool_executions'],0); self.assertEqual(r['model_calls'],0)
        self.assertEqual(r['cost_guard']['accounted_micro_cny'],3360)
        with self.assertRaises(FileExistsError): self.fake(lambda p,t:response())

    def test_malformed_output_retained_never_repaired(self):
        raw='{"kind":"run_sql","sql":"SELECT 1"}' + "'}"
        r=self.fake(lambda p,t:response(raw))
        self.assertEqual(r['dispatch_attempts'],12)
        self.assertTrue(all(x['status']=='INVALID_ACTION' for x in r['rows']))
        saved=json.loads(next(self.output.glob('*/action.provider.json')).read_text('utf-8'))
        self.assertEqual(saved['raw']['choices'][0]['message']['content'],raw)

    def test_failure_retains_reserve_and_pending_slots(self):
        def fail(p,t): raise RuntimeError('fixture')
        r=self.fake(fail)
        self.assertEqual(r['dispatch_attempts'],1)
        self.assertEqual(sum(x['status']=='NOT_STARTED' for x in r['rows']),11)
        self.assertGreater(r['cost_guard']['uncertain_reserved_micro_cny'],0)

    def test_interrupt_stops_without_retry(self):
        def fail(p,t): raise KeyboardInterrupt()
        r=self.fake(fail)
        self.assertEqual(r['stop_reason'],'KeyboardInterrupt')
        self.assertEqual(r['dispatch_attempts'],1)

    def test_late_response_saved_before_stop(self):
        ticks=[0]
        def late(p,t): ticks[0]=601; return response()
        r=self.fake(late,clock=lambda:ticks[0])
        self.assertEqual(r['stop_reason'],'TimeoutError')
        self.assertEqual(r['dispatch_attempts'],1)
        self.assertEqual(len(list(self.output.glob('*/action.provider.json'))),1)

    def test_provider_token_mismatch_stops(self):
        r=self.fake(lambda p,t:response(n=101))
        self.assertEqual(r['stop_reason'],'ValueError')
        self.assertEqual(r['dispatch_attempts'],1)

    def test_expired_before_dispatch(self):
        times=iter([0,601])
        r=self.fake(lambda p,t:self.fail('must not dispatch'),clock=lambda:next(times))
        self.assertEqual(r['dispatch_attempts'],0)
        self.assertEqual(r['stop_reason'],'TimeoutError')

    def test_wrong_approval_no_secret_prompt(self):
        with patch.object(launcher.getpass,'getpass') as prompt:
            with self.assertRaises(ValueError): launcher.execute(self.output,'0'*64,None)
            prompt.assert_not_called()

    def test_archive_extra_file_fails(self):
        root=Path(self.tmp.name)/'sealed'; root.mkdir()
        f=root/'artifact_hashes.json'; f.write_text('{}',encoding='utf-8')
        sha=launcher.audit.hash_file(f)
        launcher.verify_archive(root,sha)
        (root/'extra').write_text('fixture',encoding='utf-8')
        with self.assertRaises(ValueError): launcher.verify_archive(root,sha)
