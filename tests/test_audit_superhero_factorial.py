"""Archive and gate fixtures only. Never official/model research results."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts import audit_superhero_factorial as script


class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(temp.cleanup); self.root=Path(temp.name)
        self.prepared=self.root/'prepared'; self.prepared.mkdir(); (self.prepared/'source').mkdir()
        self.reference=self.root/'reference'; self.reference.mkdir()
        self.write(self.reference/'fixture.json',{'not_research':True})
        self.write(self.prepared/'manifest.json',{'source_files':{}})
        self.sha=script.sha(self.prepared/'manifest.json')

    def write(self,path,data): path.write_text(json.dumps(data),encoding='utf-8')

    def sealed(self,status='OFFICIAL_DEVELOPMENT_FINISHED',stop=None):
        exp=self.prepared/'experiment'; exp.mkdir()
        self.write(exp/'summary.json',dict(status=status,stop_reason=stop,dispatch_attempts=4))
        self.write(exp/'artifact_hashes.json',script.snapshot(exp))
        self.write(self.prepared/'execution_claim.json',dict(manifest_sha256=self.sha,approved_cny=.20,automatic_retry=False))
        self.write(self.prepared/'launch_outcome.json',dict(status=status,dispatch_attempts=4))

    def result(self):
        project=Path(__file__).resolve().parents[1]
        return dict(status='SCRIPTED_ARCHIVE_FIXTURE_ONLY',analysis_source_files={n:script.sha(project/n) for n in script.ANALYSIS})

    def test_unsealed_or_altered_source_refused(self):
        with self.assertRaisesRegex(ValueError,'No completed seal'): script.precheck(self.prepared,self.sha)
        (self.prepared/'source/extra.py').write_text('# fixture')
        with self.assertRaisesRegex(ValueError,'Frozen source'): script.precheck(self.prepared,self.sha)

    def test_stopped_batch_never_promoted(self):
        self.sealed('OFFICIAL_DEVELOPMENT_STOPPED','failure')
        with self.assertRaisesRegex(ValueError,'Not a finished'): script.precheck(self.prepared,self.sha)

    def test_parent_outcome_and_claim_must_match(self):
        self.sealed(); script.precheck(self.prepared,self.sha)
        self.write(self.prepared/'launch_outcome.json',dict(status='WORKER_TIMEOUT'))
        with self.assertRaisesRegex(ValueError,'Parent completion'): script.precheck(self.prepared,self.sha)
        self.write(self.prepared/'execution_claim.json',dict(approved_cny=8))
        with self.assertRaisesRegex(ValueError,'Execution claim'): script.precheck(self.prepared,self.sha)

    def test_exact_archive_contains_inputs_reference_and_analysis(self):
        self.sealed(); before=script.snapshot(self.prepared); ref=script.snapshot(self.reference)
        output=self.root/'archive'; result=self.result()
        saved=script.archive(self.prepared,self.reference,output,result,before,ref)
        expected={'prepared/'+n for n in before}|{'reference/'+n for n in ref}|{'analysis-source/'+n for n in script.ANALYSIS}
        with zipfile.ZipFile(output/'prepared-results-and-reference.zip') as z:
            self.assertEqual(set(z.namelist()),expected)
        self.assertEqual(saved['backup']['verified_files'],len(expected))
        self.assertFalse(saved['backup']['off_device'])
        self.assertFalse(saved['backup']['external_dataset_and_tokenizer_included'])
        hashes=script.read(output/'artifact_hashes.json')
        self.assertNotIn('artifact_hashes.json',hashes)
        self.assertEqual(hashes,{n:script.sha(output/n) for n in hashes})
        with self.assertRaisesRegex(ValueError,'Never overwrite'):
            script.archive(self.prepared,self.reference,output,result,before,ref)

    def test_changed_or_nested_inputs_do_not_archive(self):
        before=script.snapshot(self.prepared); ref=script.snapshot(self.reference); result=self.result()
        for target in (self.prepared/'archive',self.reference/'archive'):
            with self.assertRaisesRegex(ValueError,'outside'):
                script.archive(self.prepared,self.reference,target,result,before,ref)
        self.write(self.reference/'fixture.json',{'changed':True})
        with self.assertRaisesRegex(ValueError,'changed before'):
            script.archive(self.prepared,self.reference,self.root/'out',result,before,ref)
        self.assertFalse((self.root/'out').exists())

    def test_unexpected_analysis_source_or_changed_hash_refused(self):
        before=script.snapshot(self.prepared); ref=script.snapshot(self.reference); result=self.result()
        result['analysis_source_files']['../elsewhere']='0'*64
        with self.assertRaisesRegex(ValueError,'source set'):
            script.archive(self.prepared,self.reference,self.root/'out',result,before,ref)
        result=self.result(); result['analysis_source_files'][script.ANALYSIS[0]]='0'*64
        with self.assertRaisesRegex(ValueError,'source changed'):
            script.archive(self.prepared,self.reference,self.root/'out',result,before,ref)
        self.assertFalse((self.root/'out').exists())


if __name__=='__main__': unittest.main()
