"""Synthetic reviews exercise file gates; never count these as human evidence."""
from copy import deepcopy
from datetime import datetime,timezone
import unittest
from unittest.mock import patch

from runtime import bird_readonly_audit as audit
from runtime.native_symmetric_batch_audit import read
from runtime.student_club_review_files import export,lock_stage_a,release_stage_b
from tests import test_student_club_batch_audit as fixture


class Tests(unittest.TestCase):
    setUpClass=classmethod(fixture.Tests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.Tests.tearDownClass.__func__)

    def setUp(self):
        self.root=self.f.root/self._testMethodName
        export(self.f.root/'out',self.f.plan,self.f.c,self.f.cache,output=self.root,
            masking_seed='SYNTHETIC_TEST_ONLY_SEED',expected_plan_sha=audit.digest(self.f.plan),
            expected_archive_sha=audit.hash_file(self.f.root/'out/artifact_hashes.json'),
            native_counter=self.f.counter,plain_counter=self.f.plain,
            frozen_tool_sources=fixture.Tests.sources(self),mode='SCRIPTED_NOT_MODEL')
        self.pin=audit.hash_file(self.root/'handoff.json')

    def submission(self,number,code=None):
        form=read(self.root/'stage_a'/f'reviewer-{number}.json')
        form['identity']=dict(reviewer_code=code or f'SYNTHETIC-{number}',
            actual_datetime=datetime.now(timezone.utc).isoformat(),independent_judgment=True,
            model_assistance_scope='未使用模型',prior_material_exposure='Synthetic unit test, not a human review')
        for item in form['stage_a']['items']:
            item.update(assessment='信息不足',evidence_quote=item['definition'],reason='SYNTHETIC TEST ANSWER')
        return form

    def save_lock(self,number,form):
        path=self.root/f'synthetic-submission-{number}.json';audit.save_new(path,form)
        return lock_stage_a(self.root,path,reviewer_number=number,expected_handoff_sha=self.pin)

    def test_export_only_blank_stage_a_no_programs_and_no_labels(self):
        self.assertFalse((self.root/'stage_b').exists())
        for n in (1,2):
            form=read(self.root/'stage_a'/f'reviewer-{n}.json')
            self.assertNotIn('stage_b',form)
            self.assertNotIn('issued_sql',str(form))
            self.assertEqual(len(form['stage_a']['items']),8)
            self.assertTrue(all(x['assessment'] is None for x in form['stage_a']['items']))
        self.assertEqual(read(self.root/'handoff.json')['independent_human_reviews_received'],0)

    def test_two_locks_required_release_preserves_blank_program_reviews(self):
        a=self.save_lock(1,self.submission(1))
        with self.assertRaises(ValueError):release_stage_b(self.root,expected_handoff_sha=self.pin,expected_lock_shas={1:a['lock_sha256']})
        b=self.save_lock(2,self.submission(2))
        result=release_stage_b(self.root,expected_handoff_sha=self.pin,expected_lock_shas={1:a['lock_sha256'],2:b['lock_sha256']})
        self.assertEqual(result['status'],'STAGE_B_UNFILLED_RELEASED')
        form=read(self.root/'stage_b/reviewer-1.json')
        self.assertTrue(all(x['program_vs_definition'] is None for x in form['stage_b']['items']))
        receipt=read(self.root/'stage_b/release.json')
        self.assertFalse(receipt['human_identity_independence_verified'])
        with self.assertRaises(ValueError):lock_stage_a(self.root,self.root/'synthetic-submission-1.json',reviewer_number=1,expected_handoff_sha=self.pin)

    def test_blank_submission_retained_but_not_locked(self):
        form=read(self.root/'stage_a/reviewer-1.json')
        with self.assertRaises(ValueError):self.save_lock(1,form)
        self.assertFalse((self.root/'locks/reviewer-1').exists())
        originals=list((self.root/'intake').glob('*/submission.json'))
        self.assertEqual(len(originals),1)
        self.assertEqual(read(originals[0]),form)

    def test_source_wording_edit_and_model_judgment_scope_refused(self):
        form=self.submission(1);form['stage_a']['items'][0]['definition']='OPERATOR REPLACEMENT'
        with self.assertRaises(ValueError):self.save_lock(1,form)
        form=self.submission(2);form['identity']['model_assistance_scope']='模型代写判断'
        with self.assertRaises(ValueError):self.save_lock(2,form)
        self.assertFalse((self.root/'locks').exists())

    def test_duplicate_reviewer_and_changed_external_seal_refused(self):
        self.save_lock(1,self.submission(1,code='same-person'))
        with self.assertRaises(ValueError):self.save_lock(2,self.submission(2,code='SAME-PERSON'))
        with self.assertRaises(ValueError):release_stage_b(self.root,expected_handoff_sha='0'*64,expected_lock_shas={})

    def test_stopped_batch_cannot_export_reviewable_success_prefix(self):
        output=self.root/'refused'
        with patch('runtime.student_club_review_files.inspect',return_value={'status':'SEALED_STOPPED_INVENTORY_ONLY'}):
            with self.assertRaises(ValueError):export(None,None,None,None,output=output,masking_seed='x'*16)
        self.assertFalse(output.exists())


if __name__=='__main__':unittest.main()
