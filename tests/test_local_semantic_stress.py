import unittest
from scripts.local_semantic_stress import worlds, oracle, evaluate, STRONG_SQL


class Tests(unittest.TestCase):
    def test_domain_count(self):
        self.assertEqual(len(list(worlds())), 576)

    def test_counterexample_has_no_duplicate_edges(self):
        w = dict(events=[(1,"Women's Soccer",'a'),(2,"Women's Soccer",'b')],
                 members=[(1,'Medium')], attendance=[(1,1),(2,1)])
        count_rows = "SELECT COUNT(*) FROM event e JOIN attendance a ON e.event_id=a.link_to_event JOIN member m ON m.member_id=a.link_to_member WHERE e.event_name='Women''s Soccer' AND m.t_shirt_size='Medium'"
        self.assertEqual(oracle(w), 1)
        self.assertEqual(evaluate(w, [count_rows, STRONG_SQL]),
                         [dict(value=2,error=None),dict(value=1,error=None)])

    def test_null_excluded_and_write_refused(self):
        w = dict(events=[(1,"Women's Soccer",'a')],
                 members=[(1,None)], attendance=[(1,1)])
        self.assertEqual(oracle(w), 0)
        self.assertEqual(evaluate(w,[STRONG_SQL])[0]['value'],0)
        self.assertIsNotNone(evaluate(w,['DELETE FROM member'])[0]['error'])

    def test_empty_attendance(self):
        w = dict(events=[],members=[],attendance=[])
        self.assertEqual(evaluate(w,[STRONG_SQL])[0]['value'],0)


if __name__ == '__main__':
    unittest.main()
