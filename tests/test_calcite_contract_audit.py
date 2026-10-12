"""Unit checks for the audit/evaluator; never launch Java or a production batch."""
from __future__ import annotations

from collections import Counter
import copy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from runtime import calcite_contract_audit as audit


def encoded_rows(rows):
    """Synthetic report cells for tests, not real Calcite execution records."""
    return [[{"javaType": "null" if value is None else "unit-test-number",
              "value": value, "numericCanonical": None} for value in row]
            for row in rows]


class CalciteContractAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "configs/calcite_contracts_v1.json"
        cls.config = json.loads(path.read_text(encoding="utf-8"))
        cls.cases = {case["id"]: case for case in cls.config["cases"]}

    def report(self, case_id="C1", state="base"):
        case = self.cases[case_id]
        return {
            "caseId": case_id,
            "state": state,
            "status": "EXECUTED_EQUAL",
            "baseRows": encoded_rows(self.config["fixtures"][state]["rows"]),
            "originalRows": encoded_rows(case["expected_query_rows"][state]),
            "rewrittenRows": encoded_rows(case["expected_query_rows"][state]),
            "materializedViewRows": encoded_rows(case["expected_view_rows"][state]),
            "comparison": {"numericNormalizedBagEqual": True},
            "classification": "SYNTHETIC_UNIT_TEST_REPORT",
            "rewrittenScanTables": ["MV"],
            "ruleProductions": [{"scanTables": ["MV"]}],
        }

    def assess(self, report, case_id="C1", state="base"):
        return audit.assess(report, self.cases[case_id], state,
                            self.config["fixtures"][state])

    def test_bag_order_does_not_remove_duplicates(self):
        self.assertEqual(audit.bag([[1], [2], [1]]), audit.bag([[2], [1], [1]]))
        self.assertNotEqual(audit.bag([[1], [2], [1]]), audit.bag([[1], [2]]))
        self.assertNotEqual(audit.bag([[None], [None]]), audit.bag([[None]]))

    def test_bag_preserves_null_column_position_and_non_numbers(self):
        self.assertNotEqual(audit.bag([[None]]), audit.bag([[0]]))
        self.assertNotEqual(audit.bag([[None]]), audit.bag([["null"]]))
        self.assertNotEqual(audit.bag([[1]]), audit.bag([["1"]]))
        self.assertNotEqual(audit.bag([[1]]), audit.bag([[True]]))
        self.assertNotEqual(audit.bag([[None, 1]]), audit.bag([[1, None]]))
        self.assertNotEqual(audit.bag([]), audit.bag([[None]]))

    def test_numeric_comparison_is_exact_not_a_blanket_tolerance(self):
        self.assertEqual(audit.bag([[180]]), audit.bag([[180.0]]))
        self.assertEqual(audit.bag([[180]]), audit.bag([[Decimal("180.000")]]))
        self.assertNotEqual(audit.bag([[180]]), audit.bag([[Decimal("180.000001")]]))
        self.assertNotEqual(audit.bag([[Decimal("0.1")]]),
                            audit.bag([[Decimal("0.10000000000000001")]]))

    def test_decoded_preserves_values_and_does_not_mutate_metadata(self):
        rows = encoded_rows([[None, 300], [None, 300], [0, 5]])
        before = copy.deepcopy(rows)
        self.assertEqual(audit.decoded(rows), [[None, 300], [None, 300], [0, 5]])
        self.assertEqual(rows, before)

    def test_correct_synthetic_reports_pass_all_24_combinations(self):
        count = 0
        for case_id in self.cases:
            for state in self.config["fixtures"]:
                with self.subTest(case=case_id, state=state):
                    result = self.assess(self.report(case_id, state), case_id, state)
                    self.assertTrue(result["identity_matches"])
                    self.assertTrue(result["fixture_matches"])
                    self.assertTrue(result["all_oracles_equal"])
                    self.assertTrue(result["produced_mv_candidate"])
                    count += 1
        self.assertEqual(count, 24)

    def test_matching_but_wrong_original_and_rewrite_do_not_pass_oracle(self):
        report = self.report("C7")
        # Common-mode error: COUNT(non-NULL) is 7, COUNT(DISTINCT) must be 5.
        report["originalRows"] = encoded_rows([[7]])
        report["rewrittenRows"] = encoded_rows([[7]])
        result = self.assess(report, "C7")
        self.assertEqual(result["reported_status"], "EXECUTED_EQUAL")
        self.assertTrue(result["comparison"]["numericNormalizedBagEqual"])
        self.assertFalse(result["originalRows_oracle_equal"])
        self.assertFalse(result["rewrittenRows_oracle_equal"])
        self.assertTrue(result["materializedViewRows_oracle_equal"])
        self.assertFalse(result["all_oracles_equal"])

    def test_wrong_materialization_is_detected_even_when_query_results_match(self):
        report = self.report("C7")
        report["materializedViewRows"] = encoded_rows([[10, 2], [20, 3], [30, 1], [None, 1]])
        result = self.assess(report, "C7")
        self.assertTrue(result["originalRows_oracle_equal"])
        self.assertTrue(result["rewrittenRows_oracle_equal"])
        self.assertFalse(result["materializedViewRows_oracle_equal"])
        self.assertFalse(result["all_oracles_equal"])

    def test_duplicate_loss_is_detected(self):
        report = self.report("C1")
        report["rewrittenRows"] = encoded_rows([[300], [0]])
        result = self.assess(report, "C1")
        self.assertFalse(result["rewrittenRows_oracle_equal"])
        self.assertFalse(result["all_oracles_equal"])

    def test_empty_global_aggregates_require_one_row(self):
        expected = {"C5": [[0, 0, None]], "C6": [[None]], "C7": [[0]]}
        for case_id, rows in expected.items():
            with self.subTest(case=case_id):
                self.assertEqual(self.cases[case_id]["expected_query_rows"]["empty"], rows)
                report = self.report(case_id, "empty")
                report["originalRows"] = []
                report["rewrittenRows"] = []
                result = self.assess(report, case_id, "empty")
                self.assertFalse(result["originalRows_oracle_equal"])
                self.assertFalse(result["rewrittenRows_oracle_equal"])
                self.assertFalse(result["all_oracles_equal"])

    def test_absent_result_field_is_not_an_empty_success(self):
        report = self.report("C1", "empty")
        del report["rewrittenRows"]
        result = self.assess(report, "C1", "empty")
        self.assertFalse(result["rewrittenRows_oracle_equal"])
        self.assertFalse(result["all_oracles_equal"])

    def test_case_and_state_identity_mismatches_are_flagged(self):
        for key, wrong in [("caseId", "C8"), ("state", "empty")]:
            with self.subTest(field=key):
                report = self.report()
                report[key] = wrong
                result = self.assess(report)
                self.assertFalse(result["identity_matches"])
                self.assertTrue(result["fixture_matches"])

    def test_fixture_mismatch_and_duplicate_fixture_rows_are_flagged(self):
        report = self.report()
        report["baseRows"] = report["baseRows"][:-1]
        self.assertFalse(self.assess(report)["fixture_matches"])
        report = self.report()
        report["baseRows"].append(copy.deepcopy(report["baseRows"][0]))
        self.assertFalse(self.assess(report)["fixture_matches"])

    def test_absent_fixture_is_not_proof_of_empty_fixture(self):
        report = self.report("C1", "empty")
        del report["baseRows"]
        self.assertFalse(self.assess(report, "C1", "empty")["fixture_matches"])

    def test_mv_candidate_flag_uses_reported_scan_tables(self):
        report = self.report()
        report["ruleProductions"] = [{"scanTables": ["emps"]}]
        self.assertFalse(self.assess(report)["produced_mv_candidate"])

    def test_48_independent_in_memory_sql_oracle_checks(self):
        checks = audit.check_oracle(copy.deepcopy(self.config))
        self.assertEqual(len(checks), 48)
        self.assertTrue(all(check["equal"] for check in checks))
        self.assertEqual(Counter(check["state"] for check in checks),
                         {"base": 16, "empty": 16, "all_salary_null": 16})
        self.assertEqual(Counter(check["kind"] for check in checks),
                         {"query_rows": 24, "view_rows": 24})
        self.assertEqual(len({(c["case"], c["state"], c["kind"]) for c in checks}), 48)

    def test_oracle_rejects_mutated_expected_result(self):
        wrong = copy.deepcopy(self.config)
        next(case for case in wrong["cases"] if case["id"] == "C7")["expected_query_rows"]["base"] = [[7]]
        with self.assertRaisesRegex(ValueError, "Oracle inconsistency: C7 base query_rows"):
            audit.check_oracle(wrong)

    def test_save_refuses_overwrite_and_preserves_original_bytes(self):
        with tempfile.TemporaryDirectory(prefix="calcite-audit-unit-") as folder:
            path = Path(folder) / "record.json"
            audit.save(path, {"original": [None, 1]})
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                audit.save(path, {"replacement": True})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"original": [None, 1]})


if __name__ == "__main__":
    unittest.main()
