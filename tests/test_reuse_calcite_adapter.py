from pathlib import Path
import unittest
from scripts.run_reuse_calcite import bag_equal,load_sqlglot,projection_rewrite,quote_query,check_generated_rewrite
from scripts.prepare_reuse_suite import calcite_input
from runtime.reuse_scenarios import build_suite


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parents[1]
        wheel=root/'runtime/runs/dependencies/sqlglot-30.22.0-20261012-001/sqlglot-30.22.0-py3-none-any.whl'
        if not wheel.exists(): raise unittest.SkipTest('Pinned optional SQLGlot wheel not installed')
        cls.sg=load_sqlglot(wheel)
        cls.cases=build_suite('development')

    def test_projection_has_no_oracle_argument(self):
        covered=[]
        for case in self.cases:
            public=calcite_input(case)
            rewrite=projection_rewrite(public,self.sg)
            if rewrite:
                covered.append(case['case_id'])
                self.assertTrue(case['audit']['sufficient'])
                result=check_generated_rewrite(case,rewrite)
                self.assertEqual(result['value_mismatches'],0)
                self.assertEqual(result['column_mismatches'],0)
        self.assertEqual(len(covered),8)  # 4 public projection structures x 2 worlds

    def test_identifier_quoting_does_not_call_optimizer(self):
        q=quote_query('SELECT period,COUNT(*) AS n FROM items GROUP BY period',self.sg)
        self.assertIn('"period"',q)
        self.assertIn('COUNT(*)',q)
        with self.assertRaises(ValueError):quote_query('DELETE FROM items',self.sg)
        with self.assertRaises(ValueError):quote_query('SELECT 1; SELECT 2',self.sg)

    def test_distinct_and_missing_columns_not_reusable(self):
        for case in self.cases:
            if not case['audit']['sufficient']:
                self.assertIsNone(projection_rewrite(calcite_input(case),self.sg))

    def test_bag_preserves_multiplicity_and_null(self):
        self.assertTrue(bag_equal([[1],[None],[1]],[[None],[1],[1]]))
        self.assertFalse(bag_equal([[1],[1]],[[1]]))
        self.assertFalse(bag_equal([[None]],[[0]]))


if __name__=='__main__':unittest.main()
