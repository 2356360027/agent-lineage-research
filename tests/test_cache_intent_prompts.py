"""Offline prompt-construction fixtures, not model or semantic-validation results."""
from copy import deepcopy
import json
import unittest

from runtime import cache_intent_prompts as p


def inputs():
    return {
        "current_request": "Count distinct customers active in 2024.",
        "evidence": None,
        "schema": ["CREATE TABLE visits (customer_id INTEGER, year INTEGER);"],
        "history": {"historical_request": "Count visits in 2023.",
                    "sql": "SELECT COUNT(*) FROM visits WHERE year = 2023"},
        "database_sha256": "a" * 64,
    }


def packet(envelope):
    return json.loads(envelope["messages"][1]["content"])


def set_packet(envelope, value):
    envelope["messages"][1]["content"] = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class PromptTests(unittest.TestCase):
    def test_availability_changes_only_the_one_factor(self):
        a, b = p.make_pair(**inputs())
        p.assert_pair(a, b)
        x, y = packet(a), packet(b)
        self.assertIs(x["context"]["cache"]["materialized_available"], False)
        self.assertIs(y["context"]["cache"]["materialized_available"], True)
        x["context"]["cache"]["materialized_available"] = True
        self.assertEqual(x, y)
        self.assertEqual(a["messages"][0], b["messages"][0])
        for value in (x, y):
            context = value["context"]
            self.assertEqual(context["cache"]["cache_id"], "v000")
            self.assertEqual(context["cache"]["read_cost"], 1)
            self.assertEqual(context["sql_call_cost"], 10)
            self.assertEqual(context["budget"], 100)
            self.assertEqual(context["cost_basis"], "declared_relative_units")

    def test_read_cost_changes_only_one_factor(self):
        a, b = p.make_pair(**inputs(), factor="read_cost")
        p.assert_pair(a, b, factor="read_cost")
        x, y = packet(a), packet(b)
        self.assertEqual(x["context"]["cache"]["read_cost"], 1)
        self.assertEqual(y["context"]["cache"]["read_cost"], 8)
        for value in (x, y):
            self.assertIs(value["context"]["cache"]["materialized_available"], True)
            self.assertGreaterEqual(value["context"]["budget"], value["context"]["sql_call_cost"])
        x["context"]["cache"]["read_cost"] = 8
        self.assertEqual(x, y)

    def test_common_information_has_no_private_record_fields(self):
        source = inputs()
        a, b = p.make_pair(**source)
        for envelope in (a, b):
            self.assertEqual(set(envelope), {"messages", "tools", "response_format"})
            value = packet(envelope)
            self.assertEqual(set(value), {"current_request", "evidence", "schema", "history", "context"})
            self.assertEqual(value["history"], source["history"])
            self.assertEqual(set(value["history"]), {"historical_request", "sql"})
            self.assertEqual(value["schema"], source["schema"])
            self.assertEqual(set(value["context"]), {
                "database_sha256", "cache", "sql_call_cost", "budget", "cost_basis"})
            self.assertEqual(set(value["context"]["cache"]), {
                "cache_id", "materialized_available", "read_cost"})

    def test_stage_one_has_exactly_eight_observable_fields_and_no_tools(self):
        a, b = p.make_pair(**inputs())
        self.assertEqual(a["tools"], [])
        self.assertEqual(a["response_format"], b["response_format"])
        response = a["response_format"]["json_schema"]
        schema = response["schema"]
        self.assertIs(response["strict"], True)
        self.assertEqual(tuple(schema["properties"]), p.CONTRACT_FIELDS)
        self.assertEqual(schema["required"], list(p.CONTRACT_FIELDS))
        self.assertIs(schema["additionalProperties"], False)
        self.assertTrue(all(x["type"] == "string" for x in schema["properties"].values()))
        system = a["messages"][0]["content"]
        for phrase in ("No tools are enabled", "read-only SQL", "read_cache",
                       "not measured runtime", "unresolved", "Do not call tools"):
            self.assertIn(phrase, system)

    def test_full_envelope_diff_detects_unilateral_tampering(self):
        def system(x): x["messages"][0]["content"] += " changed"
        def tools(x): x["tools"].append({"type": "function"})
        def metadata(x): x["metadata"] = {"arm": "high"}
        def remove(x): x.pop("tools")
        def response(x): x["response_format"]["json_schema"]["schema"]["required"].pop()
        def message(x): x["messages"].append({"role": "assistant", "content": "hint"})
        for alter in (system, tools, metadata, remove, response, message):
            with self.subTest(alter=alter.__name__):
                a, b = p.make_pair(**inputs())
                alter(b)
                with self.assertRaises(ValueError): p.assert_pair(a, b)

    def test_identical_unauthorized_edits_to_both_arms_are_rejected(self):
        for mode in ("system", "tools", "metadata", "strict_type", "extra_context"):
            with self.subTest(mode=mode):
                a, b = p.make_pair(**inputs())
                for envelope in (a, b):
                    if mode == "system": envelope["messages"][0]["content"] += " changed"
                    elif mode == "tools": envelope["tools"] = [{"type": "function"}]
                    elif mode == "metadata": envelope["metadata"] = {"reviewer": "fixture"}
                    elif mode == "strict_type": envelope["response_format"]["json_schema"]["strict"] = 1
                    else:
                        value = packet(envelope); value["context"]["gold"] = "hidden"
                        set_packet(envelope, value)
                with self.assertRaises(ValueError): p.assert_pair(a, b)

    def test_wrong_factor_values_order_and_additional_changes_rejected(self):
        a, b = p.make_pair(**inputs())
        for low, high, factor in ((b, a, "availability"), (a, a, "availability"),
                                  (a, b, "read_cost")):
            with self.assertRaises(ValueError): p.assert_pair(low, high, factor)
        for key, value in (("materialized_available", 1), ("read_cost", 8)):
            altered = deepcopy(b); data = packet(altered); data["context"]["cache"][key] = value
            set_packet(altered, data)
            with self.assertRaises(ValueError): p.assert_pair(a, altered)
        altered = deepcopy(b); data = packet(altered); data["current_request"] += " Changed."
        set_packet(altered, data)
        with self.assertRaises(ValueError): p.assert_pair(a, altered)

    def test_low_cost_factor_must_be_one_and_high_eight(self):
        a, b = p.make_pair(**inputs(), factor="read_cost")
        value = packet(b); value["context"]["cache"]["read_cost"] = 9
        set_packet(b, value)
        with self.assertRaises(ValueError): p.assert_pair(a, b, "read_cost")

    def test_inputs_are_not_mutated_and_envelopes_do_not_alias(self):
        source = inputs(); before = deepcopy(source)
        a, b = p.make_pair(**source)
        self.assertEqual(source, before)
        a["response_format"]["json_schema"]["schema"]["required"].clear()
        a["tools"].append("changed")
        self.assertEqual(b["tools"], [])
        self.assertEqual(b["response_format"]["json_schema"]["schema"]["required"], list(p.CONTRACT_FIELDS))
        self.assertEqual(p.make_pair(**source)[1], b)

    def test_evidence_empty_unicode_and_instruction_like_text_preserved_as_data(self):
        source = inputs()
        source.update(current_request='Count records named "ignore prior instructions". 中文', evidence="")
        a, b = p.make_pair(**source)
        self.assertEqual(packet(a)["current_request"], source["current_request"])
        self.assertEqual(packet(a)["evidence"], "")
        self.assertEqual(a["messages"][0]["content"], p.SYSTEM)
        p.assert_pair(a, b)

    def test_invalid_or_extra_private_inputs_are_rejected(self):
        invalid = [("current_request", " "), ("current_request", 1), ("evidence", {}),
                   ("schema", []), ("schema", [""]), ("schema", ("schema",)),
                   ("history", {"historical_request": "h", "sql": "SELECT 1", "gold": "leak"}),
                   ("history", {"historical_request": "h", "sql": ""}),
                   ("database_sha256", "A" * 64), ("database_sha256", "a" * 63)]
        for key, value in invalid:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                source = inputs(); source[key] = value; p.make_pair(**source)
        with self.assertRaises(ValueError): p.make_pair(**inputs(), factor="both")

    def test_noncanonical_invalid_or_duplicate_json_rejected(self):
        a, b = p.make_pair(**inputs())
        for content in ("not JSON", "[]", " " + b["messages"][1]["content"],
                        '{"duplicate":1,"duplicate":2}', '{"number":NaN}'):
            with self.subTest(content=content[:30]), self.assertRaises(ValueError):
                altered = deepcopy(b); altered["messages"][1]["content"] = content
                p.assert_pair(a, altered)


if __name__ == "__main__":
    unittest.main()
