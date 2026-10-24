import unittest

from runtime.cache_action_horizon import classify, phase, MAX_GENERATIONS, MAX_TOOL_ATTEMPTS


class HorizonTests(unittest.TestCase):
    def test_schedule_has_exactly_three_tool_opportunities(self):
        sql = '{"kind":"run_sql","sql":"SELECT 1"}'
        decisions = [classify(sql, n) for n in range(1, MAX_GENERATIONS + 1)]
        self.assertEqual(sum(d['dispatch_tool'] for d in decisions), MAX_TOOL_ATTEMPTS)
        self.assertEqual(decisions[-1]['status'], 'TOOL_HORIZON_VIOLATION')

    def test_recovery_disallows_reads_without_executing_them(self):
        raw = '{"kind":"read_cache","cache_id":"v000"}'
        for n in (1, 2):
            self.assertTrue(classify(raw, n)['dispatch_tool'])
        self.assertEqual(classify(raw, 3)['status'], 'RECOVERY_ACTION_VIOLATION')
        self.assertFalse(classify(raw, 3)['dispatch_tool'])

    def test_invalid_outputs_are_not_repaired(self):
        for raw in ('{}', '{"kind":"answer","text":"x"} extra',
                    '{"kind":"answer","kind":"answer","text":"x"}', None):
            d = classify(raw, 1)
            self.assertEqual(d['status'], 'INVALID_ACTION')
            self.assertTrue(d['stop'])
            self.assertIsNone(d['action'])

    def test_answer_is_not_a_correctness_label(self):
        for n in range(1, 5):
            d = classify('{"kind":"answer","text":"unverified answer"}', n)
            self.assertEqual(d['status'], 'ANSWERED')
            self.assertTrue(d['stop'])
            self.assertNotIn('correct', d)
            self.assertFalse(d['dispatch_tool'])

    def test_abstention_is_terminal_at_every_stage(self):
        for n in range(1, 5):
            self.assertEqual(classify('{"kind":"abstain","text":"uncertain"}', n)['status'], 'ABSTAINED')

    def test_bad_counter_fails_closed(self):
        for n in (True, False, 0, 5, 1.0, '1', None):
            with self.assertRaises(ValueError):
                phase(n)


if __name__ == '__main__':
    unittest.main()
