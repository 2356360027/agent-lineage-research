"""No API calls. Fake owned-worker outcomes test launch authorization guards."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_student_club_diagnostic as launch

KEY='synthetic-test-value-not-a-credential'


class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='student-launch-',dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(temp.cleanup);self.root=Path(temp.name);self.sha='a'*64
        for name in ('preflight','day','getpass.getpass','owned_worker'):
            p=patch('scripts.run_student_club_diagnostic.'+name)
            obj=p.start();self.addCleanup(p.stop)
            setattr(self,name.split('.')[-1],obj)
        self.day.return_value=launch.PRICE_DATE
        self.getpass.return_value=KEY
        self.owned_worker.return_value=dict(status='WORKER_TIMEOUT',automatic_retry=False,billing_unknown=True)

    def test_exact_approval_date_and_preflight_required_before_key(self):
        for value in (None,True,0.70,2):
            with self.assertRaises(ValueError):launch.execute(self.root,self.sha,value)
        self.getpass.assert_not_called();self.owned_worker.assert_not_called()
        self.day.return_value='2000-01-01'
        with self.assertRaises(ValueError):launch.execute(self.root,self.sha,1.10)
        self.getpass.assert_not_called()
        self.day.return_value=launch.PRICE_DATE;self.preflight.side_effect=ValueError('modified source')
        with self.assertRaises(ValueError):launch.execute(self.root,self.sha,1.10)
        self.getpass.assert_not_called()

    def test_timeout_preserved_credential_only_stdin_no_restart(self):
        result=launch.execute(self.root,self.sha,1.10)
        self.assertEqual(result['status'],'WORKER_TIMEOUT')
        command,packet,cwd=self.owned_worker.call_args.args
        self.assertNotIn(KEY,str(command));self.assertEqual(json.loads(packet)['key'],KEY)
        self.assertEqual(cwd,self.root/'source')
        self.assertEqual(self.owned_worker.call_args.kwargs['timeout'],630)
        for p in self.root.glob('*.json'):self.assertNotIn(KEY,p.read_text('utf-8'))
        self.assertFalse((self.root/'archive_receipt.json').exists())
        with self.assertRaises(ValueError):launch.execute(self.root,self.sha,1.10)
        self.assertEqual(self.owned_worker.call_count,1)

    def test_success_requires_matching_worker_summary_and_retains_receipt(self):
        exp=self.root/'experiment'
        def worker(*args,**kwargs):
            exp.mkdir()
            value=dict(status=launch.MODE+'_FINISHED',dispatch_attempts=19)
            (exp/'summary.json').write_text(json.dumps(value),encoding='utf-8')
            (exp/'artifact_hashes.json').write_text('{}',encoding='utf-8')
            return value
        self.owned_worker.side_effect=worker
        launch.execute(self.root,self.sha,1.10)
        receipt=json.loads((self.root/'archive_receipt.json').read_text('utf-8'))
        self.assertFalse(receipt['semantic_review_complete'])
        self.assertFalse(receipt['full_trace_audit_complete'])
        self.assertTrue(receipt['worker_returned'])

    def test_launcher_exception_retains_claim_and_failure_without_secret(self):
        self.owned_worker.side_effect=OSError(KEY)
        with self.assertRaises(OSError):launch.execute(self.root,self.sha,1.10)
        failure=json.loads((self.root/'launch_failure.json').read_text('utf-8'))
        self.assertEqual(failure,{'error_type':'OSError','automatic_retry':False})
        self.assertTrue((self.root/'execution_claim.json').exists())
        self.assertFalse((self.root/'archive_receipt.json').exists())

    def test_worker_rejects_missing_claim_and_wrong_fields_before_batch(self):
        with patch.object(launch,'run') as run:
            with self.assertRaises(ValueError):launch.worker({'key':KEY})
            with self.assertRaises(FileNotFoundError):launch.worker(dict(prepared=str(self.root),sha=self.sha,key=KEY))
            run.assert_not_called()

    def test_claim_exactness_and_unfrozen_entrypoint(self):
        good=dict(manifest_sha256=self.sha,approved_cny=1.10,utc='2026-10-26T00:00:00+00:00',automatic_retry=False)
        launch.validate_claim(good,self.sha)
        for change in ({'approved_cny':2},{'automatic_retry':True},{'utc':'2026-10-26T00:00:00'},{'extra':1}):
            with self.assertRaises(ValueError):launch.validate_claim(good|change,self.sha)


if __name__=='__main__':unittest.main()
