import unittest
from copy import deepcopy
from scripts.import_combined_contract_reviews import transcribe


class Tests(unittest.TestCase):
    def fixture(self):
        template = {'reviewer':{'code':None},'items':[
            {'item_id':f'R-{i}', 'raw_contract':'{"note":"some original phrase"}',
             'review':{'whole_contract_vs_request':None,'material_omission':None,
                       'field_misplacement_only':None,'evidence_quote':None,'reason':None}}
            for i in range(12)]}
        text = ''
        for slot in (1,2):
            text += f'# 开发实验契约复核表：复核者 {slot}\n'
            text += f'复核者自选代号：fixture-{slot}\n日期：2026-10-24\n'
            text += '是否独立完成（是/否）：是\n此前接触过哪些题目、输出、研究讨论或答案：无\n'
            text += '本次是否借助他人/模型；如有，请说明范围：否\n第一部分\n你的需求理解：测试材料\n'
            for i in range(12):
                text += f'编号：R-{i}\n整体判断（match / contradiction / unresolved）：match\n'
                text += '是否有实质关键遗漏（是/否/未决）：否\n'
                text += '问题是否仅为字段摆放错位（是/否/未决；无问题填否）：否\n'
                text += '支持判断的模型原文片段（精确引用，不改写）：\n`"original phrase"`\n'
                text += '理由（结合需求解释，不能仅凭字段名或格式）：\nfixture reason\n---\n'
        return text,template

    def test_preserve_labels_and_record_quote_delimiters(self):
        text,template = self.fixture()
        original = deepcopy(template)
        reviews,changes = transcribe(text,template,'fake-sha')
        self.assertEqual(template,original)
        self.assertEqual(len(reviews),2)
        self.assertEqual(len(changes),24)
        self.assertEqual(reviews[0]['items'][0]['review']['evidence_quote'],'original phrase')
        self.assertEqual(reviews[0]['items'][0]['review']['reason'],'fixture reason')
        self.assertEqual(reviews[0]['reviewer']['code'],'fixture-1')

    def test_disagreement_is_preserved(self):
        text,template = self.fixture()
        text = text.replace('）：match','）：contradiction',1)
        reviews,_ = transcribe(text,template,'fake-sha')
        self.assertEqual(reviews[0]['items'][0]['review']['whole_contract_vs_request'],'contradiction')
        self.assertEqual(reviews[1]['items'][0]['review']['whole_contract_vs_request'],'match')

    def test_reject_duplicate_and_unmatched_quote(self):
        text,template = self.fixture()
        for altered in (text.replace('编号：R-1\n','编号：R-0\n',1),
                        text.replace('`"original phrase"`','`"invented phrase"`',1)):
            with self.assertRaises(ValueError):
                transcribe(altered,template,'fake-sha')

    def test_nonindependence_is_not_rewritten(self):
        text,template = self.fixture()
        reviews,_ = transcribe(text.replace('是否独立完成（是/否）：是','是否独立完成（是/否）：否',1),template,'fake-sha')
        self.assertFalse(reviews[0]['reviewer']['independently_completed'])


if __name__ == '__main__':
    unittest.main()
