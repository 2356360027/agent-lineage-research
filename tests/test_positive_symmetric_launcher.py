"""Offline authorization/launch boundaries, no actual API credentials/calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import run_positive_symmetric as launch


class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)

    def test_wrong_amount_or_mutable_launcher_before_key(self):
        for amount in (None,True,.19,.30,'0.20',.20):
            with patch.object(launch.getpass,'getpass') as prompt:
                with self.assertRaises(ValueError):launch.execute(self.root,'0'*64,amount)
                prompt.assert_not_called()

    def test_existing_preparation_never_overwritten(self):
        with self.assertRaisesRegex(ValueError,'overwrite'):launch.prepare(self.root,self.root)

    def test_stale_price_and_existing_claim_before_key(self):
        with patch.object(launch,'frozen'),patch.object(launch,'verify'),patch.object(launch.getpass,'getpass') as prompt:
            with patch.object(launch,'day',return_value='2000-01-01'):
                with self.assertRaisesRegex(ValueError,'prices'):launch.execute(self.root,'0'*64,.20)
            (self.root/'execution_claim.json').write_text('{}')
            with patch.object(launch,'day',return_value=launch.PRICE_DATE):
                with self.assertRaisesRegex(ValueError,'claimed'):launch.execute(self.root,'0'*64,.20)
            prompt.assert_not_called()

    def test_unfrozen_worker_before_input_or_transport(self):
        with patch.object(launch,'verify') as verify:
            with self.assertRaisesRegex(ValueError,'frozen'):
                launch.worker(dict(prepared=str(self.root),sha='0'*64,key='unused-fixture'))
            verify.assert_not_called()

    def test_changed_manifest_before_input(self):
        (self.root/'manifest.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'Manifest'):launch.verify(self.root,'0'*64)

    def test_freeze_includes_analysis_and_task_policy(self):
        self.assertEqual(len(launch.FILES),len(set(launch.FILES)))
        for suffix in ('design','tools','loop','capacity','adapter','batch','readout','replay','batch_audit'):
            self.assertIn('runtime/cache_route_symmetric_'+suffix+'.py',launch.FILES)
        for name in (launch.POLICY,launch.ADMISSION,launch.SELF):self.assertIn(name,launch.FILES)

    def test_secret_only_in_worker_stdin_not_persistent_records(self):
        secret='synthetic-fixture-not-an-api-key'
        outcome=dict(status='OFFICIAL_DEVELOPMENT_FINISHED',dispatch_attempts=4)
        with patch.object(launch,'frozen'),patch.object(launch,'verify'),\
                patch.object(launch,'day',return_value=launch.PRICE_DATE),\
                patch.object(launch.getpass,'getpass',return_value=secret),\
                patch.object(launch,'owned_worker',return_value=outcome) as owned:
            result=launch.execute(self.root,'a'*64,.20)
        command,packet,cwd=owned.call_args.args
        self.assertNotIn(secret,' '.join(command))
        self.assertEqual(json.loads(packet)['key'],secret)
        for path in self.root.rglob('*'):
            if path.is_file():self.assertNotIn(secret.encode(),path.read_bytes())
        self.assertEqual(result['dispatch_attempts'],4)

    def test_hard_timeout_cannot_be_graded_or_restarted(self):
        (self.root/'execution_claim.json').write_text(json.dumps(dict(manifest_sha256='a'*64,approved_cny=.20,automatic_retry=False)))
        (self.root/'launch_outcome.json').write_text('{"status":"WORKER_TIMEOUT"}')
        with patch.object(launch,'frozen'),patch.object(launch,'verify',return_value=({},None,None,None)),\
                patch.object(launch,'finished_batch') as scorer,patch.object(launch,'run') as run:
            with self.assertRaisesRegex(ValueError,'normal worker closure'):launch.analyze(self.root,'a'*64)
            scorer.assert_not_called();run.assert_not_called()
        self.assertFalse((self.root/'analysis').exists())


if __name__=='__main__':unittest.main()
