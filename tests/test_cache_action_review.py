"""Scripted review fixtures; none are genuine human labels or research data."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from runtime import cache_action_review as review
from runtime.cache_action_preview import build_plan, HISTORIES
from tests.test_cache_action_loop import pair


class ReviewTests(unittest.TestCase):
    def setUp(self):
        old={'jobs':[dict(history_id=h,read_price=1,envelope=pair()[1],cached_result_sha256='a'*64)
                     for h in HISTORIES]}
        self.plan=build_plan(old)
        self.outcomes={j['id']:dict(final_status='ANSWERED',final_answer='synthetic answer',trace_sha256='b'*64)
                       for j in self.plan['jobs']}
        policy=json.loads((Path(__file__).resolve().parents[1]/'configs/cache_action_review_policy_v1.json').read_text('utf-8'))
        self.result=review.build(self.plan,self.outcomes,policy,{'200':{'fixture':True},'234':{'fixture':True}})
        self.blank=self.result['reviewer_1']; self.policy=policy

    def submissions(self):
        return [dict(reviewer_code='SCRIPTED_'+str(n),date='fixture-not-human',independent_completion=True,
            model_or_other_person_assistance=False,ratings=[dict(item_id=i['item_id'],rating='MATCH',
            quoted_answer_span='synthetic answer',reason='scripted fixture only',reference_concern=False)
            for i in self.blank['items']]) for n in (1,2)]

    def test_blank_reviews_are_separate_and_hide_condition_metadata(self):
        self.assertEqual(len(self.blank['items']),12)
        self.assertTrue(all(i['rating'] is None for i in self.blank['items']))
        self.result['reviewer_1']['items'][0]['rating']='MATCH'
        self.assertIsNone(self.result['reviewer_2']['items'][0]['rating'])
        for item in self.blank['items']:
            for field in ('history','available','cost','job_id','trace_sha256'):
                self.assertNotIn(field,item)
        self.assertEqual(self.result['new_human_labels'],0)

    def test_no_answer_slot_cannot_get_invented_text(self):
        ident=next(iter(self.outcomes)); self.outcomes[ident]['final_status']='NOT_STARTED'
        with self.assertRaises(ValueError):
            review.build(self.plan,self.outcomes,self.policy,{'200':{},'234':{}})

    def test_missing_outcome_rejected_not_dropped(self):
        self.outcomes.pop(next(iter(self.outcomes)))
        with self.assertRaises(ValueError):
            review.build(self.plan,self.outcomes,self.policy,{'200':{},'234':{}})

    def test_agreement_is_provisional_not_attested_or_C(self):
        result=review.account(self.blank,self.submissions())
        self.assertTrue(all(r['review_status']=='PROVISIONAL_AGREED_MATCH' for r in result['rows']))
        self.assertFalse(result['identity_independently_verified'])
        self.assertFalse(result['C_drift_assessed'])

    def test_disagreement_missing_and_reference_concern_remain_unresolved(self):
        for label,concern,expected in (('MISMATCH',False,'DISAGREEMENT_UNRESOLVED'),
                (None,False,'PENDING_REVIEW'),('UNCERTAIN',False,'UNCERTAIN_UNRESOLVED'),
                ('MATCH',True,'REFERENCE_CONCERN_UNRESOLVED')):
            submissions=self.submissions()
            submissions[1]['ratings'][0].update(rating=label,reference_concern=concern)
            self.assertEqual(review.account(self.blank,submissions)['rows'][0]['review_status'],expected)

    def test_blank_or_assisted_provenance_not_promoted(self):
        for field,value in (('reviewer_code',''),('independent_completion',None),
                            ('model_or_other_person_assistance',True)):
            submissions=self.submissions(); submissions[0][field]=value
            with self.assertRaises(ValueError): review.account(self.blank,submissions)

    def test_fake_quote_and_duplicate_ids_rejected(self):
        submissions=self.submissions(); submissions[0]['ratings'][0]['quoted_answer_span']='not in text'
        with self.assertRaises(ValueError): review.account(self.blank,submissions)
        submissions=self.submissions(); submissions[0]['ratings'][1]=deepcopy(submissions[0]['ratings'][0])
        with self.assertRaises(ValueError): review.account(self.blank,submissions)

    def test_non_answer_status_not_labelled_mismatch(self):
        self.blank['items'][0].update(final_status='ABSTAINED',answer_text=None)
        submissions=self.submissions()
        with self.assertRaises(ValueError): review.account(self.blank,submissions)
        for s in submissions: s['ratings'][0].update(rating=None,quoted_answer_span='',reason='')
        self.assertEqual(review.account(self.blank,submissions)['rows'][0]['review_status'],'NO_FINAL_ANSWER')

    def filled(self, index):
        submission=self.submissions()[index]
        packet=deepcopy(self.blank)
        packet.update({key:submission[key] for key in review.PROVENANCE_FIELDS})
        for item,rating in zip(packet['items'],submission['ratings']): item.update(rating)
        return packet

    def test_fillable_packet_roundtrip_does_not_require_retyping_material(self):
        imported=[review.submission_from_filled(self.blank,self.filled(n),
                  expected_blank_sha256=review.digest(self.blank)) for n in (0,1)]
        self.assertEqual(imported,self.submissions())
        self.assertEqual(len(review.account(self.blank,imported)['rows']),12)

    def test_material_changes_are_rejected_not_silently_discarded(self):
        for field,value in (('answer_text','replaced answer'),('reference',{'changed':True}),
                            ('current_request','different request'),('final_status','FAILED')):
            packet=self.filled(0); packet['items'][0][field]=value
            with self.assertRaises(ValueError):
                review.submission_from_filled(self.blank,packet,expected_blank_sha256=review.digest(self.blank))
        packet=self.filled(0); packet['instructions']=[]
        with self.assertRaises(ValueError):
            review.submission_from_filled(self.blank,packet,expected_blank_sha256=review.digest(self.blank))

    def test_changed_template_and_dropped_or_duplicate_items_rejected(self):
        with self.assertRaises(ValueError):
            review.submission_from_filled(self.blank,self.filled(0),expected_blank_sha256='0'*64)
        for duplicate in (False,True):
            packet=self.filled(0)
            if duplicate: packet['items'][1]=deepcopy(packet['items'][0])
            else: packet['items'].pop()
            with self.assertRaises(ValueError):
                review.submission_from_filled(self.blank,packet,expected_blank_sha256=review.digest(self.blank))

    def test_case_and_space_variant_reviewer_code_not_independence(self):
        submissions=self.submissions(); submissions[1]['reviewer_code']=' scripted_1 '
        with self.assertRaises(ValueError): review.account(self.blank,submissions)

    def test_nonterminal_status_cannot_be_reviewed(self):
        row=self.outcomes[next(iter(self.outcomes))]
        row.update(final_status='RUNNING',final_answer=None)
        with self.assertRaises(ValueError):
            review.build(self.plan,self.outcomes,self.policy,{'200':{},'234':{}})


if __name__=='__main__': unittest.main()
