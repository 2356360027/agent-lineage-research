import unittest
from scripts.render_private_contract_review_text import render


class TextTests(unittest.TestCase):
    def packet(self):
        return {'status':'BLANK_PRIMARY_ENDPOINT_REVIEW_NOT_RESULTS',
                'reviewer':{'code':None},
                'items':[{'item_id':f'R-{i}', 'current_request':f'task {i % 2}',
                          'evidence':'evidence', 'schema':[{'table':'sample'}],
                          'raw_contract':f'{{"raw": "verbatim {i}"}}',
                          'review':{'label':None}} for i in range(12)]}

    def test_all_outputs_verbatim_and_unfilled(self):
        p = self.packet()
        text = render(p, 1)
        for item in p['items']:
            self.assertIn(item['raw_contract'], text)
            self.assertIn('编号：'+item['item_id']+'\n', text)
        self.assertEqual(text.count('整体判断（match / contradiction / unresolved）：\n'), 12)
        self.assertEqual(text.count('你的需求理解（'), 2)
        self.assertNotIn('read_price', text)

    def test_refuse_filled_packet(self):
        p = self.packet()
        p['items'][0]['review']['label'] = 'match'
        with self.assertRaises(ValueError):
            render(p, 1)


if __name__ == '__main__':
    unittest.main()
