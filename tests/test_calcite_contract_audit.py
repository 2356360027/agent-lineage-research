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
from scripts.verify_calcite_contract_audit import recorded_schedule, cost_evidence_valid, required_manifest_entries, json_decimal_text


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
            "querySchema": [{"name": column["name"]} for column in case["query_columns"]],
            "rewrittenSchema": [{"name": column["name"]} for column in case["query_columns"]],
            "viewSchema": [{"name": name} for name in case["view_columns"]],
            "comparison": {"numericNormalizedBagEqual": True, "typedBagEqual": True,
                           "sqlRowTypesEqualIgnoringNames": True},
            "classification": "SYNTHETIC_UNIT_TEST_REPORT",
            "rewrittenScanTables": ["MV"],
            "ruleProductions": [{"scanTables": ["MV"]}],
        }

    def assess(self, report, case_id="C1", state="base"):
        return audit.assess(report, self.cases[case_id], state,
                            self.config["fixtures"][state])

    def test_fixed_cost_schedule_has_both_modes_once_and_balanced_order(self):
        plan = audit.tasks(self.config, ["volcano_base", "volcano_mv"])
        self.assertEqual(len(plan), 48)
        self.assertEqual(len({(p["case"], p["state"], p["mode"]) for p in plan}), 48)
        self.assertEqual(Counter(p["mode"] for p in plan[::2]), {"volcano_base": 12, "volcano_mv": 12})
        design = {"version": "calcite-contract-audit-v3-cost-controls", "planner_modes": ["volcano_base", "volcano_mv"], "schedule": plan}
        self.assertEqual(recorded_schedule(design, self.config), plan)

    def test_schedule_rejects_duplicate_missing_and_wrong_label(self):
        for modes in [[], ["hep", "hep"], ["unknown"]]:
            with self.assertRaises(ValueError):
                audit.tasks(self.config, modes)
        plan = audit.tasks(self.config, ["volcano_base", "volcano_mv"])
        design = {"version": "calcite-contract-audit-v3-cost-controls", "planner_modes": ["volcano_base", "volcano_mv"], "schedule": plan}
        for bad in [plan[:-1], plan[:-1] + [plan[0]], None]:
            with self.assertRaises(ValueError):
                recorded_schedule({**design, "schedule": bad}, self.config)
        wrong = copy.deepcopy(design)
        wrong["schedule"][0]["label"] = "../invalid"
        with self.assertRaises(ValueError):
            recorded_schedule(wrong, self.config)

    def test_legacy_hep_schedule_remains_24(self):
        schedule = recorded_schedule({"version": "calcite-hep-contract-audit-v2"}, self.config)
        self.assertEqual(len(schedule), 24)
        self.assertEqual(schedule[0]["label"], "C1-base")

    def test_cost_mode_requires_actual_planner_evidence(self):
        cost = {"status": "AVAILABLE", "rows": 10, "cpu": 50, "io": 0, "infinite": False}
        for mode, count in [("volcano_base", 0), ("volcano_mv", 1)]:
            report = {"plannerMode": mode, "selectedPlanExecutionRel": "EnumerablePlan", "costPlanning": {
                "status": "PLANNED", "planner": "org.apache.calcite.plan.volcano.VolcanoPlanner",
                "program": "Programs.standard()", "registeredMaterializationCount": count,
                "materializationsEnabled": True, "freshClusterAndUnregisteredTreeVerified": True,
                "metadataResetBeforeStandardProgram": True, "selectedConvention": "ENUMERABLE",
                "selectedPlan": "EnumerablePlan",
                "selectedCost": copy.deepcopy(cost), "finalProgramCost": copy.deepcopy(cost)}}
            for validator in (audit.planner_evidence, cost_evidence_valid):
                self.assertTrue(validator(report, mode))
                for key in report["costPlanning"]:
                    if key == "selectedPlan" and validator is audit.planner_evidence:
                        continue  # Runtime guard is distinct from the stronger post-hoc verifier.
                    bad = copy.deepcopy(report)
                    del bad["costPlanning"][key]
                    self.assertFalse(validator(bad, mode), key)
                bad = copy.deepcopy(report)
                bad["costPlanning"]["registeredMaterializationCount"] = 1 - count
                self.assertFalse(validator(bad, mode))
                for value in (None, -1, "10", True):
                    bad = copy.deepcopy(report)
                    bad["costPlanning"]["selectedCost"]["rows"] = value
                    self.assertFalse(validator(bad, mode))
                self.assertFalse(validator(report, "hep"))
            wrong = copy.deepcopy(report)
            wrong["selectedPlanExecutionRel"] = "DifferentPlan"
            self.assertFalse(cost_evidence_valid(wrong, mode))
            del wrong["selectedPlanExecutionRel"]
            self.assertFalse(cost_evidence_valid(wrong, mode))
        self.assertTrue(cost_evidence_valid({}, None))
        self.assertTrue(audit.planner_evidence({"plannerMode": "hep"}, "hep"))

    def test_manifest_requires_all_raw_process_and_frozen_inputs(self):
        schedule = audit.tasks(self.config, ["volcano_base", "volcano_mv"])
        needed = required_manifest_entries({"source_hashes": {"a/source.py": "placeholder"}}, schedule)
        self.assertEqual(len(needed), 103)  # Six metadata + one source + 96 case/process artifacts.
        self.assertIn(str(Path("source/a/source.py")), needed)
        for row in schedule:
            self.assertIn(row["label"] + ".json", needed)
            self.assertIn(row["label"] + ".process.json", needed)

    def test_verifier_cli_serializes_exact_decimal_cost_without_float_loss(self):
        value = Decimal("0.10000000000000001")
        encoded = json.dumps({"cost": value}, default=json_decimal_text)
        self.assertEqual(json.loads(encoded), {"cost": "0.10000000000000001"})
        self.assertEqual(value, Decimal("0.10000000000000001"))
        for invalid in (Decimal("NaN"), Decimal("Infinity"), object()):
            with self.assertRaises(TypeError):
                json.dumps({"value": invalid}, default=json_decimal_text)

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
                    self.assertTrue(result["all_column_contracts_equal"])
                    self.assertTrue(result["contract_passed"])
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
        self.assertFalse(result["contract_passed"])

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
                self.assertFalse(result["contract_passed"])

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
        result = self.assess(report, "C1", "empty")
        self.assertFalse(result["fixture_matches"])
        self.assertFalse(result["contract_passed"])

    def test_empty_c4_reversed_named_columns_fail_despite_equal_empty_bags(self):
        report = self.report("C4", "empty")
        wrong = [{"name": name} for name in ("empid", "deptno", "s")]
        report["querySchema"] = copy.deepcopy(wrong)
        report["rewrittenSchema"] = copy.deepcopy(wrong)
        result = self.assess(report, "C4", "empty")
        self.assertTrue(result["all_oracles_equal"])
        self.assertTrue(result["comparison"]["typedBagEqual"])
        self.assertFalse(result["query_columns_match_contract"])
        self.assertFalse(result["rewritten_columns_match_contract"])
        self.assertTrue(result["view_columns_match_contract"])
        self.assertFalse(result["all_column_contracts_equal"])
        self.assertFalse(result["contract_passed"])

    def test_each_schema_field_is_required_and_preserves_column_order(self):
        for key in ("querySchema", "rewrittenSchema", "viewSchema"):
            for action in ("missing", "reversed"):
                with self.subTest(field=key, action=action):
                    report = self.report("C4", "empty")
                    if action == "missing":
                        del report[key]
                    else:
                        report[key] = list(reversed(report[key]))
                    result = self.assess(report, "C4", "empty")
                    self.assertTrue(result["all_oracles_equal"])
                    self.assertFalse(result["all_column_contracts_equal"])
                    self.assertFalse(result["contract_passed"])

    def test_required_result_identity_and_comparison_fields_cannot_be_missing(self):
        for key in ("caseId", "state", "baseRows", "originalRows", "rewrittenRows",
                    "materializedViewRows", "comparison"):
            with self.subTest(field=key):
                report = self.report("C1", "empty")
                del report[key]
                self.assertFalse(self.assess(report, "C1", "empty")["contract_passed"])

    def test_typed_bag_and_sql_types_need_explicit_true(self):
        for key in ("typedBagEqual", "sqlRowTypesEqualIgnoringNames"):
            for value in (False, None, "true", 1, "missing"):
                with self.subTest(field=key, value=value):
                    report = self.report()
                    if value == "missing":
                        del report["comparison"][key]
                    else:
                        report["comparison"][key] = value
                    result = self.assess(report)
                    self.assertTrue(result["all_oracles_equal"])
                    self.assertTrue(result["all_column_contracts_equal"])
                    self.assertFalse(result["contract_passed"])

    def test_correct_base_fallback_is_not_penalized_for_no_mv_candidate(self):
        report = self.report("C2")
        report["rewrittenScanTables"] = ["emps"]
        report["ruleProductions"] = []
        report["classification"] = {"usesMaterializedViewAtExecution": False,
                                    "readsBaseAtExecution": True}
        result = self.assess(report, "C2")
        self.assertFalse(result["produced_mv_candidate"])
        self.assertTrue(result["contract_passed"])

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
