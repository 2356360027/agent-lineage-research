import unittest
from scripts.audit_amendment_semantics import expected_groups, rows_match


class Tests(unittest.TestCase):
    def test_ties_leave_gaps_and_row_order_free(self):
        rows = expected_groups([(1,'Gold'),(2,'Blue'),(3,'Red')], [(1,'Marvel Comics')],
            [(i+1, c, 1) for i,c in enumerate([1,1,2,2,3])])
        expected = [('Gold',2,1),('Blue',2,1),('Red',1,3)]
        self.assertTrue(rows_match(expected, rows[::-1]))
        self.assertFalse(rows_match(expected, [('Gold',2,1),('Blue',2,1),('Red',1,2)]))

    def test_names_case_null_and_placeholder(self):
        colours = [(1,'Gold'),(2,'Gold'),(3,'gold'),(4,None),(5,'-'),(6,'Unused')]
        rows = expected_groups(colours, [(1,'Marvel Comics'),(2,'marvel comics')],
            [(1,1,1),(2,2,1),(3,3,1),(4,4,1),(5,5,1),(6,None,1),(7,999,1),(8,1,2)])
        self.assertTrue(rows_match([('Gold',2,1),('gold',1,2),(None,1,2),('-',1,2)], rows))

    def test_empty(self):
        self.assertEqual(expected_groups([],[],[]), [])

    def test_invalid_keys_not_silently_collapsed(self):
        for colours in ([(1,'Gold'),(1,'Blue')], [(None,'Gold')]):
            with self.assertRaises(ValueError): expected_groups(colours, [], [])

    def test_duplicate_output_and_truncation_detected(self):
        expected = [('Gold',2,1),('Blue',1,2)]
        self.assertFalse(rows_match(expected, expected[:1]))
        self.assertFalse(rows_match(expected, expected+expected[:1]))

    def test_wrong_value_type_not_equated(self):
        self.assertFalse(rows_match([('Gold',1,1)], [('Gold',True,1)]))


if __name__ == '__main__': unittest.main()
