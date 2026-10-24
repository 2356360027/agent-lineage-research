import unittest

from scripts.prepare_superhero_assets import review_text


class Tests(unittest.TestCase):
    def test_original_text_and_no_oracle(self):
        records = {i: {'question': f'ORIGINAL_{i}', 'evidence': f'EVIDENCE_{i}',
                       'SQL': 'SELECT SECRET_ORACLE', 'answer': 'SECRET_VALUE'} for i in (728, 733)}
        text = review_text(records, 1)
        for i in records:
            self.assertIn(records[i]['question'], text)
            self.assertIn(records[i]['evidence'], text)
        self.assertNotIn('SECRET_ORACLE', text)
        self.assertNotIn('SECRET_VALUE', text)
        self.assertLess(text.index('题号：733'), text.index('题号：728'))
        self.assertLess(text.index('题号：728'), text.index('第二部分：研究者配对'))
        self.assertIn('待澄清', text)
        self.assertIn('不是自然发生', text)

    def test_forms_are_blank_not_labels(self):
        records = {i: {'question': 'question', 'evidence': None} for i in (728, 733)}
        text = review_text(records, 2)
        self.assertIn('复核者 2（空白表）', text)
        self.assertIn('原公开辅助说明：(无)', text)
        self.assertIn('是否独立完成：\n', text)


if __name__ == '__main__':
    unittest.main()
