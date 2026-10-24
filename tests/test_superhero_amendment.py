import unittest
from copy import deepcopy
from scripts.prepare_superhero_amendment import review_text, CLARIFICATION, VERSION


class Tests(unittest.TestCase):
    def setUp(self):
        self.records = {i: dict(question=f'ORIGINAL_{i}', evidence=f'EVIDENCE_{i}',
                                SQL='ORACLE_SQL_SECRET', result='RESULT_SECRET') for i in (728, 733)}

    def test_no_oracle_and_originals_preserved(self):
        original = deepcopy(self.records)
        text = review_text(self.records, 1)
        for row in self.records.values():
            self.assertIn(row['question'], text)
            self.assertIn(row['evidence'], text)
        self.assertNotIn('ORACLE_SQL_SECRET', text)
        self.assertNotIn('RESULT_SECRET', text)
        self.assertEqual(original, self.records)

    def test_unapproved_blank_and_disclosed(self):
        text = review_text(self.records, 2)
        for marker in ('空白表', '待澄清', '研究者改编', '实际填写日期和时间：\n', '不是对原题唯一正确解释的声明'):
            self.assertIn(marker, text)
        self.assertIn(VERSION, text)
        self.assertIn(CLARIFICATION, text)

    def test_semantic_clauses_and_no_row_order_requirement(self):
        for marker in ('all observed', 'non-null id', 'not distinct names',
                       'case-sensitive binary', 'null names form one', 'ties share rank',
                       'order of returned rows is not', 'no row limit'):
            self.assertIn(marker, CLARIFICATION)

    def test_missing_source_evidence_explicit(self):
        self.records[728]['evidence'] = None
        self.assertIn('728 原公开辅助说明：(无)', review_text(self.records, 1))


if __name__ == '__main__': unittest.main()
