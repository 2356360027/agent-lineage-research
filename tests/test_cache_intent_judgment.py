"""Mechanical software tests with hand-written, synthetic labels only.

These examples are not human judgments, research data, task approvals, SQL
executions, or evidence about a model. No files, databases, or network are used.
"""
from copy import deepcopy
from fractions import Fraction
from itertools import product
import unittest

from runtime import cache_intent_judgment as judgment


A = "A_incompatible_cache_with_correct_contract"
B = "B_program_mismatch_with_correct_contract"
C = "C_wrong_contract_aligned_program"
CH = "C_history_directed"
FINAL = "final_result_mismatch"
PRE = "pre_result_contract_contradiction"
OBSERVED = "observed_contract_contradiction"
MISUSE = "incompatible_cache_use"


def assessment(**changes):
    return {
        "request": "clear",
        "reference": "valid",
        "contract_vs_request": "match",
        "program_vs_contract": "match",
        "program_vs_request": "match",
        "contract_toward_history": "no",
        "cache_application": "not_used",
        "final_result": "match",
    } | changes


def link(**changes):
    return {
        "contract_id": "synthetic-contract-1",
        "program_id": "synthetic-program-1",
        "program_contract_id": "synthetic-contract-1",
        "contract_event": 2,
        "program_event": 3,
        "contract_observation": "pre_result",
        "execution_status": "complete",
    } | changes


def derive(labels=None, trace=None):
    return judgment.derive(assessment() if labels is None else labels,
                           link=link() if trace is None else trace)


