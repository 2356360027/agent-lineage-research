"""Archive/gate fixtures only. No official outputs, human labels or API calls."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts import audit_cache_action as script


class AuditCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name)
        self.prepared=self.root/'prepared'; self.prepared.mkdir()
        (self.prepared/'source').mkdir()
        self.write(self.prepared/'manifest.json',{'source_files':{}})
        self.sha=script.sha(self.prepared/'manifest.json')

    def write(self,path,data):
        path.write_text(json.dumps(data),encoding='utf-8')

    def sealed(self,status='OFFICIAL_DEVELOPMENT_FINISHED',stop=None):
        exp=self.prepared/'experiment'; exp.mkdir()
        self.write(exp/'summary.json',{'status':status,'stop_reason':stop})
        hashes=script.snapshot(exp)
        self.write(exp/'artifact_hashes.json',hashes)
        self.write(self.prepared/'execution_claim.json',dict(manifest_sha256=self.sha,
                    approved_cny=.50,automatic_retry=False))

    def test_no_seal_prevents_effect_inspection(self):
        with self.assertRaisesRegex(ValueError,'No completed seal'):
            script.precheck(self.prepared,self.sha)

    def test_wrong_identity_and_mutated_source_refused(self):
        with self.assertRaisesRegex(ValueError,'Wrong prepared'):
            script.precheck(self.prepared,'0'*64)
        (self.prepared/'source/unexpected.py').write_text('# fixture',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Frozen source'):
            script.precheck(self.prepared,self.sha)

    def test_stopped_and_unclaimed_batches_not_promoted(self):
        self.sealed('OFFICIAL_DEVELOPMENT_STOPPED','fixture failure')
        with self.assertRaisesRegex(ValueError,'Not a finished official'):
            script.precheck(self.prepared,self.sha)

    def test_manifest_and_claim_binding(self):
        self.sealed(); script.precheck(self.prepared,self.sha)
        self.write(self.prepared/'execution_claim.json',{})
        with self.assertRaisesRegex(ValueError,'Execution claim'):
            script.precheck(self.prepared,self.sha)

    def test_archive_is_exact_and_manifest_does_not_hash_itself(self):
        self.sealed(); before=script.snapshot(self.prepared); output=self.root/'archive'
        result=script.archive(self.prepared,output,{'status':'TEST_FIXTURE_NOT_RESEARCH'},before)
        self.assertFalse(result['backup']['off_device'])
        self.assertEqual(result['backup']['verified_files'],len(before))
        with zipfile.ZipFile(output/'prepared-and-results.zip') as z:
            self.assertEqual(set(z.namelist()),set(before))
        hashes=script.read(output/'artifact_hashes.json')
        self.assertNotIn('artifact_hashes.json',hashes)
        self.assertEqual(hashes,{name:script.sha(output/name) for name in hashes})
        with self.assertRaisesRegex(ValueError,'Never overwrite'):
            script.archive(self.prepared,output,{},before)

    def test_changed_input_and_nested_output_do_not_archive(self):
        before=script.snapshot(self.prepared)
        with self.assertRaisesRegex(ValueError,'outside'):
            script.archive(self.prepared,self.prepared/'archive',{},before)
        (self.prepared/'extra.txt').write_text('fixture',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'changed before'):
            script.archive(self.prepared,self.root/'archive',{},before)
        self.assertFalse((self.root/'archive').exists())


if __name__=='__main__': unittest.main()
