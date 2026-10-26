"""Synthetic identity/consistency failures; no model or database execution."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from runtime import bird_readonly_audit as audit
from runtime.student_club_input_admission import validate_contents, verify_pinned, DB_SHA, TASK_SHA
from scripts.audit_cache_action import snapshot
from tests.test_student_club_diagnostic_design import common


def inputs():
    c = common() | {'database_sha256':DB_SHA}
    tasks={1371:dict(question=c['history_request'],evidence=c['history_evidence'],SQL=c['history_sql'],db_id='student_club'),
        1317:dict(question=c['current_request'],evidence=c['current_evidence'],SQL='SELECT PRIVATE_REFERENCE_SENTINEL',db_id='student_club')}
    plan=dict(database_sha256=DB_SHA,task_sha256=TASK_SHA,
        queries=dict(original_H=tasks[1371]['SQL'],original_C=tasks[1317]['SQL']))
    responses={}
    for k,n in (('original_H',2),('original_C',1),('entity_H',2),('entity_C',1)):
        rows=[[['integer',str(n)]]]
        responses[k]=dict(status='EXECUTED',complete=True,columns=['COUNT(*)'],rows=rows,**audit.fingerprints(rows))
    summary=dict(status='REFERENCE_CHECK_COMPLETE',original_and_entity_counts_agree=True,database_unchanged=True,
        counts=dict(original_H=2,original_C=1,entity_H=2,entity_C=1))
    process=dict(original_sha256='a'*64,independent_before_model_judgment_confirmed_by_user=True)
    supplement=dict(original_sha256='a'*64,reviewer_2_readonly_negative_control_agreement=True,
        students_means_relevant_members_no_extra_identity_filter=True)
    return [c,tasks,plan,summary,responses,process,supplement,'a'*64]


class Tests(unittest.TestCase):
    def test_only_historical_scalar_promoted_to_cache(self):
        values=inputs(); before=deepcopy(values)
        cache=validate_contents(*values)
        self.assertEqual(values,before)
        self.assertEqual(cache['rows'],[[['integer','2']]])
        self.assertEqual(cache['sql_sha256'],audit.digest(values[0]['history_sql']))
        self.assertNotIn('PRIVATE_REFERENCE_SENTINEL',str(cache))
        self.assertNotIn('PRIVATE_REFERENCE_SENTINEL',str(values[0]))

    def test_task_db_reference_or_consent_mismatch_rejected(self):
        changes=[
            lambda x:x[0].update(current_request='WRONG'),
            lambda x:x[0].update(current_sql='GOLD'),
            lambda x:x[1][1317].update(db_id='other'),
            lambda x:x[2].update(database_sha256='0'*64),
            lambda x:x[2]['queries'].update(original_H='SELECT 999'),
            lambda x:x[2]['queries'].update(original_C='SELECT 999'),
            lambda x:x[3].update(database_unchanged=False),
            lambda x:x[3]['counts'].update(original_C=999),
            lambda x:x[4]['original_H'].update(complete=False),
            lambda x:x[4].pop('entity_C'),
            lambda x:x[4]['entity_C'].update(rows=[[['integer','999']]]),
            lambda x:x[5].update(independent_before_model_judgment_confirmed_by_user=False),
            lambda x:x[6].update(reviewer_2_readonly_negative_control_agreement=False),
            lambda x:x[6].update(students_means_relevant_members_no_extra_identity_filter=False),
            lambda x:x[6].update(original_sha256='0'*64)]
        for n,change in enumerate(changes):
            with self.subTest(case=n):
                values=inputs();change(values)
                with self.assertRaises(ValueError):validate_contents(*values)

    def test_rehashed_but_disagreeing_reference_is_still_rejected(self):
        values=inputs(); rows=[[['integer','9']]]
        values[4]['entity_C'].update(rows=rows,**audit.fingerprints(rows))
        values[3]['counts']['entity_C']=9
        with self.assertRaises(ValueError):validate_contents(*values)

    def test_pin_and_content_must_both_match(self):
        root=Path(__file__).resolve().parents[1]/'runtime/runs'
        with tempfile.TemporaryDirectory(dir=root,prefix='admission-unit-') as tmp:
            d=Path(tmp)
            audit.save_new(d/'item.json',{'synthetic':True})
            audit.save_new(d/'manifest.json',snapshot(d))
            pin=audit.hash_file(d/'manifest.json')
            verify_pinned(d,'manifest.json',pin)
            with self.assertRaises(ValueError):verify_pinned(d,'manifest.json','0'*64)
            audit.save_new(d/'extra.json',{'synthetic_extra':True})
            with self.assertRaises(ValueError):verify_pinned(d,'manifest.json',pin)


if __name__=='__main__':unittest.main()
