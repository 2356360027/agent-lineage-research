import unittest
from scripts.local_cache_sufficiency import analyse, average, avg_transform, worlds


class Tests(unittest.TestCase):
    def test_null_denominator(self):
        self.assertEqual(average((None,2)), '2')
        self.assertIsNone(average((None,)))
        self.assertIsNone(avg_transform((0,0)))

    def test_all_cases_and_witnesses(self):
        summaries,witnesses,observations=analyse()
        self.assertEqual(len(observations),290)
        self.assertEqual(len(witnesses),3)
        for witness in witnesses.values():
            self.assertEqual(witness['first']['cache'],witness['second']['cache'])
            self.assertNotEqual(witness['first']['target'],witness['second']['target'])
        for row in summaries:
            if row['classical_transform_tested']:
                self.assertEqual(row['classical_transform_mismatches'],0)


if __name__=='__main__':unittest.main()
