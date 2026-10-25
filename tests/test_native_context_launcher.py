"""Fake transport and credentials only; no paid inference."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from runtime import native_context_runner as batch
from scripts import run_native_context as launch
from tests.test_research_context_interface import requests
from tests.test_native_transport_adapter import provider


class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs',prefix='native-launch-fixture-')
        self.addCleanup(temp.cleanup); self.root=Path(temp.name)
        self.plan=batch.build_plan(requests())
        self.counter=Mock(); self.counter.inspect.return_value={'reference_input_tokens':100}

    def make_run(self, invalid=False, failure=False):
        def factory(ident,log):
            def generate(payload,timeout):
                if failure: raise TimeoutError('fixture')
                raw=provider()
                if invalid: raw['choices'][0]['message']['tool_calls']*=2
                text=json.dumps(raw)
                log('transport-001',dict(transport_status='RESPONSE_RECEIVED',response_text=text))
                return text
            return generate
        result=batch.run(self.plan,counter=self.counter,transport_factory=factory,
                         output=self.root/'experiment',mode='OFFICIAL_NATIVE_CONTEXT',clock=lambda:0)
        launch.audit.save_new(self.root/'execution_claim.json',dict(manifest_sha256='fixture',approved_cny=.10))
        launch.audit.save_new(self.root/'launch_outcome.json',dict(status=result['status'],dispatch_attempts=result['dispatch_reservations']))
        return result

    def analyze(self):
        with patch.object(launch,'frozen'),patch.object(launch,'verify',return_value=({'input_tokens':[100]*8},self.plan,self.counter)):
            return launch.analyze(self.root,'fixture')

    def test_complete_and_invalid_audit_replays_without_repair(self):
        original=self.make_run(invalid=True)
        report=self.analyze()
        self.assertEqual(report['status'],'SEALED_NATIVE_CONTEXT_REPLAY_VERIFIED')
        self.assertEqual(report['readout'],original['readout'])
        self.assertEqual(report['readout']['totals']['invalid'],8)
        self.assertEqual(report['cost_guard']['accounted_micro_cny'],2880)
        with self.assertRaises(FileExistsError): self.analyze()

    def test_valid_replay(self):
        self.make_run()
        self.assertEqual(self.analyze()['readout']['totals']['admitted'],8)

    def test_failure_is_inventory_not_completed_effect(self):
        self.make_run(failure=True)
        report=self.analyze()
        self.assertEqual(report['status'],'STOPPED_INVENTORY_ONLY')
        self.assertFalse(report['effects_computed'])

    def test_resealed_score_tampering_rejected(self):
        self.make_run()
        experiment=self.root/'experiment'
        score=experiment/self.plan['jobs'][0]['id']/'score.json'
        data=launch.read(score); data['admitted']=False
        score.write_text(json.dumps(data),encoding='utf-8')
        # Even after an untrusted party updates the outer hashes, replay rejects it.
        hashes={p.relative_to(experiment).as_posix():launch.audit.hash_file(p)
                for p in experiment.rglob('*') if p.is_file() and p!=experiment/'artifact_hashes.json'}
        (experiment/'artifact_hashes.json').write_text(json.dumps(hashes),encoding='utf-8')
        with self.assertRaises(ValueError): self.analyze()

    def test_wrong_approval_unfrozen_date_and_claim_before_key(self):
        for approved in (None,True,.12,.20,'0.10',.10):
            with patch.object(launch.getpass,'getpass') as key:
                with self.assertRaises(ValueError): launch.execute(self.root,'fixture',approved)
                key.assert_not_called()
        for mode in ('date','claim'):
            if mode=='claim': (self.root/'execution_claim.json').touch()
            with patch.object(launch,'frozen'),patch.object(launch,'verify'),patch.object(launch,'day',return_value='1900-01-01' if mode=='date' else launch.PRICE_DATE),patch.object(launch.getpass,'getpass') as key:
                with self.assertRaises(ValueError): launch.execute(self.root,'fixture',.10)
                key.assert_not_called()

    def test_hidden_key_only_owned_stdin_and_timeout_bound(self):
        with patch.object(launch,'frozen'),patch.object(launch,'verify'),patch.object(launch,'day',return_value=launch.PRICE_DATE),patch.object(launch.getpass,'getpass',return_value='synthetic-test-secret'),patch.object(launch,'owned_worker',return_value=dict(status='WORKER_TIMEOUT')) as worker:
            launch.execute(self.root,'fixture',.10)
        args=worker.call_args.args
        self.assertNotIn('synthetic-test-secret',str(args[0]))
        self.assertEqual(json.loads(args[1])['key'],'synthetic-test-secret')
        self.assertEqual(worker.call_args.kwargs['timeout'],630)
        for p in self.root.rglob('*.json'):
            self.assertNotIn('synthetic-test-secret',p.read_text('utf-8'))

    def test_worker_rejects_missing_claim_before_transport(self):
        with patch.object(launch,'frozen'),patch.object(launch,'verify',return_value=({},self.plan,self.counter)),patch.object(launch,'NativeTransport') as transport:
            with self.assertRaises(FileNotFoundError):
                launch.worker(dict(prepared=str(self.root),sha='fixture',key='synthetic-test-secret'))
            transport.assert_not_called()

    def test_limits_consistent(self):
        self.assertEqual(launch.LIMITS['ceiling_micro_cny'],batch.CEILING)
        self.assertEqual(launch.LIMITS['max_calls'],batch.MAX_CALLS)
        self.assertEqual(launch.LIMITS['max_seconds'],batch.MAX_SECONDS)

    def test_schema_order_stable_across_frozen_serialization(self):
        from runtime.native_action_counter import render_first_request
        reloaded=json.loads(launch.audit.encoded(self.plan))
        for before,after in zip(self.plan['jobs'],reloaded['jobs']):
            self.assertEqual(render_first_request(before['payload'],'fixture preamble',()),
                             render_first_request(after['payload'],'fixture preamble',()))


if __name__=='__main__': unittest.main()
