"""Offline launcher/worker isolation tests. No provider calls or credentials."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import run_superhero_factorial as launch


class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(temp.cleanup); self.root=Path(temp.name)

    def test_wrong_approval_or_mutable_launcher_never_prompts(self):
        for amount in (None,True,.10,.30,1,'0.20',.20):
            with patch.object(launch.getpass,'getpass') as prompt:
                with self.assertRaises(ValueError): launch.execute(self.root,'0'*64,amount)
                prompt.assert_not_called()

    def test_existing_preparation_not_overwritten(self):
        with self.assertRaisesRegex(ValueError,'overwrite'): launch.prepare(self.root,self.root)

    def test_unfrozen_worker_rejected_before_verification(self):
        with patch.object(launch,'verify') as verify:
            with self.assertRaisesRegex(ValueError,'Unfrozen'):
                launch.worker(dict(prepared=str(self.root),sha='0'*64,key='unused-fixture'))
            verify.assert_not_called()

    def test_changed_manifest_refused_before_loading_inputs(self):
        (self.root/'manifest.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'Manifest'): launch.verify(self.root,'0'*64)

    def test_source_inventory_unique_and_contains_all_new_runtime_components(self):
        self.assertEqual(len(launch.FILES),len(set(launch.FILES)))
        for name in ('batch','loop','capacity','preview'):
            self.assertIn('runtime/cache_action_factorial_'+name+'.py',launch.FILES)
        self.assertIn('runtime/deepseek_official_transport.py',launch.FILES)

    def test_stale_date_existing_claim_rejected_before_key(self):
        fake=self.root/'source/scripts/run_superhero_factorial.py'
        with patch.object(launch,'__file__',str(fake)),patch.object(launch,'verify'),patch.object(launch,'check_imports'),patch.object(launch.getpass,'getpass') as prompt:
            with patch.object(launch,'day',return_value='2000-01-01'):
                with self.assertRaisesRegex(ValueError,'pricing'): launch.execute(self.root,'0'*64,.20)
            (self.root/'execution_claim.json').write_text('{}')
            with patch.object(launch,'day',return_value=launch.PRICE_DATE):
                with self.assertRaisesRegex(ValueError,'claimed'): launch.execute(self.root,'0'*64,.20)
            prompt.assert_not_called()

    def test_worker_summary_allowlist_suppresses_unexpected_stdout(self):
        fake=SimpleNamespace(returncode=0,communicate=lambda *a,**k:(b'{"extra":"not allowed"}',b''))
        with patch.object(launch.subprocess,'Popen',return_value=fake):
            result=launch.owned_worker(['fixture'],b'{}',self.root)
        self.assertEqual(result['status'],'INVALID_WORKER_SUMMARY')
        self.assertNotIn('extra',json.dumps(result))

    def test_owned_clean_child_executes_without_inheriting_pythonpath(self):
        code='import json,os; print(json.dumps({"status":"PYTHONPATH" in os.environ,"dispatch_attempts":0}))'
        with patch.dict(launch.os.environ,{'PYTHONPATH':'fixture-not-importable'}):
            result=launch.owned_worker([sys.executable,'-B','-c',code],b'{}',self.root,timeout=5)
        self.assertIs(result['status'],False)

    def test_owned_timeout_kills_only_created_pid_tree(self):
        fake=SimpleNamespace(pid=123456,returncode=-1,
            communicate=lambda *a,**k:(_ for _ in ()).throw(subprocess.TimeoutExpired('fixture',1)),
            wait=lambda **k:None)
        with patch.object(launch.subprocess,'Popen',return_value=fake),patch.object(launch.subprocess,'run') as kill,patch.object(launch.os,'killpg',create=True) as killpg:
            result=launch.owned_worker(['fixture'],b'{}',self.root,timeout=1)
            if launch.os.name=='nt': self.assertEqual(kill.call_args.args[0],['taskkill','/PID','123456','/T','/F'])
            else: self.assertEqual(killpg.call_args.args[0],123456)
        self.assertEqual(result['status'],'WORKER_TIMEOUT')
        self.assertTrue(result['billing_unknown'])


if __name__=='__main__': unittest.main()
