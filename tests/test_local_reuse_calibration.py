import unittest
from scripts.local_reuse_calibration import cases


class Tests(unittest.TestCase):
    def test_fixed_scope(self):
        jobs=cases()
        self.assertEqual(len(jobs),24)
        self.assertEqual(len({j['id'] for j in jobs}),24)
        self.assertEqual(sum(j['expected']=='REUSE' for j in jobs),6)
        self.assertEqual(sum(j['sufficient'] for j in jobs),12)
        self.assertEqual(len({j['family'] for j in jobs}),6)

    def test_no_answer_labels_in_model_input(self):
        for j in cases():
            self.assertNotIn('"expected"',str(j['messages']))
            self.assertNotIn('"sufficient"',str(j['messages']))


if __name__=='__main__':unittest.main()
