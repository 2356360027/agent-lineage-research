"""Software fixtures only: no dataset downloads, model calls, or QA evidence."""

import copy
import hashlib
import json
import unittest

from runtime.natural_qa_data import (
    OFFICIAL_METRIC_NAMES, adapt_hotpot_row, answer_metrics, gold_view,
    normalize_answer, prediction_view, score_prediction, seeded_order,
    support_metrics,
)


def example():
    return {
        "_id": "fixture-1", "question": "Which city is mentioned?",
        "context": [
            ["Document A", ["A traveller was born in Paris.", "She moved to Rome.", ""]],
            ["Document B", ["A traveller was born in Paris."]],
        ],
        "answer": "Paris", "supporting_facts": [["Document A", 0], ["Document A", 1]],
        "type": "bridge", "level": "fixture", "hidden_label": "never project this",
    }


class NaturalQADataTests(unittest.TestCase):
    def test_projection_has_exact_whitelist(self):
        row = example()
        view = prediction_view(row)
        self.assertEqual(set(view), {"id", "question", "units"})
        self.assertEqual(view["id"], row["_id"])
        self.assertEqual(view["question"], row["question"])
        for unit in view["units"]:
            self.assertEqual(set(unit), {"id", "title", "sentence_index", "text", "text_sha256"})
            self.assertEqual(unit["text_sha256"], hashlib.sha256(unit["text"].encode()).hexdigest())
        self.assertNotIn("hidden_label", json.dumps(view))
        self.assertNotIn("supporting_facts", json.dumps(view))

    def test_gold_swap_cannot_change_prediction_projection(self):
        first = example()
        second = copy.deepcopy(first)
        second["answer"] = "Rome"
        second["supporting_facts"] = [["Document B", 0]]
        first_view, first_gold = adapt_hotpot_row(first)
        second_view, second_gold = adapt_hotpot_row(second)
        self.assertEqual(first_view, second_view)
        self.assertNotEqual(first_gold, second_gold)
        self.assertEqual(set(first_gold), {"answer", "supporting_facts"})

    def test_prediction_projection_does_not_even_validate_gold(self):
        row = example()
        expected = prediction_view(row)
        row["answer"] = object()
        row["supporting_facts"] = "malformed gold"
        self.assertEqual(prediction_view(row), expected)
        del row["answer"]
        del row["supporting_facts"]
        self.assertEqual(prediction_view(row), expected)
        with self.assertRaises(ValueError):
            adapt_hotpot_row(row)

    def test_prediction_projection_never_reads_label_keys(self):
        class GuardedRow(dict):
            def get(self, key, default=None):
                if key in {"answer", "supporting_facts"}:
                    raise AssertionError("prediction attempted to read a label")
                return super().get(key, default)

            def __getitem__(self, key):
                if key in {"answer", "supporting_facts"}:
                    raise AssertionError("prediction attempted to read a label")
                return super().__getitem__(key)

        self.assertEqual(prediction_view(GuardedRow(example())), prediction_view(example()))

    def test_same_document_distinct_sentences_and_blank_are_retained(self):
        units = prediction_view(example())["units"]
        self.assertEqual([unit["id"] for unit in units], ["d0s0", "d0s1", "d0s2", "d1s0"])
        self.assertEqual(units[2]["text"], "")
        self.assertEqual(units[2]["sentence_index"], 2)
        self.assertEqual(units[0]["text_sha256"], units[3]["text_sha256"])
        self.assertNotEqual(units[0]["id"], units[3]["id"])

    def test_no_mutable_aliases_to_source(self):
        row = example()
        before = copy.deepcopy(row)
        view, gold = adapt_hotpot_row(row)
        view["units"][0]["text"] = "changed view"
        gold["supporting_facts"][0][0] = "changed gold"
        self.assertEqual(row, before)

    def test_context_validation(self):
        for field, value in [("_id", 3), ("_id", ""), ("question", None),
                             ("question", " "), ("context", {}),
                             ("context", [["title"]]), ("context", [[1, []]]),
                             ("context", [["title", "text"]]),
                             ("context", [["title", [None]]]),
                             ("context", [["title", []], ["title", []]])]:
            with self.subTest(field=field, value=value):
                row = example()
                row[field] = value
                with self.assertRaises(ValueError):
                    prediction_view(row)
        with self.assertRaises(ValueError):
            prediction_view([])

    def test_gold_support_must_exist_and_have_valid_types(self):
        for facts in [None, {}, [["Missing", 0]], [["Document A", 9]],
                      [["Document A", -1]], [["Document A", True]],
                      [["Document A", "0"]], [["Document A"]]]:
            with self.subTest(facts=facts):
                row = example()
                row["supporting_facts"] = facts
                with self.assertRaises(ValueError):
                    adapt_hotpot_row(row)
        row = example()
        row["answer"] = False
        with self.assertRaises(ValueError):
            gold_view(row)

    def test_different_row_view_ids_rejected(self):
        view = prediction_view(example())
        view["id"] = "other"
        with self.assertRaises(ValueError):
            gold_view(example(), view)
        with self.assertRaises(ValueError):
            gold_view(example(), [])

    def test_seeded_order_uses_only_seed_and_id(self):
        rows = [{"_id": f"fixture-{index}", "answer": "hidden"} for index in range(8)]
        result = seeded_order(rows, 42)
        self.assertEqual(result, seeded_order(reversed(rows), 42))
        expected = sorted(rows, key=lambda row: hashlib.sha256(
            json.dumps([42, row["_id"]], separators=(",", ":")).encode()).digest())
        self.assertEqual(result, expected)
        altered = [{"_id": row["_id"], "supporting_facts": object()} for row in rows]
        self.assertEqual([row["_id"] for row in result],
                         [row["_id"] for row in seeded_order(altered, 42)])
        projected = [{"id": row["_id"]} for row in rows]
        self.assertEqual([row["_id"] for row in result],
                         [row["id"] for row in seeded_order(projected, 42)])
        self.assertNotEqual([row["_id"] for row in result],
                            [row["_id"] for row in seeded_order(rows, 43)])

    def test_seeded_order_rejects_ambiguous_or_duplicate_ids(self):
        for rows in [[{"_id": "x"}, {"_id": "x"}], [{"id": "x"}, {"_id": "x"}],
                     [{"_id": "x", "id": "y"}], [{"_id": 1}], [{}], [None]]:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    seeded_order(rows, 42)
        with self.assertRaises(ValueError):
            seeded_order([], True)

    def test_official_answer_normalization(self):
        self.assertEqual(normalize_answer("  The CAT's, an apple! A  "), "cats apple")
        self.assertEqual(answer_metrics(" THE Paris!!! ", "Paris"),
                         {"em": 1.0, "f1": 1.0, "prec": 1.0, "recall": 1.0})
        metrics = answer_metrics("red red blue", "red blue green")
        self.assertEqual(metrics["em"], 0.0)
        self.assertAlmostEqual(metrics["prec"], 2 / 3)
        self.assertAlmostEqual(metrics["recall"], 2 / 3)
        self.assertAlmostEqual(metrics["f1"], 2 / 3)

    def test_special_answers_cannot_receive_partial_token_credit(self):
        for predicted, gold in [("yes", "yes indeed"), ("yes indeed", "yes"),
                                ("no", "no way"), ("noanswer", "noanswer supplied"),
                                ("yes", "no")]:
            with self.subTest(predicted=predicted, gold=gold):
                self.assertEqual(answer_metrics(predicted, gold),
                                 {"em": 0.0, "f1": 0.0, "prec": 0.0, "recall": 0.0})
        self.assertEqual(answer_metrics("YES!", "yes")["f1"], 1.0)

    def test_empty_overlap_semantics_match_official_metrics(self):
        self.assertEqual(answer_metrics("", ""),
                         {"em": 1.0, "f1": 0.0, "prec": 0.0, "recall": 0.0})
        self.assertEqual(support_metrics([], []),
                         {"em": 1.0, "f1": 0.0, "prec": 0.0, "recall": 0.0})

    def test_support_is_coordinate_set_not_title_set(self):
        scores = support_metrics([["A", 0], ["A", 0], ["A", 1]],
                                 [["A", 0], ["A", 1], ["A", 1]])
        self.assertEqual(scores, {"em": 1.0, "f1": 1.0, "prec": 1.0, "recall": 1.0})
        partial = support_metrics([["A", 0]], [["A", 0], ["A", 1]])
        self.assertEqual(partial["recall"], 0.5)
        self.assertAlmostEqual(partial["f1"], 2 / 3)

    def test_scoring_maps_unit_ids_and_ignores_duplicate_citations(self):
        view, gold = adapt_hotpot_row(example())
        output = {"answer": "Paris", "citations": ["d0s0", "d0s1"]}
        scores = score_prediction(output, view, gold)
        self.assertTrue(scores["valid_output"])
        self.assertIsNone(scores["invalid_reason"])
        self.assertTrue(all(scores[name] == 1.0 for name in OFFICIAL_METRIC_NAMES))
        output["citations"] += ["d0s0", "d0s1"]
        self.assertEqual(score_prediction(output, view, gold), scores)

    def test_identical_text_in_another_document_is_not_same_coordinate(self):
        view, gold = adapt_hotpot_row(example())
        scores = score_prediction({"answer": "Paris", "citations": ["d1s0"]}, view, gold)
        self.assertEqual(scores["em"], 1.0)
        self.assertEqual(scores["sp_f1"], 0.0)
        self.assertEqual(scores["joint_f1"], 0.0)

    def test_joint_f1_is_not_product_of_component_f1(self):
        row = example()
        row["answer"] = "red blue"
        view, gold = adapt_hotpot_row(row)
        output = {"answer": "red", "citations": ["d0s0", "d0s1", "d1s0"]}
        scores = score_prediction(output, view, gold)
        self.assertAlmostEqual(scores["joint_prec"], 2 / 3)
        self.assertEqual(scores["joint_recall"], 0.5)
        self.assertAlmostEqual(scores["joint_f1"], 4 / 7)
        self.assertNotAlmostEqual(scores["joint_f1"], scores["f1"] * scores["sp_f1"])

    def test_invalid_model_outputs_are_explicit_all_zero(self):
        view, gold = adapt_hotpot_row(example())
        invalid_outputs = [None, "Paris", [], {}, {"answer": "Paris"},
                           {"answer": 1, "citations": []},
                           {"answer": "Paris", "citations": "d0s0"},
                           {"answer": "Paris", "citations": [None]},
                           {"answer": "Paris", "citations": [["d0s0"]]},
                           {"answer": "Paris", "citations": ["d0s0", "unknown"]},
                           {"answer": "Paris", "citations": [], "extra": True}]
        for output in invalid_outputs:
            with self.subTest(output=output):
                scores = score_prediction(output, view, gold)
                self.assertFalse(scores["valid_output"])
                self.assertTrue(scores["invalid_reason"])
                self.assertTrue(all(scores[name] == 0.0 for name in OFFICIAL_METRIC_NAMES))

    def test_empty_citations_are_valid_but_unsupported(self):
        view, gold = adapt_hotpot_row(example())
        scores = score_prediction({"answer": "Paris", "citations": []}, view, gold)
        self.assertTrue(scores["valid_output"])
        self.assertEqual(scores["em"], 1.0)
        self.assertEqual(scores["sp_em"], 0.0)
        self.assertEqual(scores["joint_em"], 0.0)

    def test_invalid_source_views_raise_not_silently_overwrite(self):
        view, gold = adapt_hotpot_row(example())
        bad_views = []
        duplicate_id = copy.deepcopy(view)
        duplicate_id["units"][1]["id"] = "d0s0"
        bad_views.append(duplicate_id)
        duplicate_coordinate = copy.deepcopy(view)
        duplicate_coordinate["units"][1]["sentence_index"] = 0
        bad_views.append(duplicate_coordinate)
        altered_text = copy.deepcopy(view)
        altered_text["units"][0]["text"] = "tampered"
        bad_views.append(altered_text)
        extra_label = copy.deepcopy(view)
        extra_label["answer"] = "Paris"
        bad_views.append(extra_label)
        for bad_view in bad_views:
            with self.subTest(view=bad_view):
                with self.assertRaises(ValueError):
                    score_prediction({"answer": "Paris", "citations": []}, bad_view, gold)


if __name__ == "__main__":
    unittest.main()