class DerivationTests(unittest.TestCase):
    def test_aligned_clean_case_is_not_any_failure_or_semantic_approval(self):
        result = derive()
        self.assertEqual(result["eligibility"], "eligible_by_supplied_labels")
        for key in (A, B, C, CH, FINAL, PRE, OBSERVED, MISUSE):
            self.assertIs(result["indicators"][key], False)
        self.assertEqual(result["annotation_scope"], "MECHANICAL_RULES_ONLY_NOT_SEMANTIC_APPROVAL")
        self.assertIs(result["causal_effect_established"], False)

    def test_a_and_b_can_both_be_true_without_c(self):
        values = derive(assessment(program_vs_contract="contradiction",
                                   program_vs_request="contradiction",
                                   cache_application="incompatible_used"))["indicators"]
        self.assertIs(values[A], True)
        self.assertIs(values[B], True)
        self.assertIs(values[C], False)

    def test_wrong_contract_aligned_program_is_c_even_when_result_matches(self):
        values = derive(assessment(contract_vs_request="contradiction",
                                   program_vs_request="contradiction",
                                   contract_toward_history="yes",
                                   cache_application="incompatible_used",
                                   final_result="match"))["indicators"]
        self.assertIs(values[C], True)
        self.assertIs(values[CH], True)
        self.assertIs(values[A], False)
        self.assertIs(values[B], False)
        self.assertIs(values[FINAL], False)
        self.assertIs(values[PRE], True)
        self.assertIs(values[OBSERVED], True)
        self.assertIs(values[MISUSE], True)

    def test_c_is_incompatible_with_true_a_or_b_for_consistent_semantic_labels(self):
        labels = ("match", "contradiction", "unresolved")
        accepted = 0
        for c, pc, pr in product(labels, repeat=3):
            inconsistent = ((c == pc == "match" and pr == "contradiction") or
                            (c == pr == "match" and pc == "contradiction") or
                            (pc == pr == "match" and c == "contradiction"))
            if inconsistent:
                continue
            with self.subTest(contract=c, program_contract=pc, program_request=pr):
                values = derive(assessment(contract_vs_request=c, program_vs_contract=pc,
                                           program_vs_request=pr,
                                           cache_application="incompatible_used"))["indicators"]
                accepted += 1
                if values[C] is True:
                    self.assertIs(values[A], False)
                    self.assertIs(values[B], False)
                if values[A] is True or values[B] is True:
                    self.assertIs(values[C], False)
        self.assertEqual(accepted, 24)

    def test_unknown_conjuncts_remain_unknown_unless_another_is_false(self):
        values = derive(assessment(contract_vs_request="unresolved", program_vs_contract="unresolved",
                                   program_vs_request="unresolved", contract_toward_history="unresolved",
                                   cache_application="unresolved", final_result="unresolved"))["indicators"]
        for key in (A, B, C, CH, FINAL, PRE, OBSERVED, MISUSE):
            self.assertIsNone(values[key])
        values = derive(assessment(contract_vs_request="contradiction", program_vs_contract="unresolved",
                                   program_vs_request="unresolved", contract_toward_history="no",
                                   cache_application="unresolved"))["indicators"]
        self.assertIs(values[A], False)
        self.assertIs(values[B], False)
        self.assertIsNone(values[C])
        self.assertIs(values[CH], False)

    def test_history_direction_is_not_inferred_from_wrong_result_or_contract(self):
        for history_label, expected in (("yes", True), ("no", False),
                                        ("not_observed", None), ("unresolved", None)):
            with self.subTest(history_label=history_label):
                values = derive(assessment(contract_vs_request="contradiction",
                                           program_vs_request="contradiction",
                                           contract_toward_history=history_label))["indicators"]
                self.assertIs(values[C], True)
                self.assertIs(values[CH], expected)

    def test_naturally_unelicited_and_missing_contracts_stay_distinct(self):
        labels = assessment(contract_vs_request="not_observed", program_vs_contract="not_observed",
                            program_vs_request="contradiction", contract_toward_history="not_observed",
                            cache_application="incompatible_used", final_result="mismatch")
        outputs = []
        for stage in ("not_elicited", "missing"):
            trace = link(contract_id=None, contract_event=None, program_contract_id=None,
                         contract_observation=stage)
            result = derive(labels, trace)
            outputs.append(result)
            self.assertEqual(result["trace_link"]["contract_observation"], stage)
            for key in (A, B, C, CH, PRE):
                self.assertIsNone(result["indicators"][key])
            self.assertIs(result["indicators"][FINAL], True)
        self.assertNotEqual(outputs[0]["trace_link"], outputs[1]["trace_link"])

    def test_wrong_result_does_not_imply_c(self):
        values = derive(assessment(final_result="mismatch"))["indicators"]
        self.assertIs(values[FINAL], True)
        self.assertIs(values[C], False)
        self.assertIs(values[CH], False)

    def test_post_result_contract_before_new_program_can_be_c_but_not_pre_result(self):
        values = derive(assessment(contract_vs_request="contradiction", program_vs_request="contradiction"),
                        link(contract_observation="post_result"))["indicators"]
        self.assertIs(values[C], True)
        # The earlier/pre-result checkpoint was not observed by this label.
        # Post-result contradiction is not evidence that it was absent earlier.
        self.assertIsNone(values[PRE])
        self.assertIs(values[OBSERVED], True)

    def test_other_contract_same_time_or_later_contract_is_rejected(self):
        changes = [{"program_contract_id": "synthetic-other-contract"},
                   {"contract_event": 3}, {"contract_event": 4},
                   {"contract_event": 4, "contract_observation": "post_result"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                derive(trace=link(**change))

    def test_b_cannot_be_inferred_without_explicit_program_contract_link(self):
        with self.assertRaisesRegex(ValueError, "linkage"):
            derive(assessment(program_vs_contract="unresolved", program_vs_request="contradiction"),
                   link(program_contract_id=None))

    def test_unobserved_program_does_not_erase_observed_contract_or_invent_c(self):
        values = derive(assessment(contract_vs_request="contradiction", program_vs_contract="not_observed",
                                   program_vs_request="not_observed", final_result="not_observed"),
                        link(program_id=None, program_event=None, program_contract_id=None,
                             execution_status="not_executed"))["indicators"]
        self.assertIs(values[OBSERVED], True)
        self.assertIs(values[PRE], True)
        self.assertIsNone(values[C])
        self.assertIsNone(values[FINAL])

    def test_cache_misuse_does_not_require_a_correct_or_observed_contract(self):
        labels = assessment(contract_vs_request="not_observed", program_vs_contract="not_observed",
                            program_vs_request="unresolved", contract_toward_history="not_observed",
                            cache_application="incompatible_used")
        values = derive(labels, link(contract_id=None, contract_event=None, program_contract_id=None,
                                     contract_observation="not_elicited"))["indicators"]
        self.assertIs(values[MISUSE], True)
        self.assertIsNone(values[A])
        self.assertIsNone(values[C])

    def test_correct_contract_cannot_be_history_directed_on_a_discriminating_field(self):
        with self.assertRaises(ValueError):
            derive(assessment(contract_toward_history="yes"))

    def test_claimed_cache_use_requires_an_observed_action(self):
        for cache_label in ("compatible_used", "incompatible_used"):
            with self.subTest(cache_label=cache_label), self.assertRaises(ValueError):
                derive(assessment(program_vs_contract="not_observed", program_vs_request="not_observed",
                                   cache_application=cache_label),
                       link(program_id=None, program_event=None, program_contract_id=None,
                            execution_status="not_executed"))

    def test_not_executed_program_cannot_be_labeled_as_actual_cache_use(self):
        for cache_label in ("compatible_used", "incompatible_used"):
            with self.subTest(cache_label=cache_label), self.assertRaises(ValueError):
                derive(assessment(cache_application=cache_label),
                       link(execution_status="not_executed"))

    def test_unexecuted_later_program_can_align_with_wrong_contract_without_task_success(self):
        result = derive(assessment(contract_vs_request="contradiction",
                                   program_vs_contract="match",
                                   program_vs_request="contradiction",
                                   cache_application="not_used",
                                   final_result="not_observed"),
                        link(execution_status="not_executed"))
        self.assertIs(result["indicators"][C], True)
        self.assertIs(result["indicators"][MISUSE], False)
        self.assertIsNone(result["indicators"][FINAL])
        self.assertEqual(result["trace_link"]["execution_status"], "not_executed")
        self.assertEqual(result["annotation_scope"], "MECHANICAL_RULES_ONLY_NOT_SEMANTIC_APPROVAL")
        self.assertIs(result["causal_effect_established"], False)
        self.assertNotIn("task_success", result)

    def test_definitive_program_contract_labels_require_explicit_link(self):
        for pc in ("match", "contradiction"):
            with self.subTest(pc=pc), self.assertRaises(ValueError):
                derive(assessment(program_vs_contract=pc), link(program_contract_id=None))

    def test_transitively_inconsistent_labels_require_readjudication(self):
        for c, pc, pr in (("match", "match", "contradiction"),
                          ("match", "contradiction", "match"),
                          ("contradiction", "match", "match")):
            with self.subTest(labels=(c, pc, pr)), self.assertRaisesRegex(ValueError, "Inconsistent"):
                derive(assessment(contract_vs_request=c, program_vs_contract=pc, program_vs_request=pr))

    def test_d_e_known_exclusions_do_not_turn_abc_into_zero(self):
        for request, reference, d, e in (("ambiguous", "valid", True, False),
                                       ("clear", "defective", False, True),
                                       ("ambiguous", "defective", True, True)):
            with self.subTest(request=request, reference=reference):
                result = derive(assessment(request=request, reference=reference))
                self.assertEqual(result["eligibility"], "excluded_by_supplied_labels")
                self.assertIs(result["D_ambiguous_request"], d)
                self.assertIs(result["E_defective_reference"], e)
                self.assertTrue(all(value is None for value in result["indicators"].values()))

    def test_unresolved_d_e_are_not_recorded_as_false(self):
        for request, reference, d, e in (("unresolved", "valid", None, False),
                                       ("clear", "unresolved", False, None),
                                       ("unresolved", "unresolved", None, None)):
            with self.subTest(request=request, reference=reference):
                result = derive(assessment(request=request, reference=reference))
                self.assertEqual(result["eligibility"], "unresolved")
                self.assertIs(result["D_ambiguous_request"], d)
                self.assertIs(result["E_defective_reference"], e)
                self.assertTrue(all(value is None for value in result["indicators"].values()))

    def test_mixed_exclusion_and_unknown_preserves_unknown_dimension(self):
        result = derive(assessment(request="unresolved", reference="defective"))
        self.assertEqual(result["eligibility"], "excluded_by_supplied_labels")
        self.assertIsNone(result["D_ambiguous_request"])
        self.assertIs(result["E_defective_reference"], True)

    def test_missing_or_inferred_contract_artifacts_are_rejected(self):
        invalid = [link(contract_id=None), link(contract_event=None),
                   link(contract_observation="not_elicited"),
                   link(program_id=None), link(program_event=None),
                   link(contract_event=True), link(program_event=-1)]
        for trace in invalid:
            with self.subTest(trace=trace), self.assertRaises(ValueError):
                derive(trace=trace)
        with self.assertRaises(ValueError):
            derive(assessment(contract_vs_request="not_observed"))

    def test_assessment_and_trace_require_exact_fields_and_valid_labels(self):
        invalid_labels = [assessment(extra="value"), assessment(request=True),
                          assessment(cache_application="assumed_compatible"), []]
        missing = assessment(); del missing["final_result"]; invalid_labels.append(missing)
        for labels in invalid_labels:
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                derive(labels)
        invalid_links = [None, {}, link(extra="value"), link(program_id=" "),
                         link(contract_id="x" * 161), link(execution_status="success"),
                         link(contract_observation=[]), link(execution_status={})]
        for trace in invalid_links:
            with self.subTest(trace=trace), self.assertRaises(ValueError):
                judgment.derive(assessment(), link=trace)

    def test_inputs_and_returned_link_are_not_mutated_or_shared(self):
        labels, trace = assessment(), link()
        before = deepcopy((labels, trace))
        result = derive(labels, trace)
        self.assertEqual((labels, trace), before)
        result["trace_link"]["contract_id"] = "changed-only-in-return-value"
        self.assertEqual((labels, trace), before)


class PairedBoundsTests(unittest.TestCase):
    def bounds(self, rows, ids):
        return judgment.paired_bounds(rows, expected_pair_ids=ids)

    def test_all_nine_three_valued_single_pairs_have_exact_missingness_bounds(self):
        expected = {
            (False, False): (0, 0), (False, True): (1, 1), (False, None): (0, 1),
            (True, False): (-1, -1), (True, True): (0, 0), (True, None): (-1, 0),
            (None, False): (-1, 0), (None, True): (0, 1), (None, None): (-1, 1),
        }
        for (a, b), (lower, upper) in expected.items():
            with self.subTest(first=a, second=b):
                result = self.bounds([{"pair_id": "synthetic-pair", "first": a, "second": b}],
                                     ["synthetic-pair"])
                self.assertEqual(Fraction(result["difference_lower"]), lower)
                self.assertEqual(Fraction(result["difference_upper"]), upper)
                self.assertEqual(result["first_known"], int(a is not None))
                self.assertEqual(result["second_known"], int(b is not None))
                complete = a is not None and b is not None
                self.assertEqual(result["complete_pairs"], int(complete))
                self.assertEqual(result["incomplete_pairs"], int(not complete))
                self.assertEqual(result["complete_case_descriptive_difference"],
                                 str(int(b) - int(a)) if complete else None)
                self.assertIs(result["confidence_interval"], False)
                self.assertIs(result["causal_effect_established"], False)

    def test_equal_weighted_full_grid_and_complete_case_summary_are_distinct(self):
        rows = [{"pair_id": f"synthetic-{i}", "first": a, "second": b}
                for i, (a, b) in enumerate(product((False, True, None), repeat=2))]
        result = self.bounds(rows, [row["pair_id"] for row in rows])
        self.assertEqual(result["planned_pairs_supplied"], 9)
        self.assertEqual((result["difference_lower"], result["difference_upper"]), ("-1/3", "1/3"))
        self.assertEqual((result["first_known"], result["second_known"]), (6, 6))
        self.assertEqual((result["complete_pairs"], result["incomplete_pairs"]), (4, 5))
        self.assertEqual(result["complete_case_descriptive_difference"], "0")

    def test_omitted_record_is_two_unknown_labels_not_removed_denominator(self):
        rows = [{"pair_id": "observed", "first": False, "second": True}]
        result = self.bounds(rows, ["observed", "missing"])
        self.assertEqual(result["planned_pairs_supplied"], 2)
        self.assertEqual((result["difference_lower"], result["difference_upper"]), ("0", "1"))
        self.assertEqual((result["complete_pairs"], result["incomplete_pairs"]), (1, 1))
        self.assertEqual(result["complete_case_descriptive_difference"], "1")
        self.assertEqual(result["missing_pair_records"], ["missing"])
        explicit = self.bounds(rows + [{"pair_id": "missing", "first": None, "second": None}],
                               ["observed", "missing"])
        for key in ("difference_lower", "difference_upper", "complete_pairs", "incomplete_pairs",
                    "first_known", "second_known", "complete_case_descriptive_difference"):
            self.assertEqual(result[key], explicit[key])

    def test_all_records_missing_retains_full_plan_and_maximal_bounds(self):
        result = self.bounds([], ["missing-1", "missing-2"])
        self.assertEqual(result["planned_pairs_supplied"], 2)
        self.assertEqual((result["difference_lower"], result["difference_upper"]), ("-1", "1"))
        self.assertEqual((result["complete_pairs"], result["incomplete_pairs"]), (0, 2))
        self.assertIsNone(result["complete_case_descriptive_difference"])
        self.assertEqual(result["missing_pair_records"], ["missing-1", "missing-2"])

    def test_unexpected_or_duplicate_record_ids_are_rejected(self):
        row = {"pair_id": "planned", "first": False, "second": True}
        for rows in ([row, deepcopy(row)], [row | {"pair_id": "unplanned"}],
                     [row | {"pair_id": " "}], [row | {"pair_id": 1}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.bounds(rows, ["planned"])

    def test_non_boolean_labels_cannot_be_silently_coerced(self):
        for arm, value in product(("first", "second"), (0, 1, "false", "True", [], {}, 0.0)):
            with self.subTest(arm=arm, value=value), self.assertRaises(ValueError):
                self.bounds([{"pair_id": "planned", "first": False, "second": True} | {arm: value}],
                            ["planned"])

    def test_bad_manifest_shapes_and_ids_are_rejected(self):
        for ids in ([], ["dup", "dup"], [""], [None], [True], "planned", None):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.bounds([], ids)
        for rows in (None, (), {}, [{"pair_id": "planned", "first": False}],
                     [{"pair_id": "planned", "first": False, "second": True, "extra": 1}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.bounds(rows, ["planned"])

    def test_explicit_plan_is_required_and_arguments_are_not_mutated(self):
        rows = [{"pair_id": "synthetic", "first": None, "second": True}]
        ids = ["synthetic", "missing"]
        before = deepcopy((rows, ids))
        self.bounds(rows, ids)
        self.assertEqual((rows, ids), before)
        with self.assertRaises(TypeError):
            judgment.paired_bounds(rows)


if __name__ == "__main__":
    unittest.main()
