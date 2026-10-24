import unittest
from scripts.audit_superhero_snapshot import compare_results
from runtime.bird_readonly_audit import typed


def result(columns, rows):
    return {'complete': True, 'columns': columns, 'rows': [[typed(c) for c in row] for row in rows]}


class Tests(unittest.TestCase):
    def test_match_is_not_admission(self):
        h = result(['EyeColor', 'Count', 'PopularityRank'], [('Gold', 2, 1)])
        x = compare_results(h, result(['count'], [(2,)]))
        self.assertTrue(x['matches_on_this_snapshot'])
        self.assertFalse(x['semantic_approval'])
        self.assertEqual(x['formal_tasks_admitted'], 0)

    def test_mismatch_preserved(self):
        h = result(['EyeColor', 'Count', 'PopularityRank'], [('Gold', 2, 1)])
        self.assertFalse(compare_results(h, result(['count'], [(3,)]))['matches_on_this_snapshot'])

    def test_incomplete_and_malformed_rejected(self):
        h = result(['EyeColor', 'Count', 'PopularityRank'], [])
        for cur in ({'complete': False}, result(['count'], []), result(['count'], [(None,)]),
                    result(['count'], [('0',)]), result(['count'], [(-1,)])):
            with self.subTest(cur=cur), self.assertRaises(ValueError):
                compare_results(h, cur)


if __name__ == '__main__': unittest.main()
