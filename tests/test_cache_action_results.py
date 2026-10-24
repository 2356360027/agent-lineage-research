import unittest
from copy import deepcopy
from scripts.audit_cache_action_results import compare, bag


class ResultTests(unittest.TestCase):
    def record(self, rows, columns, tool='run_sql'):
        value = dict(status='EXECUTED', complete=True, rows=rows, columns=columns)
        return dict(response=deepcopy(value), private={'backend_result':value}, request={'tool':tool})

    def test_bag_preserves_types_duplicates_not_order(self):
        a, b = [['text','a']], [['text','b']]
        self.assertEqual(bag([a,b]), bag([b,a]))
        self.assertNotEqual(bag([a,a]), bag([a]))
        self.assertNotEqual(bag([[['integer','3']]]), bag([[['text','3']]]))

    def test_direct_and_nonmatch(self):
        r = self.record([[['integer','3']]], ['n'])
        self.assertTrue(compare(r, {'rows':[[['integer','3']]]}, 'h234a')['direct_typed_bag_match'])
        self.assertFalse(compare(r, {'rows':[[['integer','4']]]}, 'h234a')['direct_typed_bag_match'])

    def test_distinct_nonnull_count_is_separate(self):
        r = self.record([[['text','a']],[['text','a']],[['text','b']],[['null',None]]], ['bond_id'], 'read_cache')
        out = compare(r, {'rows':[[['integer','2']]]}, 'h234b')
        self.assertFalse(out['direct_typed_bag_match'])
        self.assertTrue(out['scripted_distinct_count_match'])

    def test_reject_incomplete_or_changed_visible_data(self):
        r = self.record([[['integer','3']]], ['n'])
        r['response']['complete'] = False
        with self.assertRaises(ValueError): compare(r, {'rows':[]}, 'h234a')
        r['response']['complete'] = True
        r['response']['rows'] = []
        with self.assertRaises(ValueError): compare(r, {'rows':[]}, 'h234a')


if __name__ == '__main__': unittest.main()
