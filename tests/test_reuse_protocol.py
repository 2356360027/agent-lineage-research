import json
import unittest
from collections import Counter
from runtime.reuse_scenarios import build_suite
from runtime.reuse_protocol import audit_matrix,batches,messages,schedule,label_order


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.cases=build_suite('development')

    def test_full_development_matrix(self):
        result=audit_matrix(self.cases)
        self.assertEqual(result['job_count'],480)
        self.assertEqual(result['batch_call_counts'],[200,200,80])
        self.assertEqual(result['logical_M4_workflow_calls'],288)
        jobs=schedule(self.cases)
        self.assertEqual(Counter(j['method'] for j in jobs),{'M1':144,'M2':144,'M3':48,'M4':144})
        seen=set()
        for j in jobs:
            if j['method']=='M4':self.assertIn(j['proposal_id'],seen)
            seen.add(j['job_id'])

    def test_all_verifier_prompts_hide_price_and_reason(self):
        index={c['case_id']:c for c in self.cases}
        for j in schedule(self.cases):
            if j['method']!='M4':continue
            prompt=messages(index[j['case_id']],j,'applicable')
            data=json.loads(prompt[1]['content'])
            self.assertNotIn('costs',data)
            self.assertNotIn('audit',data)
            self.assertNotIn('candidate_reason',data)
            self.assertNotIn('candidate_action',data)
            self.assertEqual(len(prompt),2)

    def test_order_balanced_within_both_oracle_classes(self):
        for sufficient in (True,False):
            options=Counter(tuple(label_order(c)[0]) for c in self.cases if c['audit']['sufficient']==sufficient)
            self.assertEqual(sorted(options.values()),[12,12])

    def test_limits_and_determinism(self):
        self.assertEqual(schedule(self.cases),schedule(list(reversed(self.cases))))
        # by-base cache insertion order could affect PRNG choices: the function
        # must canonicalize case order too, not rely on input filesystem order.
        with self.assertRaises(ValueError):batches(schedule(self.cases),201)


if __name__=='__main__':unittest.main()
