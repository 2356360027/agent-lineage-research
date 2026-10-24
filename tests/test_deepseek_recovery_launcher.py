"""Launcher failure/archiving tests. All credentials are explicit dummy fixtures."""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from scripts import run_deepseek_recovery as launcher


class Tests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'runtime/runs'
        self.temp=tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'prepared'; self.root.mkdir()
        (self.root/'source').mkdir()
        (self.root/'preflight_result.json').write_text('{"status":"FROZEN_PREFLIGHT_PASSED"}')

    def test_no_approval_does_not_read_secret_or_launch(self):
        with patch.object(launcher.getpass,'getpass') as secret:
            with self.assertRaises(ValueError): launcher.execute(self.root,'fake',None)
            secret.assert_not_called()

    def test_mocked_execution_archives_without_key_and_cannot_repeat(self):
        manifest={'price_checked_date':datetime.now().date().isoformat()}
        with patch.object(launcher,'verify_prepared',return_value=(manifest,None,None,None)), \
             patch.object(launcher.getpass,'getpass',return_value='FAKE_TEST_SECRET') as secret, \
             patch.object(launcher,'communicate_owned',return_value={'status':'FIXTURE_ONLY'}) as owned:
            result=launcher.execute(self.root,'fake',8)
            self.assertEqual(result['status'],'FIXTURE_ONLY')
            command, packet=owned.call_args.args
            self.assertNotIn('FAKE_TEST_SECRET',str(command))
            self.assertEqual(json.loads(packet)['key'],'FAKE_TEST_SECRET')
            for file in self.root.rglob('*.json'):
                self.assertNotIn('FAKE_TEST_SECRET',file.read_text())
            self.assertTrue(Path(result['archive']).exists())
            receipt=json.loads(self.root.with_name(self.root.name+'.archive-receipt.json').read_text())
            self.assertTrue(receipt['archive_member_hashes_verified'])
            with self.assertRaises(ValueError): launcher.execute(self.root,'fake',8)
            self.assertEqual(secret.call_count,1)
            self.assertEqual(owned.call_count,1)

    def test_incomplete_archive_is_not_reported_as_verified(self):
        (self.root/'completed_file_hashes.json').write_text('{}')
        archive=self.root.with_name('incomplete.tar.gz')
        with tarfile.open(archive,'x:gz'): pass
        with self.assertRaises(ValueError): launcher.verify_archive(self.root,archive)

    def test_stale_price_stops_before_key(self):
        with patch.object(launcher,'verify_prepared',return_value=({'price_checked_date':'2000-01-01'},None,None,None)), \
             patch.object(launcher.getpass,'getpass') as secret:
            with self.assertRaises(ValueError): launcher.execute(self.root,'fake',8)
            secret.assert_not_called()

    def test_real_owned_worker_timeout_exits(self):
        result=launcher.communicate_owned([sys.executable,'-c','import time;time.sleep(5)'],
                                         b'',cwd=self.root,timeout=0.1)
        self.assertEqual(result['status'],'WORKER_TREE_TIMEOUT')
        self.assertIsNotNone(result['worker_returncode'])
        self.assertFalse(result['server_cancellation_verified'])

    def test_keyboard_interrupt_cleans_up_only_owned_process(self):
        process = MagicMock()
        process.pid = 123456
        process.communicate.side_effect = KeyboardInterrupt
        process.returncode = -1
        with patch.object(launcher.subprocess, 'Popen', return_value=process), \
             patch.object(launcher.subprocess, 'run') as taskkill, \
             patch.object(launcher.os, 'killpg', create=True) as killpg:
            result = launcher.communicate_owned(['fixture-only'], b'', cwd=self.root, timeout=1)
        self.assertEqual(result['status'], 'WORKER_TREE_INTERRUPTED')
        self.assertTrue(result['billing_unknown'])
        self.assertFalse(result['automatic_retry'])
        process.wait.assert_called_once_with(timeout=15)
        if launcher.os.name == 'nt':
            self.assertEqual(taskkill.call_args.args[0],
                             ['taskkill', '/PID', '123456', '/T', '/F'])
        else:
            killpg.assert_called_once_with(123456, launcher.signal.SIGKILL)


if __name__=='__main__': unittest.main()
