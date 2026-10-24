"""Invented review-material fixtures; no human or research labels."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from runtime.cache_intent_prompts import make_pair
from scripts.prepare_contract_output_review import FIELDS, group_records, ordered, render, code_block


def inputs():
    envelope = make_pair("Count customers.", "Use customer IDs.", ["CREATE TABLE c (id INTEGER);"],
                         {"historical_request": "PRIVATE_HISTORY", "sql": "PRIVATE_SQL"}, "a" * 64)[0]
    record = {"index": 0, "status": "format_valid", "contract": {f: "fixture" for f in FIELDS}}
    return {"envelope": envelope}, record


class OutputReviewTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "subprocess.Popen", "sqlite3.connect"):
            guard = patch(target, side_effect=AssertionError("Offline review fixture forbids " + target))
            guard.start()
            self.addCleanup(guard.stop)

    def test_duplicate_outputs_map_every_call_once(self):
        c, r = inputs()
        before = deepcopy((c, r))
        items, definitions = group_records([c, deepcopy(c)], [r, dict(r, index=1)])
        self.assertEqual((c, r), before)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["call_indices"], [0, 1])
        self.assertEqual(set(definitions), set(FIELDS))

    def test_equal_output_different_current_task_kept_separate(self):
        c, r = inputs()
        d = deepcopy(c)
        packet = json.loads(d["envelope"]["messages"][1]["content"])
        packet["current_request"] = "Count visits."
        d["envelope"]["messages"][1]["content"] = json.dumps(packet)
        items, _ = group_records([c, d], [r, dict(r, index=1)])
        self.assertEqual(len(items), 2)

    def test_hidden_context_is_not_transferred_but_output_is_unchanged(self):
        c, r = inputs()
        r["contract"]["unit"] = "literal output with # and | and ` characters"
        items, definitions = group_records([c], [r])
        text = render(items, definitions, 1)
        for forbidden in ("PRIVATE_HISTORY", "PRIVATE_SQL", "materialized_available", "read_cost",
                          "database_sha256", "call_indices", "call-000", "history_id"):
            self.assertNotIn(forbidden, text)
        self.assertIn(r["contract"]["unit"], text)
        self.assertIn("____", text)
        self.assertIn("不能保证完全盲化", text)

    def test_definitions_and_all_fields_rendered(self):
        c, r = inputs()
        items, definitions = group_records([c], [r])
        text = render(items, definitions, 2)
        for key in FIELDS:
            self.assertIn(definitions[key], text)
            self.assertIn(f"| {key} | ____ | ____ |", text)

    def test_wrong_index_invalid_response_or_missing_item_rejected(self):
        c, r = inputs()
        for calls, records in (([c], []), ([], []), ([c], [dict(r, index=1)]),
                               ([c], [dict(r, status="format_invalid")])):
            with self.assertRaises(Exception): group_records(calls, records)

    def test_no_response_frequency_in_reviewer_material(self):
        c, r = inputs()
        items, definitions = group_records([c, c], [r, dict(r, index=1)])
        text = render(items, definitions, 1)
        self.assertEqual(text.count("### 模型原输出"), 1)
        self.assertNotIn("[0, 1]", text)
        self.assertEqual(ordered(items, 1), ordered(items, 1))

    def test_code_fences_preserve_embedded_content(self):
        self.assertEqual(code_block("a\n```\nb", "text"), "````text\na\n```\nb\n````\n")


if __name__ == "__main__":
    unittest.main()
