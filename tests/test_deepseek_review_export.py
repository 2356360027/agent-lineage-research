"""New record format compatibility; all archives and replies here are synthetic."""
import json
from pathlib import Path
import unittest

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_journal import verify
from scripts import prepare_staged_contract_review as review
from scripts import audit_staged_trace_evidence as evidence
from tests import test_prepare_staged_contract_review as old_fixtures
from tests import test_deepseek_recovery_batch as batch_fixtures


class Tests(unittest.TestCase):
    def setUp(self):
        self.fixture=old_fixtures.ReviewTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def new_format(self, mode='OFFICIAL_UNREVIEWED'):
        f=self.fixture
        f.data.pop('batch/backend.json')
        f.data['batch/settings.json']={
            'backend':mode,'max_calls':276,'max_output_tokens':512,
            'independent_targets':2,'automatic_retry':False,'confirmation_study':False}
        f.data['batch/summary.json']['status']=mode+'_STOPPED'

    def test_official_declaration_is_not_remote_attestation_or_a_label(self):
        f=self.fixture; self.new_format(); f.seal()
        receipt=f.prepare()
        self.assertEqual(receipt['source_backend'],'official-deepseek')
        self.assertEqual(receipt['available_texts'],1)
        self.assertEqual(receipt['missing_responses'],11)
        self.assertFalse(receipt['semantic_labels_generated'])
        self.assertFalse(receipt['server_dispatch_or_execution_attested'])
        packet=json.loads((f.root/'review/reviewer-1.json').read_text())
        self.assertFalse(packet['source_is_scripted'])
        for item in packet['items']:
            self.assertTrue(all(v is None for v in item['review'].values()))
            self.assertNotIn('read_price',item)
            self.assertNotIn('later_outcome',item)

    def test_conflicting_backend_formats_rejected(self):
        f=self.fixture; self.new_format()
        f.data['batch/backend.json']={'kind':'native'}
        f.seal()
        with self.assertRaises(ValueError): f.prepare()

    def test_backend_summary_mismatch_rejected(self):
        f=self.fixture; self.new_format()
        f.data['batch/summary.json']['status']='SCRIPTED_NOT_MODEL_FINISHED'
        f.seal()
        with self.assertRaises(ValueError): f.prepare()

    def test_unsealed_batch_cannot_be_exported(self):
        f=self.fixture; self.new_format(); f.seal()
        (f.run/'artifact_hashes.json').unlink()
        with self.assertRaisesRegex(ValueError,'Missing or oversized admission artifact'): f.prepare()
        self.assertFalse((f.root/'review').exists())

    def test_actual_new_orchestrator_archive_keeps_contract_after_later_failure(self):
        f=batch_fixtures.Tests()
        self.addCleanup(f.doCleanups); f.setUp()
        n=[0]
        def fail_second(raw):
            n[0]+=1
            return dict(raw,model='wrong') if n[0]==2 else raw
        result=f.run_script(fail_second)
        self.assertEqual(result['dispatch_attempts'],2)
        self.assertEqual(result['rows'][0]['status'],'FAILED')
        run=f.f.root/'run'; sha=audit.hash_file(run/'artifact_hashes.json')
        args={'manifest_sha':sha,'plan_sha':audit.digest(f.f.plan)}
        primary=review.prepare(run,output=f.f.root/'review',**args)
        secondary=evidence.prepare(run,output=f.f.root/'secondary',**args)
        self.assertEqual(primary['available_texts'],1)
        self.assertEqual(primary['planned_slots'],12)
        self.assertEqual(primary['source_backend'],'scripted')
        self.assertEqual(secondary['saved_contracts'],1)
        self.assertFalse(secondary['semantic_labels_generated'])
        verify(f.f.root/'review'); verify(f.f.root/'secondary')


if __name__=='__main__': unittest.main()
