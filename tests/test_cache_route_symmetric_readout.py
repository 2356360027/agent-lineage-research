"""Artificial answers only; not model experiments or human annotations."""
from copy import deepcopy
import unittest
from runtime import bird_readonly_audit as audit
from runtime.cache_route_symmetric_design import classify
from runtime.cache_route_symmetric_readout import summarize, first_action


def reference(n=63):
    rows = [[['integer', str(n)]]]
    return dict(status='EXECUTED', complete=True, columns=['count'], rows=rows, **audit.fingerprints(rows))


def rows():
    return [dict(id=f'cell-{i}', final_status='ANSWERED', final_answer='63',
                 first_decision=classify('{"kind":"answer","text":"63"}', 1)) for i in range(4)]


class Tests(unittest.TestCase):
    def test_all_slots_and_one_independent_pair(self):
        value=rows(); before=deepcopy(value)
        result=summarize(list(reversed(value)), reference())
        self.assertEqual(result['counts']['MATCH'],4)
        self.assertEqual([r['id'] for r in result['rows']], [f'cell-{i}' for i in range(4)])
        self.assertEqual([r['cache_read_cost'] for r in result['rows']],[1,20,1,20])
        self.assertEqual(result['independent_task_pairs'],1)
        self.assertEqual(value,before)
        self.assertFalse(result['C_drift_assessed'])

    def test_reference_not_hardcoded(self):
        for n in (0,2,63,64):
            value=rows()
            for r in value:r['final_answer']=str(n)
            self.assertEqual(summarize(value,reference(n))['counts']['MATCH'],4)
        self.assertEqual(summarize(rows(),reference(64))['counts']['MISMATCH'],4)

    def test_prose_not_wrong_or_extracted(self):
        for answer in ('63 heroes','36 or 63','63.0','[63]','０６３','063','sixty three'):
            value=rows();value[0]['final_answer']=answer
            result=summarize(value,reference())
            self.assertEqual(result['counts']['UNCERTAIN'],1)
            self.assertEqual(result['counts']['MISMATCH'],0)
            self.assertEqual(result['human_review_queue'],['cell-0'])

    def test_failures_keep_late_answers_without_promoting(self):
        for status in ('FAILED','INTERRUPTED','ENVIRONMENT_INVALID','FINALIZATION_FAILED'):
            value=rows();value[0]['final_status']=status
            result=summarize(value,reference())
            self.assertEqual(result['counts']['NOT_COMPLETED'],1)
            self.assertEqual(sum(result['counts'].values()),4)

    def test_stopped_batch_keeps_unstarted_denominator(self):
        value=rows()
        for i,r in enumerate(value):
            r.update(final_status='FAILED' if i==0 else 'NOT_STARTED', final_answer=None, first_decision=None)
        result=summarize(value,reference())
        self.assertEqual(result['counts']['NOT_COMPLETED'],4)
        self.assertEqual(result['first_action_counts']['NO_RECORDED_DECISION'],4)

    def test_first_malformed_not_replaced(self):
        value=rows();value[0].update(final_status='INVALID_ACTION',final_answer=None,
                                    first_decision=classify('not json',1))
        result=summarize(value,reference())
        self.assertEqual(result['first_action_counts']['INVALID_OUTPUT'],1)
        self.assertEqual(result['counts']['INVALID_OUTPUT'],1)

    def test_decision_shape_and_horizon(self):
        for d in ({},classify('{"kind":"run_sql","sql":"SELECT 1"}',4)):
            with self.assertRaises(ValueError):first_action(d)
        for raw,kind in (('{"kind":"read_cache","cache_id":"v000"}','read_cache'),
                         ('{"kind":"run_sql","sql":"SELECT 1"}','run_sql')):
            self.assertEqual(first_action(classify(raw,1)),kind)
        bad=classify('{"kind":"answer","text":"63"}',1);bad['stop']=1
        with self.assertRaises(ValueError):first_action(bad)

    def test_missing_duplicate_foreign_rows(self):
        value=rows()
        with self.assertRaises(ValueError):summarize(value[:3],reference())
        value[0]=deepcopy(value[1])
        with self.assertRaises(ValueError):summarize(value,reference())
        value=rows();value[0]['id']='old-price8'
        with self.assertRaises(ValueError):summarize(value,reference())

    def test_bad_status_and_unstarted_payload(self):
        value=rows();value[0]['final_status']='RECOVERY_ACTION_VIOLATION'
        with self.assertRaises(ValueError):summarize(value,reference())
        value[0]['final_status']='NOT_STARTED'
        with self.assertRaises(ValueError):summarize(value,reference())
        value=rows();value[0]['first_decision']=None
        with self.assertRaises(ValueError):summarize(value,reference())

    def test_reference_fingerprint_and_shape_rejected(self):
        bad=reference();bad['rows'][0][0][1]='64'
        with self.assertRaises(ValueError):summarize(rows(),bad)
        with self.assertRaises(ValueError):summarize(rows(),reference(-1))


if __name__=='__main__':unittest.main()
