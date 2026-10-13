"""Invented fixture contracts only; no assigned semantic labels."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from scripts.prepare_interface_output_review import materials
from tests.test_prepare_contract_output_review import inputs


def fixture():
    call, record = inputs()
    calls = [{"request": deepcopy(call["envelope"]), "interface_arm": "HIDDEN_ARM",
              "history_id": "HIDDEN_HISTORY"} for _ in range(12)]
    return {"calls": calls}, [dict(deepcopy(record), index=i) for i in range(12)]


class MaterialTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket", "subprocess.Popen", "sqlite3.connect"):
            guard = patch(target, side_effect=AssertionError("Offline fixture"))
            guard.start(); self.addCleanup(guard.stop)

    def test_maps_every_duplicate_without_editing_inputs(self):
        plan, records = fixture(); before = deepcopy((plan, records))
        items, texts = materials(plan, records)
        self.assertEqual((plan, records), before)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["call_indices"], list(range(12)))
        self.assertTrue(items[0]["item_id"].startswith("I-"))
        for text in texts.values():
            self.assertEqual(text.count("### 模型原输出"), 1)
            self.assertNotIn("这是首批开发结果", text)
            for hidden in ("PRIVATE_HISTORY", "PRIVATE_SQL", "HIDDEN_ARM", "HIDDEN_HISTORY", "call_indices"):
                self.assertNotIn(hidden, text)
            self.assertIn("____", text)

    def test_distinct_task_kept_even_if_same_contract(self):
        plan, records = fixture()
        packet = json.loads(plan["calls"][0]["request"]["messages"][1]["content"])
        packet["current_request"] = "Count visits."
        plan["calls"][0]["request"]["messages"][1]["content"] = json.dumps(packet)
        items, _ = materials(plan, records)
        self.assertEqual(len(items), 2)

    def test_no_cherry_picking_invalid_or_missing_response(self):
        plan, records = fixture()
        with self.assertRaises(Exception): materials(plan, records[:-1])
        records[5]["status"] = "format_invalid"
        with self.assertRaises(Exception): materials(plan, records)

    def test_verbatim_values_even_when_they_hint_at_condition(self):
        plan, records = fixture()
        records[0]["contract"]["unit"] = "declared_relative_units"
        _, texts = materials(plan, records)
        for text in texts.values():
            self.assertIn('"unit": "declared_relative_units"', text)
            self.assertIn("不能保证完全盲化", text)


if __name__ == "__main__":
    unittest.main()
