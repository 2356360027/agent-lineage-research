"""Scripted trace reconstruction only; no research judgments or inference."""
from copy import deepcopy
import json
import unittest
from runtime import cache_intent_trace_evidence as evidence
from runtime import bird_readonly_audit as audit
from scripts import audit_staged_trace_evidence as exporter
from runtime.cache_intent_journal import verify
from tests import test_cache_intent_recovery as fixtures


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.RecoveryTests(); self.fixture.setUp()

    def inspect(self):
        return evidence.inspect(self.fixture.pair[0],self.fixture.records)

    def test_sql_link_preserves_raw_and_unknown_semantics(self):
        f=self.fixture; f.run_script([f.contract,f.sql,f.answer])
        before=deepcopy(f.records); result=self.inspect()
        self.assertEqual(result["termination"],"ANSWERED")
        self.assertEqual(result["events"][1]["contract_link"]["contract_event"],"g001")
        self.assertTrue(result["events"][1]["tool"]["included_in_later_saved_request"])
        self.assertTrue(all(v is None for v in result["judgments"].values()))
        self.assertEqual(f.records,before)

    def test_cache_read_alone_does_not_prove_cache_application(self):
        f=self.fixture; f.run_script([f.contract,f.cache,f.sql,f.answer])
        result=self.inspect()
        self.assertIsNone(result["judgments"]["cache_application"])
        self.assertTrue(result["cache_read_is_not_proof_of_final_cache_application"])
        self.assertEqual(result["tool_pending_records"],2)

    def test_recovery_23_generations_reconstruct_exactly(self):
        f=self.fixture; f.run_script([f.contract]+[f.cache]*20+[f.sql,f.answer])
        result=self.inspect()
        self.assertEqual(len(result["events"]),23)
        self.assertEqual(result["tool_pending_records"],21)

    def test_sorted_journal_json_does_not_change_original_observation_string(self):
        f=self.fixture; f.run_script([f.contract,f.sql,f.answer])
        f.records=json.loads(json.dumps(f.records,sort_keys=True))
        result=self.inspect()
        self.assertEqual(result["termination"],"ANSWERED")
        self.assertTrue(result["events"][1]["tool"]["included_in_later_saved_request"])

    def test_forward_message_mutation_rejected_even_with_rehashed_response(self):
        f=self.fixture; f.run_script([f.contract,f.sql,f.answer])
        f.records["g003.pending"]["messages"][-1]["content"]="invented observation"
        f.records["g003.response"]["request_sha256"]=audit.digest(f.records["g003.pending"])
        with self.assertRaises(ValueError): self.inspect()

    def test_tool_dispatch_must_equal_generated_sql(self):
        f=self.fixture; f.run_script([f.contract,f.sql,f.answer])
        f.records["t001.pending"]["arguments"]["sql"]="SELECT 2"
        with self.assertRaises(ValueError): self.inspect()

    def test_orphan_tool_response_rejected(self):
        f=self.fixture; f.run_script([f.contract,f.sql,f.answer])
        f.records.pop("t001.pending")
        with self.assertRaises(ValueError): self.inspect()

    def test_exposure_failure_keeps_raw_without_claiming_model_saw_it(self):
        f=self.fixture
        with self.assertRaises(ValueError):
            f.run_script([f.contract,f.sql],count=lambda messages:999)
        result=self.inspect(); tool=result["events"][1]["tool"]
        self.assertEqual(result["termination"],"EXPOSURE_FAILED")
        self.assertIsNotNone(tool["raw_response"])
        self.assertIsNotNone(tool["exposure_failure"])
        self.assertIsNone(tool["visible_observation"])
        self.assertFalse(tool["included_in_later_saved_request"])

    def test_withheld_result_not_misrepresented_as_visible(self):
        f=self.fixture
        original=f.session.call
        def large(name,args): return original(name,args)|{"rows":[[["text","oversize-marker"]]]}
        f.session.call=large
        f.run_script([f.contract,f.cache,f.answer],
            count=lambda messages:600 if "oversize-marker" in messages[-1]["content"] else 100)
        tool=self.inspect()["events"][1]["tool"]
        self.assertIn("oversize-marker",str(tool["raw_response"]))
        self.assertNotIn("oversize-marker",str(tool["visible_observation"]))

    def test_generation_pending_is_not_model_completed(self):
        f=self.fixture; f.run_script([f.contract,f.answer])
        f.records.pop("g002.response")
        result=self.inspect()
        self.assertEqual(result["termination"],"GENERATION_PENDING")
        self.assertIsNotNone(result["contract"])
        self.assertIsNone(result["events"][-1]["parsed_action"])

    def test_disk_scripted_batch_export_retains_all_slots(self):
        from tests import test_cache_intent_batch as batch_fixtures
        f=batch_fixtures.BatchTests(); f.setUp(); self.addCleanup(f.doCleanups)
        count=[0]
        def scripted(job,payload):
            count[0]+=1
            return f.provider(f.contract) if count[0]==1 else f.provider("{}")|{"model":"wrong"}
        f.run_batch(scripted)
        run=f.root/"run"
        result=exporter.prepare(run,manifest_sha=audit.hash_file(run/"artifact_hashes.json"),
            plan_sha=audit.digest(f.plan),output=f.root/"evidence")
        self.assertEqual(result["planned_slots"],12)
        self.assertEqual(result["saved_contracts"],1)
        packet=json.loads((f.root/"evidence/private-evidence.json").read_text())
        self.assertEqual(packet["rows"][0]["evidence"]["termination"],"GENERATION_FAILED")
        self.assertEqual(packet["rows"][1]["evidence"]["termination"],"GENERATION_NOT_RECORDED")
        verify(f.root/"evidence")


if __name__=="__main__": unittest.main()
