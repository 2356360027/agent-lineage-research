"""Synthetic planner boundaries only, not research observations."""
import unittest
from scripts.audit_staged_routes_offline import scripted_action, SCOPED_SQL_234


class RouteAuditTests(unittest.TestCase):
    def test_direct_recompute_and_answer_requires_complete_rows(self):
        self.assertEqual(scripted_action(0,0,"SELECT 1",{}),{"kind":"run_sql","sql":"SELECT 1"})
        self.assertEqual(scripted_action(0,1,"SELECT 1",{"complete":False})["kind"],"abstain")
        self.assertEqual(scripted_action(0,1,"SELECT 1",{"complete":True,"rows":[]})["text"],"[]")

    def test_last_slot_recompute_and_after_horizon_attempt(self):
        self.assertEqual(scripted_action(19,18,"SELECT 1",{})["kind"],"read_cache")
        self.assertEqual(scripted_action(19,19,"SELECT 1",{})["kind"],"run_sql")
        self.assertEqual(scripted_action(20,19,"SELECT 1",{})["kind"],"read_cache")
        self.assertEqual(scripted_action(20,20,"SELECT 1",{})["kind"],"run_sql")

    def test_nonfixed_delays_rejected_and_scope_explicit(self):
        for delay in (1,True,-1,21):
            with self.assertRaises(ValueError): scripted_action(delay,0,"SELECT 1",{})
        self.assertIn("b.molecule_id='TR009'",SCOPED_SQL_234)
        self.assertIn("COUNT(DISTINCT c.bond_id)",SCOPED_SQL_234)


if __name__ == "__main__": unittest.main()
