"""Offline launcher gates. No credentials, external API or research SQL."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import run_cache_action as launcher
from runtime import bird_readonly_audit as audit


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name)

    def seal(self,entries):
        audit.save_new(self.root/'artifact_hashes.json',entries)
        return audit.hash_file(self.root/'artifact_hashes.json')

    def test_archive_roundtrip_extra_and_altered_file_rejected(self):
        audit.save_new(self.root/'item.json',{'fixture':True})
        sha=self.seal({'item.json':audit.hash_file(self.root/'item.json')})
        launcher.verify_archive(self.root,sha)
        (self.root/'extra.txt').write_text('fixture')
        with self.assertRaises(ValueError): launcher.verify_archive(self.root,sha)

    def test_unsafe_archive_path_rejected(self):
        sha=self.seal({'../outside':'0'*64})
        with self.assertRaises(ValueError): launcher.verify_archive(self.root,sha)

    def test_changed_seal_rejected(self):
        self.seal({'item.json':'0'*64})
        with self.assertRaises(ValueError): launcher.verify_archive(self.root,'0'*64)

    def test_wrong_approval_never_prompts_for_key(self):
        for amount in (None,True,0.10,0.30,1,'0.50'):
            with patch.object(launcher.getpass,'getpass') as prompt:
                with self.assertRaises(ValueError): launcher.execute(self.root,'0'*64,amount)
                prompt.assert_not_called()

    def test_mutable_launcher_never_prompts_even_with_amount(self):
        with patch.object(launcher.getpass,'getpass') as prompt:
            with self.assertRaisesRegex(ValueError,'frozen'): launcher.execute(self.root,'0'*64,.50)
            prompt.assert_not_called()

    def test_existing_preparation_rejected_before_other_reads(self):
        with self.assertRaisesRegex(ValueError,'overwrite'): launcher.prepare(self.root,self.root)

    def test_readonly_worker_disables_bytecode_and_uses_own_cwd(self):
        reply=SimpleNamespace(returncode=0,stdout=json.dumps({'status':'EXECUTED'}).encode(),stderr=b'')
        with patch.object(audit.subprocess,'run',return_value=reply) as call:
            audit.run_child(self.root/'fixture.sqlite','SELECT 1')
        args,kwargs=call.call_args
        self.assertIn('-B',args[0])
        self.assertEqual(kwargs['cwd'],Path(audit.__file__).resolve().parents[1])
        self.assertIn('creationflags',kwargs)

    def test_frozen_policy_does_not_invent_labels_or_C_drift(self):
        policy=launcher.read(Path(__file__).resolve().parents[1]/launcher.POLICY)
        self.assertEqual(policy['new_human_labels'],0)
        self.assertEqual(policy['allowed_ratings'],['MATCH','MISMATCH','UNCERTAIN'])
        self.assertIn('do not assign C-type',policy['trace_readout']['unelicited_intent'])


if __name__=='__main__': unittest.main()
