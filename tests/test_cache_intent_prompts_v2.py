"""Offline synthetic construction tests; no model or semantic-validation claims."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime import cache_intent_prompts as legacy
from runtime import cache_intent_prompts_v2 as candidate


def inputs():
    return {"current_request": "Count distinct customers active in 2024.",
            "evidence": None,
            "schema": ["CREATE TABLE visits (customer_id INTEGER, year INTEGER);"],
            "history": {"historical_request": "Count visits in 2023.",
                        "sql": "SELECT COUNT(*) FROM visits WHERE year = 2023"},
            "database_sha256": "a" * 64}


def packet(envelope):
    return json.loads(envelope["messages"][1]["content"])


def set_packet(envelope, value):
    envelope["messages"][1]["content"] = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class CandidatePromptTests(unittest.TestCase):
    def setUp(self):
        self.before = Path(legacy.__file__).read_bytes()
        self.baseline = legacy.make_pair(**inputs())
        self.network_guards = []
        for target in ("socket.create_connection", "socket.socket.connect",
                       "subprocess.Popen", "sqlite3.connect", "urllib.request.OpenerDirector.open"):
            guard = patch(target, side_effect=AssertionError("Offline test forbids " + target))
            mocked = guard.start()
            self.addCleanup(guard.stop)
            self.network_guards.append(mocked)

    def tearDown(self):
        self.assertEqual(Path(legacy.__file__).read_bytes(), self.before)
        self.assertEqual(legacy.make_pair(**inputs()), self.baseline)
        for guard in self.network_guards:
            guard.assert_not_called()

    def test_both_factors_and_ordered_values(self):
        for factor in ("availability", "read_cost"):
            with self.subTest(factor=factor):
                a, b = candidate.make_pair(**inputs(), factor=factor)
                candidate.assert_pair(a, b, factor=factor)
                x, y = packet(a), packet(b)
                if factor == "availability":
                    self.assertIs(x["context"]["cache"]["materialized_available"], False)
                    self.assertIs(y["context"]["cache"]["materialized_available"], True)
                else:
                    self.assertEqual(x["context"]["cache"]["read_cost"], 1)
                    self.assertEqual(y["context"]["cache"]["read_cost"], 8)
                for data in (x, y):
                    self.assertEqual(data["context"]["budget"], 100)
                    self.assertEqual(data["context"]["sql_call_cost"], 10)

    def test_exact_legacy_definitions_visible_in_system(self):
        expected = "\n\nContract field definitions:\n" + "\n".join(
            f"{key}: {legacy._DESCRIPTIONS[key]}" for key in legacy.CONTRACT_FIELDS)
        self.assertEqual(candidate.DEFINITION_SUFFIX, expected)
        for envelope in candidate.make_pair(**inputs()):
            self.assertEqual(envelope["messages"][0]["content"], legacy.SYSTEM + expected)
            self.assertEqual(envelope["messages"][0]["content"].count(expected), 1)

    def test_only_system_has_changed_in_both_factors(self):
        for factor in ("availability", "read_cost"):
            old = legacy.make_pair(**inputs(), factor=factor)
            new = candidate.make_pair(**inputs(), factor=factor)
            for a, b in zip(old, new):
                self.assertEqual(legacy._diff(a, b), [("messages", 0, "content")])
                self.assertEqual(candidate._remove_verified_suffix(b), a)
                self.assertEqual(a["messages"][1]["content"], b["messages"][1]["content"])
                self.assertNotIn("seed", b)

    def test_original_source_hash_and_envelopes_unchanged(self):
        self.assertEqual(hashlib.sha256(self.before).hexdigest(),
                         "e92cd5098eaebd62f6f8a9424d77b3e970aef202d04869582057d2f275bd5656")
        candidate.make_pair(**inputs())
        legacy.assert_pair(*self.baseline)

    def test_no_mutation_or_aliases(self):
        source = inputs(); before = deepcopy(source)
        original = legacy.make_pair(**source)
        snapshot = deepcopy(original)
        with patch.object(legacy, "make_pair", return_value=original):
            a, b = candidate.make_pair(**source)
        pair_snapshot = deepcopy((a, b))
        candidate.assert_pair(a, b)
        self.assertEqual((a, b), pair_snapshot)
        a["messages"][0]["content"] += "changed"
        a["tools"].append("changed")
        a["response_format"]["json_schema"]["schema"]["required"].clear()
        self.assertEqual(b, pair_snapshot[1])
        self.assertEqual(original, snapshot)
        self.assertEqual(source, before)

    def test_changed_missing_or_repeated_suffix_rejected(self):
        for factor in ("availability", "read_cost"):
            for mode in ("missing", "changed", "repeated", "trailing"):
                with self.subTest(factor=factor, mode=mode):
                    a, b = candidate.make_pair(**inputs(), factor=factor)
                    content = b["messages"][0]["content"]
                    if mode == "missing": content = content[:-len(candidate.DEFINITION_SUFFIX)]
                    elif mode == "changed": content = content.replace("Requested units and scale", "Changed units and scale")
                    elif mode == "repeated": content += candidate.DEFINITION_SUFFIX
                    else: content += "\n"
                    b["messages"][0]["content"] = content
                    with self.assertRaises(ValueError): candidate.assert_pair(a, b, factor)

    def test_changed_legacy_prefix_cannot_be_hidden_by_suffix_removal(self):
        a, b = candidate.make_pair(**inputs())
        b["messages"][0]["content"] = "Changed old prefix.\n" + b["messages"][0]["content"]
        with self.assertRaises(ValueError): candidate.assert_pair(a, b)

    def test_identical_unauthorized_edits_to_both_arms_rejected(self):
        for factor in ("availability", "read_cost"):
            for mode in ("prefix", "suffix", "tools", "schema", "role", "metadata", "budget"):
                with self.subTest(factor=factor, mode=mode):
                    a, b = candidate.make_pair(**inputs(), factor=factor)
                    for envelope in (a, b):
                        if mode == "prefix": envelope["messages"][0]["content"] = "Changed. " + envelope["messages"][0]["content"]
                        elif mode == "suffix": envelope["messages"][0]["content"] += "changed"
                        elif mode == "tools": envelope["tools"] = [{"type": "function"}]
                        elif mode == "schema": envelope["response_format"]["json_schema"]["strict"] = 1
                        elif mode == "role": envelope["messages"][0]["role"] = "user"
                        elif mode == "metadata": envelope["seed"] = 77
                        else:
                            data = packet(envelope); data["context"]["budget"] = 200
                            set_packet(envelope, data)
                    with self.assertRaises(ValueError): candidate.assert_pair(a, b, factor)

    def test_reversed_identical_or_wrong_factor_rejected(self):
        for factor in ("availability", "read_cost"):
            a, b = candidate.make_pair(**inputs(), factor=factor)
            for first, second, selected in ((b, a, factor), (a, a, factor),
                    (a, b, "read_cost" if factor == "availability" else "availability")):
                with self.assertRaises(ValueError): candidate.assert_pair(first, second, selected)

    def test_old_pair_is_not_silently_accepted_as_v2(self):
        with self.assertRaises(ValueError): candidate.assert_pair(*legacy.make_pair(**inputs()))

    def test_bad_shapes_and_invalid_factor_rejected(self):
        a, b = candidate.make_pair(**inputs())
        for value in (None, [], {}, {"messages": []}, {"messages": [None, {}]},
                      {"messages": [{"content": None}, {}]}):
            with self.assertRaises(ValueError): candidate.assert_pair(value, b)
        with self.assertRaises(ValueError): candidate.make_pair(**inputs(), factor="both")

    def test_candidate_version_makes_no_effectiveness_or_deployment_claim(self):
        self.assertIn("candidate", candidate.VERSION)
        self.assertEqual(candidate.STATUS, "CANDIDATE_NOT_DEPLOYED_EFFECTIVENESS_UNTESTED")


if __name__ == "__main__":
    unittest.main()
