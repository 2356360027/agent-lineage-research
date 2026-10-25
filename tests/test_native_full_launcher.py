"""Launcher authorization/order/ownership tests, no live credentials or HTTP."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import run_native_full_trajectory as launcher
from runtime import bird_readonly_audit as audit

DUMMY='fixture-only-not-real'


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.sha='a'*64

    def patches(self):
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(launcher,'frozen'))
        stack.enter_context(patch.object(launcher,'verify',return_value=({},None,None,None,None)))
        stack.enter_context(patch.object(launcher,'day',return_value=launcher.PRICE_DATE))
        return stack

    def test_no_approval_never_requests_key(self):
        with patch.object(launcher.getpass,'getpass') as key:
            for value in (None,True,0,.10,.71):
                with self.assertRaises(ValueError):launcher.execute(self.root,self.sha,value)
            key.assert_not_called()

    def test_wrong_day_or_previous_claim_prevents_hidden_prompt(self):
        stack=self.patches()
        key=stack.enter_context(patch.object(launcher.getpass,'getpass'))
        with patch.object(launcher,'day',return_value='1900-01-01'):
            with self.assertRaises(ValueError):launcher.execute(self.root,self.sha,.70)
        audit.save_new(self.root/'execution_claim.json',{})
        with self.assertRaises(ValueError):launcher.execute(self.root,self.sha,.70)
        key.assert_not_called()

    def test_frozen_source_check_precedes_key(self):
        with patch.object(launcher.getpass,'getpass') as key:
            with self.assertRaises(ValueError):launcher.execute(self.root,self.sha,.70)
            key.assert_not_called()

    def test_owned_worker_key_stdin_and_closed_archive_receipt(self):
        stack=self.patches()
        stack.enter_context(patch.object(launcher.getpass,'getpass',return_value=DUMMY))
        experiment=self.root/'experiment'
        def owned(argv,packet,cwd,timeout):
            self.assertNotIn(DUMMY,repr(argv));self.assertEqual(cwd,(self.root/'source').resolve())
            self.assertEqual(timeout,630);self.assertEqual(json.loads(packet)['key'],DUMMY)
            self.assertEqual(json.loads(packet)['sha'],self.sha)
            experiment.mkdir();audit.save_new(experiment/'artifact_hashes.json',{})
            return dict(status=launcher.MODE+'_FINISHED',dispatch_attempts=8)
        mock=stack.enter_context(patch.object(launcher,'owned_worker',side_effect=owned))
        result=launcher.execute(self.root,self.sha,.70)
        self.assertEqual(result['dispatch_attempts'],8);mock.assert_called_once()
        receipt=launcher.read(self.root/'archive_receipt.json')
        self.assertEqual(receipt['archive_sha256'],audit.hash_file(experiment/'artifact_hashes.json'))
        for path in self.root.rglob('*.json'):
            self.assertNotIn(DUMMY,path.read_text('utf-8'))

    def test_worker_timeout_keeps_claim_and_no_fake_archive(self):
        stack=self.patches();stack.enter_context(patch.object(launcher.getpass,'getpass',return_value=DUMMY))
        owned=stack.enter_context(patch.object(launcher,'owned_worker',return_value={'status':'WORKER_TIMEOUT'}))
        result=launcher.execute(self.root,self.sha,.70)
        self.assertEqual(result['status'],'WORKER_TIMEOUT')
        self.assertTrue((self.root/'execution_claim.json').exists())
        self.assertFalse((self.root/'archive_receipt.json').exists())
        with self.assertRaises(ValueError):launcher.execute(self.root,self.sha,.70)
        owned.assert_called_once()

    def test_launch_exception_records_type_not_secret(self):
        stack=self.patches();stack.enter_context(patch.object(launcher.getpass,'getpass',return_value=DUMMY))
        stack.enter_context(patch.object(launcher,'owned_worker',side_effect=RuntimeError(DUMMY)))
        with self.assertRaises(RuntimeError):launcher.execute(self.root,self.sha,.70)
        failure=(self.root/'launch_failure.json').read_text('utf-8')
        self.assertNotIn(DUMMY,failure);self.assertIn('RuntimeError',failure)

    def test_worker_rechecks_claim_before_model_or_sql(self):
        self.patches()
        audit.save_new(self.root/'execution_claim.json',dict(manifest_sha256=self.sha,approved_cny=.10,automatic_retry=False))
        with patch.object(launcher.batch,'run') as run:
            with self.assertRaises(ValueError):launcher.worker(dict(prepared=str(self.root),sha=self.sha,key=DUMMY))
            run.assert_not_called()

    def test_unclosed_parent_outcome_cannot_be_audited_as_complete(self):
        self.patches()
        audit.save_new(self.root/'execution_claim.json',dict(manifest_sha256=self.sha,approved_cny=.70,automatic_retry=False))
        audit.save_new(self.root/'launch_outcome.json',dict(status='WORKER_TIMEOUT'))
        with patch.object(launcher,'inspect') as inspect:
            with self.assertRaises(ValueError):launcher.analyze(self.root,self.sha)
            inspect.assert_not_called()
        self.assertFalse((self.root/'analysis').exists())


if __name__=='__main__':unittest.main()
