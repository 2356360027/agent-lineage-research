"""Synthetic prompt software tests; no model, SQL, human labels or network."""
from copy import deepcopy
import json
import unittest

from runtime import cache_intent_staged_preview as preview
from runtime import cache_intent_prompts as legacy


class StagedPreviewTests(unittest.TestCase):
    def setUp(self):
        self.pair = preview.make_pair("List unique item IDs", None,
            ["CREATE TABLE items (id TEXT PRIMARY KEY)"],
            {"historical_request": "List item IDs", "sql": "SELECT id FROM items"}, "a"*64)

    def test_only_cost_changes(self):
        left, right = map(deepcopy, self.pair)
        packet = json.loads(left["messages"][1]["content"])
        self.assertEqual(packet["context"]["cache"]["read_cost"], 1)
        packet["context"]["cache"]["read_cost"] = 8
        left["messages"][1]["content"] = legacy._json(packet)
        self.assertEqual(left, right)

    def test_state_binding_and_nonbinding_budget(self):
        for envelope in self.pair:
            packet = preview.validate_preview(envelope)
            context = packet["context"]
            state = dict(context["cache"], sql_call_cost=10,
                budget_profile="nonbinding-200-v1", nonbinding_for_configured_horizon=True,
                initial_budget=200, remaining_budget=200, max_attempts=20,
                attempts_used=0, cost_basis="declared_relative_units", database_sha256="a"*64)
            preview.assert_initial_session(envelope, state)
            for key, value in (("initial_budget",100), ("attempts_used",1),
                               ("database_sha256","b"*64), ("read_cost",3)):
                with self.assertRaises(ValueError):
                    preview.assert_initial_session(envelope, state | {key:value})

    def test_common_prompt_edit_rejected(self):
        envelope = deepcopy(self.pair[0])
        envelope["messages"][0]["content"] += " Prefer cached answers."
        with self.assertRaises(ValueError):
            preview.validate_preview(envelope)

    def test_private_packet_field_rejected(self):
        envelope = deepcopy(self.pair[0])
        packet = json.loads(envelope["messages"][1]["content"])
        packet["gold_sql"] = "SECRET"
        envelope["messages"][1]["content"] = legacy._json(packet)
        with self.assertRaises(ValueError):
            preview.validate_preview(envelope)

    def test_raw_contract_kept_and_inputs_unchanged(self):
        before = deepcopy(self.pair[0])
        raw = json.dumps(dict.fromkeys(legacy.CONTRACT_FIELDS, "unspecified"), indent=2)
        messages = preview.next_generation_messages(self.pair[0], raw)
        self.assertEqual(messages[2], {"role":"assistant", "content":raw})
        self.assertEqual(before, self.pair[0])
        self.assertEqual(len(messages), 4)

    def test_invalid_contract_not_repaired(self):
        for raw in ("not json", "{}", "[]", json.dumps(dict.fromkeys(legacy.CONTRACT_FIELDS, 1))):
            with self.assertRaises(ValueError):
                preview.next_generation_messages(self.pair[0], raw)

    def test_duplicate_contract_field_not_silently_overwritten(self):
        raw = json.dumps(dict.fromkeys(legacy.CONTRACT_FIELDS, "unspecified"))
        raw = raw[:-1] + ', "entity":"different population"}'
        with self.assertRaises(ValueError):
            preview.next_generation_messages(self.pair[0], raw)


if __name__ == "__main__":
    unittest.main()
