"""Synthetic text fixtures, not genuine reviewer submissions."""
from copy import deepcopy
import unittest
from runtime.cache_action_review_text import transcribe
from runtime.cache_action_review import digest


class ReviewTextTests(unittest.TestCase):
    def setUp(self):
        self.blank = dict(status='BLANK_REVIEW_NOT_HUMAN_LABELS', reviewer_code='',date='',
            independent_completion=None,model_or_other_person_assistance=None,
            items=[dict(item_id=f'R{i:016x}', final_status='ANSWERED',answer_text='synthetic answer',
                        rating=None,quoted_answer_span='',reason='',reference_concern=False)
                   for i in range(12)])
        self.text = '\n'.join(
            f'# 本轮最终答案复核：复核者 {n}\n复核者代号：fixture-{n}\n日期：fixture-only\n'
            '是否独立完成（是/否）：是\n是否借助模型或其他人填写（是/否；如有请说明）：否\n'
            + '\n'.join(f'条目编号：{i["item_id"]}\n评分（MATCH/MISMATCH/UNCERTAIN；无答案留空）：MATCH\n'
                '答案原句引用："synthetic answer"\n理由：synthetic fixture only\n'
                '参考疑问（是/否；如有请说明）：否\n' for i in self.blank['items']) for n in (1,2))

    def parse(self, text=None):
        return transcribe(self.text if text is None else text,self.blank,expected_blank_sha256=digest(self.blank))

    def test_roundtrip_no_authorship_promotion(self):
        before=deepcopy(self.blank); result=self.parse(self.text.replace('\n','\r\n'))
        self.assertEqual([len(s['ratings']) for s in result['submissions']],[12,12])
        self.assertEqual(len(result['quote_delimiter_adjustments']),24)
        self.assertEqual(result['independent_human_reviews_counted'],0)
        self.assertEqual(self.blank,before)

    def test_negative_and_uncertain_labels_not_repaired(self):
        text=self.text.replace('）：MATCH','）：MISMATCH',1)
        self.assertEqual(self.parse(text)['submissions'][0]['ratings'][0]['rating'],'MISMATCH')
        text=self.text.replace('）：MATCH','）：UNCERTAIN',1)
        self.assertEqual(self.parse(text)['submissions'][0]['ratings'][0]['rating'],'UNCERTAIN')

    def test_assistance_self_report_retained_not_converted(self):
        text=self.text.replace('是否借助模型或其他人填写（是/否；如有请说明）：否',
                               '是否借助模型或其他人填写（是/否；如有请说明）：是',1)
        self.assertTrue(self.parse(text)['submissions'][0]['model_or_other_person_assistance'])

    def test_unknown_duplicate_ids_and_quotes_rejected(self):
        for text in (self.text.replace('R0000000000000000','Rffffffffffffffff',1),
                     self.text.replace('R0000000000000001','R0000000000000000',1),
                     self.text.replace('"synthetic answer"','"changed answer"',1)):
            with self.assertRaises(ValueError): self.parse(text)

    def test_duplicate_fields_and_missing_section_rejected(self):
        for text in (self.text.replace('理由：synthetic fixture only','理由：one\n理由：two',1),
                     self.text.split('# 本轮最终答案复核：复核者 2')[0]):
            with self.assertRaises(ValueError): self.parse(text)

    def test_wrong_blank_hash_rejected(self):
        with self.assertRaises(ValueError):
            transcribe(self.text,self.blank,expected_blank_sha256='0'*64)

    def test_multiline_reason_is_not_silently_truncated(self):
        with self.assertRaises(ValueError):
            self.parse(self.text.replace('理由：synthetic fixture only',
                                         '理由：synthetic fixture only\nadditional concern',1))

    def test_missing_rating_stays_missing_and_concern_retained(self):
        text=self.text.replace('）：MATCH','）：',1).replace(
            '参考疑问（是/否；如有请说明）：否','参考疑问（是/否；如有请说明）：是',1)
        row=self.parse(text)['submissions'][0]['ratings'][0]
        self.assertIsNone(row['rating']); self.assertTrue(row['reference_concern'])


if __name__=='__main__': unittest.main()
