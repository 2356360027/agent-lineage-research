import sqlite3
import unittest

from scripts.audit_native_positive_relation import gold_count, cases, compare_fixture


class Tests(unittest.TestCase):
    columns = ['EyeColor', 'Count', 'PopularityRank']

    def test_present_and_absent_complete(self):
        self.assertEqual(gold_count(self.columns, [('Blue', 9, 1), ('Gold', 2, 2)], complete=True), 2)
        self.assertEqual(gold_count(self.columns, [('Blue', 9, 1)], complete=True), 0)
        self.assertEqual(gold_count(self.columns, [], complete=True), 0)

    def test_case_and_null(self):
        self.assertEqual(gold_count(self.columns, [(None, 3, 1), ('gold', 2, 2)], complete=True), 0)

    def test_incomplete_not_zero(self):
        for complete in (False, None, 1, 'true'):
            with self.assertRaises(ValueError):
                gold_count(self.columns, [], complete=complete)

    def test_bad_schema_or_payload(self):
        bad = [([('Gold', -1, 1)]), [('Gold', True, 1)], [('Gold', 2, 0)],
               [('Gold', '2', 1)], [('Gold', 2)], [('Gold', 2, 1), ('Gold', 3, 2)]]
        for rows in bad:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                gold_count(self.columns, rows, complete=True)
        with self.assertRaises(ValueError):
            gold_count(['EyeColor', 'PopularityRank', 'Count'], [], complete=True)

    def test_fixed_cases(self):
        first, second = list(cases()), list(cases())
        self.assertEqual(first, second)
        self.assertEqual(len(first), 74)
        self.assertEqual(len({x[0] for x in first}), 74)

    def test_comparison_detects_wrong_reference(self):
        fixture = next(cases())
        result = compare_fixture("SELECT 'Gold' AS EyeColor, 2 AS Count, 1 AS PopularityRank",
                                 'SELECT 99', fixture)
        self.assertFalse(result['matches'])
        self.assertEqual(result['derived'], 2)

    def test_reference_error_not_imputed(self):
        with self.assertRaises(sqlite3.OperationalError):
            compare_fixture('SELECT missing_column FROM superhero', 'SELECT 0', next(cases()))


if __name__ == '__main__':
    unittest.main()
