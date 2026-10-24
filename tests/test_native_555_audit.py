import unittest
from scripts.audit_native_555 import facts, one_row, decode_scalar
from runtime.bird_readonly_audit import typed


def row(values):
    return {'complete': True, 'columns': list(values), 'rows': [[typed(v) for v in values.values()]]}


class Tests(unittest.TestCase):
    def test_null_and_scope_preserved(self):
        value = facts({'user_scope': row({'matching_users': 2, 'distinct_user_ids': 2}),
            'post_scope': row(dict(joined_rows=4, distinct_post_ids=3, nonnull_scores=2, score_sum=6, score_avg=3.0))})
        self.assertEqual(value['matching_users'], 2)
        self.assertEqual(value['null_score_rows'], 2)
        self.assertTrue(value['join_multiplicity_observed'])
        self.assertFalse(value['semantic_approval'])
        self.assertEqual(value['formal_tasks_admitted'], 0)

    def test_empty_is_not_zero(self):
        value = facts({'user_scope': row({'matching_users': 0, 'distinct_user_ids': 0}),
            'post_scope': row(dict(joined_rows=0, distinct_post_ids=0, nonnull_scores=0, score_sum=None, score_avg=None))})
        self.assertIsNone(value['score_avg'])
        self.assertTrue(value['empty_or_all_null_observed'])

    def test_incomplete_rejected(self):
        for r in ({'complete': False}, {'complete': True, 'rows': []}):
            with self.assertRaises(ValueError): one_row(r)

    def test_hex_float_decoding(self):
        self.assertEqual(decode_scalar(typed(1.25)), 1.25)


if __name__ == '__main__': unittest.main()
