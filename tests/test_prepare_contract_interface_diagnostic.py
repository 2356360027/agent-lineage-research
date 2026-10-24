"""Synthetic offline preparation tests; no model responses or semantic labels."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from runtime import cache_intent_prompts as legacy
from runtime import cache_intent_prompts_v2 as candidate
from scripts.prepare_contract_interface_diagnostic import construct, extract_controls, encoded, sha


def fixture_plan():
    calls = []
    for history in (f"h{q}{s}" for q in (200, 234) for s in "abc"):
        for factor in ("availability", "read_cost"):
            pair = legacy.make_pair("Count distinct customers.", None,
                                    ["CREATE TABLE customers (id INTEGER);"],
                                    {"historical_request": "List customers.", "sql": "SELECT id FROM customers"},
                                    "a" * 64, factor=factor)
            for arm, envelope in enumerate(pair):
                calls.append({"history_id": history, "factor": factor, "arm": arm,
                              "seed": 1, "envelope": envelope})
    return {"calls": calls}


class PreparationTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "subprocess.Popen",
                       "sqlite3.connect", "urllib.request.OpenerDirector.open"):
            guard = patch(target, side_effect=AssertionError("Offline fixture forbids " + target))
            guard.start()
            self.addCleanup(guard.stop)

    def test_twelve_new_requests_six_matched_pairs(self):
        source = fixture_plan()
        before = deepcopy(source)
        plan = construct(source)
        self.assertEqual(source, before)
        self.assertEqual(len(plan["calls"]), 12)
        first_arms = []
        for offset in range(0, 12, 2):
            a, b = plan["calls"][offset:offset+2]
            first_arms.append(a["interface_arm"])
            self.assertEqual(a["history_id"], b["history_id"])
            self.assertEqual(a["paired_seed"], b["paired_seed"])
            self.assertEqual(legacy._diff(a["request"], b["request"]), [("messages", 0, "content")])
            for c in (a, b):
                self.assertEqual(c["request_sha256"], sha(encoded(c["request"])))
                self.assertEqual(c["request"]["seed"], c["paired_seed"])
                self.assertEqual(c["request"]["temperature"], 0)
                self.assertEqual(c["request"]["tools"], [])
                self.assertEqual(set(c["request"]), {"messages", "tools", "response_format", "model", "seed",
                                                     "temperature", "top_p", "max_tokens", "stream"})
                data = __import__("json").loads(c["request"]["messages"][1]["content"])
                self.assertIs(data["context"]["cache"]["materialized_available"], True)
                self.assertEqual(data["context"]["cache"]["read_cost"], 1)
        self.assertEqual(first_arms.count("legacy"), 3)

    def test_candidate_definitions_only_in_candidate_system(self):
        plan = construct(fixture_plan())
        for call in plan["calls"]:
            content = call["request"]["messages"][0]["content"]
            expected = legacy.SYSTEM + (candidate.DEFINITION_SUFFIX if call["interface_arm"] == "visible_definitions" else "")
            self.assertEqual(content, expected)

    def test_determinism_and_source_order_independence(self):
        source = fixture_plan()
        first = construct(source)
        source["calls"].reverse()
        second = construct(source)
        self.assertEqual(first["calls"], second["calls"])
        self.assertEqual(first, construct(fixture_plan()))

    def test_no_inferred_authority_or_scoring(self):
        plan = construct(fixture_plan())
        self.assertEqual(plan["status"], "PREPARED_NOT_AUTHORIZED_NOT_EXECUTED")
        self.assertEqual(plan["human_approvals"], 0)
        self.assertFalse(plan["formal_inference_eligible"])
        self.assertFalse(plan["semantic_endpoints_automatically_scored"])
        self.assertEqual(plan["maximum_retries"], 0)

    def test_incomplete_duplicate_unknown_and_tampered_sources_rejected(self):
        for mode in ("missing", "duplicate", "unknown", "same_edit", "different_data", "bad_arm"):
            with self.subTest(mode=mode):
                source = fixture_plan()
                if mode == "missing": source["calls"].pop()
                elif mode == "duplicate": source["calls"][-1] = deepcopy(source["calls"][0])
                elif mode == "unknown": source["calls"][0]["history_id"] = "other"
                elif mode == "bad_arm": source["calls"][0]["arm"] = False
                elif mode == "same_edit":
                    for c in source["calls"]: c["envelope"]["messages"][0]["content"] += "changed"
                else:
                    for c in source["calls"][:2]:
                        c["envelope"]["messages"][1]["content"] = c["envelope"]["messages"][1]["content"].replace(
                            "Count distinct customers.", "Count all customers.")
                with self.assertRaises(ValueError if mode == "same_edit" else Exception):
                    extract_controls(source)

    def test_returned_requests_do_not_alias(self):
        source = fixture_plan()
        plan = construct(source)
        other = deepcopy(plan["calls"][1])
        plan["calls"][0]["request"]["response_format"].clear()
        self.assertEqual(plan["calls"][1], other)
        self.assertTrue(source["calls"][0]["envelope"]["response_format"])


if __name__ == "__main__":
    unittest.main()
