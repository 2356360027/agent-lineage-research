"""In-memory scripted wiring tests. Not LLM/SQL runs or semantic validation."""
from copy import deepcopy
import json
import unittest
from runtime import cache_intent_staged_preview as preview
from runtime import cache_intent_staged_loop as loop
from runtime.cache_intent_prompts import CONTRACT_FIELDS


class FakeSession:
    def __init__(self):
        self.attempts = 0
    def describe(self):
        return dict(cache_id="v000", materialized_available=True, read_cost=1,
            sql_call_cost=10, budget_profile="nonbinding-200-v1",
            nonbinding_for_configured_horizon=True, initial_budget=200,
            remaining_budget=200-self.attempts, max_attempts=20,
            attempts_used=self.attempts, cost_basis="declared_relative_units",
            database_sha256="a"*64)
    def call(self, name, arguments):
        self.attempts += 1
        return dict(status="EXECUTED", complete=True, rows=[], columns=["fixture"])


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.envelope = preview.make_pair("List IDs", None,
            ["CREATE TABLE x(id TEXT)"],
            {"historical_request":"List IDs", "sql":"SELECT id FROM x"}, "a"*64)[0]
        self.contract = json.dumps(dict.fromkeys(CONTRACT_FIELDS, "unspecified"))
        self.answer = '{"kind":"answer","text":"fixture answer"}'
        self.read = '{"kind":"read_cache","cache_id":"v000"}'
        self.records, self.requests = {}, []
        self.session = FakeSession()

    def save(self, name, record):
        if name in self.records:
            raise FileExistsError(name)
        self.records[name] = deepcopy(record)

    def run_script(self, content):
        items = iter(content)
        def generate(request):
            self.requests.append(deepcopy(request))
            raw = next(items)
            if isinstance(raw, BaseException):
                raise raw
            return {"content":raw, "finish_reason":"stop", "usage":None}
        return loop.run_trajectory(self.envelope, self.session, generate, self.save)

    def test_separate_generations_and_forward_link(self):
        result = self.run_script([self.contract, self.read, self.answer])
        self.assertEqual(result["generation_calls"], 3)
        self.assertEqual(self.records["t001.pending"]["generation_id"], "g002")
        self.assertEqual(self.records["t001.pending"]["contract_event"], "g001")
        self.assertNotIn("tool_observation", str(self.requests[1]["messages"]))
        self.assertIn("tool_observation", str(self.requests[2]["messages"]))

    def test_horizon_terminal_cannot_execute_tool(self):
        result = self.run_script([self.contract]+[self.read]*21)
        self.assertEqual(result["status"], "TOOL_HORIZON_VIOLATION")
        self.assertEqual(result["generation_calls"], 22)
        self.assertEqual(self.session.attempts, 20)

    def test_invalid_contract_no_repair(self):
        result = self.run_script(["{}"])
        self.assertEqual(result["status"], "INVALID_CONTRACT")
        self.assertEqual(len(self.requests), 1)

    def test_provider_failure_preserved_not_retried(self):
        with self.assertRaises(TimeoutError):
            self.run_script([self.contract, TimeoutError()])
        self.assertIn("g002.pending", self.records)
        self.assertIn("g002.failure", self.records)
        self.assertNotIn("summary", self.records)
        self.assertEqual(self.session.attempts, 0)

    def test_invalid_action_is_not_executed(self):
        result = self.run_script([self.contract, '{"kind":"run_sql","sql":"SELECT 1","extra":2}'])
        self.assertEqual(result["status"], "INVALID_ACTION")
        self.assertEqual(self.session.attempts, 0)

    def test_write_failure_prevents_generation(self):
        def fail(name, record):
            raise OSError("fixture")
        def forbidden(request):
            self.fail("Generation after failed journal write")
        with self.assertRaises(OSError):
            loop.run_trajectory(self.envelope, self.session, forbidden, fail)

    def test_duplicate_action_keys_rejected(self):
        with self.assertRaises(ValueError):
            loop.parse_action('{"kind":"answer","kind":"run_sql","sql":"SELECT 1"}')

    def test_truncated_generation_preserved_without_tool_execution(self):
        responses = iter([
            {"content":self.contract, "finish_reason":"stop"},
            {"content":self.read, "finish_reason":"length", "usage":{"completion_tokens":40}}])
        result = loop.run_trajectory(self.envelope, self.session,
                                     lambda request: next(responses), self.save)
        self.assertEqual(result["status"], "INVALID_ACTION_RESPONSE")
        self.assertEqual(self.session.attempts, 0)
        self.assertEqual(self.records["g002.response"]["response"]["usage"]["completion_tokens"], 40)


if __name__ == "__main__":
    unittest.main()
