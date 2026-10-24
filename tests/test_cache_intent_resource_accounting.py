"""Synthetic ledger tests: no measured research costs or model calls."""
from copy import deepcopy
import json
import unittest
from runtime.cache_intent_resource_accounting import summarize
from runtime import bird_readonly_audit as audit
from runtime.cache_intent_journal import verify
from scripts import audit_staged_resources as exporter


class ResourceTests(unittest.TestCase):
    def fixture(self):
        pending=dict(attempt=1,automatic_retry=False,charge=10,balance_before=210,balance_after=200,
            admission="ADMITTED",request={"tool":"run_sql","arguments":{"sql":"SELECT fixture"}})
        return {"tools/started.json":{"configuration":{"initial_budget":210}},
            "tools/attempt-001.pending.json":pending,
            "tools/attempt-001.completed.json":dict(**pending,response=dict(attempt=1,charged_units=10,
                remaining_budget=200,status="EXECUTION_ERROR",complete=False),
                measured_action_wall_seconds=0.25,private={"backend_result":{"process_wall_seconds":9999}}),
            "tools/summary.json":{"attempts":1,"spent_declared_units":10,"remaining_budget":200},
            "trace/http-g001.pending.json":{"payload":{}},
            "trace/http-g001.provider.json":{"raw":{"model":"wrong-model-fixture",
                "usage":{"prompt_tokens":13,"completion_tokens":7,"total_tokens":20}}},
            "trace/g001.response.json":{"wall_seconds":0.4}}

    def test_failed_attempts_are_counted_without_private_historical_timing(self):
        data=self.fixture(); before=deepcopy(data); result=summarize(data)
        self.assertEqual(result["recorded_declared_units"],10)
        self.assertEqual(result["completed_tool_action_wall_seconds"],0.25)
        self.assertEqual(result["reported_prompt_tokens"],13)
        self.assertEqual(result["reported_completion_tokens"],7)
        self.assertIsNone(result["total_financial_cost"])
        self.assertIsNone(result["verified_model_calls"])
        self.assertEqual(data,before)

    def test_missing_provider_usage_stays_unknown_not_zero_usage(self):
        data=self.fixture(); data.pop("trace/http-g001.provider.json")
        result=summarize(data)
        self.assertEqual(result["unknown_usage_records"],1)
        self.assertIsNone(result["token_records"][0]["reported_usage"])

    def test_bad_total_or_negative_usage_remains_unknown(self):
        for usage in ({"prompt_tokens":13,"completion_tokens":7,"total_tokens":99},
                      {"prompt_tokens":-1,"completion_tokens":7},
                      {"prompt_tokens":True,"completion_tokens":7}):
            data=self.fixture(); data["trace/http-g001.provider.json"]["raw"]["usage"]=usage
            self.assertEqual(summarize(data)["unknown_usage_records"],1)

    def test_pending_only_retains_recorded_charge_and_unknown_time(self):
        data=self.fixture(); data.pop("tools/attempt-001.completed.json"); data.pop("tools/summary.json")
        result=summarize(data)
        self.assertEqual(result["recorded_declared_units"],10)
        self.assertEqual(result["unresolved_tool_records"],1)
        self.assertIsNone(result["tool_records"][0]["measured_action_wall_seconds"])

    def test_duplicate_completion_is_not_added_as_another_charge(self):
        result=summarize(self.fixture())
        self.assertEqual(result["tool_pending_records"],1)
        self.assertEqual(result["recorded_declared_units"],10)

    def test_orphan_provider_and_orphan_tool_completion_rejected(self):
        for key in ("trace/http-g001.pending.json","tools/attempt-001.pending.json"):
            data=self.fixture(); data.pop(key)
            with self.assertRaises(ValueError): summarize(data)

    def test_broken_balance_chain_and_false_summary_rejected(self):
        data=self.fixture(); data["tools/attempt-001.pending.json"]["balance_after"]=199
        with self.assertRaises(ValueError): summarize(data)
        data=self.fixture(); data["tools/summary.json"]["spent_declared_units"]=0
        with self.assertRaises(ValueError): summarize(data)

    def test_nonfinite_time_rejected(self):
        data=self.fixture(); data["tools/attempt-001.completed.json"]["measured_action_wall_seconds"]=float("nan")
        with self.assertRaises(ValueError): summarize(data)

    def test_real_scripted_batch_failure_keeps_all_slots_and_unknown_usage(self):
        from tests import test_cache_intent_batch as fixtures
        f=fixtures.BatchTests(); f.setUp(); self.addCleanup(f.doCleanups)
        f.run_batch(lambda job,payload:f.provider(f.contract)|{"model":"wrong"})
        source=f.root/"run"
        exporter.prepare(source,manifest_sha=audit.hash_file(source/"artifact_hashes.json"),
            plan_sha=audit.digest(f.plan),output=f.root/"accounted")
        result=json.loads((f.root/"accounted/resource-accounting.json").read_text())
        self.assertEqual(len(result["rows"]),12)
        self.assertEqual(result["rows"][0]["outcome"],"FAILED")
        self.assertEqual(result["rows"][0]["accounting"]["unknown_usage_records"],1)
        self.assertEqual(sum(r["outcome"]=="NOT_STARTED" for r in result["rows"]),11)
        self.assertFalse(result["conditioned_on_success"])
        verify(f.root/"accounted")


if __name__=="__main__": unittest.main()
