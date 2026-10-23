"""Synthetic sealed archives only; no model replies or human judgments created."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime import bird_readonly_audit as audit
from runtime.cache_intent_batch import HISTORIES
from runtime import cache_intent_recovery_preview as preview
from runtime.cache_intent_prompts import CONTRACT_FIELDS
from runtime.cache_intent_journal import verify
from scripts import prepare_staged_contract_review as review


class ReviewTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name); self.run=self.root/"run"
        self.contract=json.dumps(dict.fromkeys(CONTRACT_FIELDS,"fixture"))
        pairs=preview.make_pair("List unique IDs",None,["CREATE TABLE x(id INTEGER)"],
            {"historical_request":"List IDs", "sql":"SELECT id FROM x"},"a"*64)
        jobs=[dict(id=f"{h}-p{p}",history_id=h,read_price=p,envelope=deepcopy(pairs[i]))
              for h in HISTORIES for i,p in enumerate((1,8))]
        self.plan={"jobs":jobs,"independent_targets":2}
        self.data={"batch/plan.json":self.plan,"batch/backend.json":{"kind":"scripted"},
            "batch/summary.json":{"rows":[{"id":j["id"],"status":"NOT_STARTED"} for j in jobs]}}
        self.request=dict(generation_id="g001",stage="contract",contract_event=None,
                          messages=jobs[0]["envelope"]["messages"])
        self.data["h200a-p1/trace/g001.pending.json"]=self.request
        self.data["h200a-p1/trace/g001.response.json"]={"request_sha256":audit.digest(self.request),
            "response":{"content":self.contract,"finish_reason":"stop"}}
        self.data["batch/summary.json"]["rows"][0]["status"]="FAILED"
        self.block=patch("urllib.request.OpenerDirector.open",side_effect=AssertionError("No network"))
        self.block.start(); self.addCleanup(self.block.stop)

    def seal(self):
        for name,data in self.data.items():
            path=self.run/name; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(audit.encoded(data))
        manifest={p.relative_to(self.run).as_posix():audit.hash_file(p)
                  for p in self.run.rglob("*") if p.is_file()}
        audit.save_new(self.run/"artifact_hashes.json",manifest)
        self.manifest_sha=audit.hash_file(self.run/"artifact_hashes.json")

    def prepare(self):
        return review.prepare(self.run,manifest_sha=self.manifest_sha,
            plan_sha=audit.digest(self.plan),output=self.root/"review")

    def test_later_failure_keeps_contract_and_twelve_blank_slots(self):
        self.seal(); result=self.prepare()
        self.assertEqual(result["available_texts"],1)
        self.assertEqual(result["missing_responses"],11)
        self.assertFalse(result["semantic_labels_generated"])
        packet=json.loads((self.root/"review/reviewer-1.json").read_text())
        self.assertEqual(len(packet["items"]),12)
        self.assertTrue(packet["source_is_scripted"])
        for item in packet["items"]:
            self.assertTrue(all(v is None for v in item["review"].values()))
            for key in ("read_price","history_id","later_outcome","job_id"):
                self.assertNotIn(key,item)
        item=next(i for i in packet["items"] if i["raw_contract"] is not None)
        self.assertEqual(item["raw_contract"],self.contract)
        self.assertEqual(item["response_availability"],"FORMAT_VALID_UNREVIEWED")
        verify(self.root/"review")
        with self.assertRaises(ValueError): self.prepare()

    def test_no_response_remains_blank_not_negative_label(self):
        self.data.pop("h200a-p1/trace/g001.response.json")
        self.seal(); result=self.prepare()
        self.assertEqual(result["available_texts"],0)
        self.assertEqual(result["missing_responses"],12)

    def test_nonstop_response_is_preserved_but_not_marked_format_valid(self):
        self.data["h200a-p1/trace/g001.response.json"]["response"]["finish_reason"]="length"
        self.seal(); self.prepare()
        packet=json.loads((self.root/"review/reviewer-1.json").read_text())
        item=next(i for i in packet["items"] if i["raw_contract"] is not None)
        self.assertEqual(item["response_availability"],"NONSTOP_RESPONSE")
        self.assertEqual(item["raw_contract"],self.contract)

    def test_bad_format_text_not_rewritten_or_semantically_labeled(self):
        self.data["h200a-p1/trace/g001.response.json"]["response"]["content"]="{broken"
        self.seal(); self.prepare()
        packet=json.loads((self.root/"review/reviewer-2.json").read_text())
        item=next(i for i in packet["items"] if i["raw_contract"] is not None)
        self.assertEqual(item["response_availability"],"FORMAT_INVALID_UNREVIEWED")
        self.assertEqual(item["raw_contract"],"{broken")

    def test_modified_raw_archive_rejected_before_export(self):
        self.seal()
        (self.run/"h200a-p1/trace/g001.response.json").write_text("{}")
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse((self.root/"review").exists())

    def test_changed_request_event_is_not_pre_result_evidence(self):
        self.request["contract_event"]="g000"
        self.data["h200a-p1/trace/g001.response.json"]["request_sha256"]=audit.digest(self.request)
        self.seal()
        with self.assertRaises(ValueError): self.prepare()

    def test_response_hash_link_must_match(self):
        self.data["h200a-p1/trace/g001.response.json"]["request_sha256"]="0"*64
        self.seal()
        with self.assertRaises(ValueError): self.prepare()

    def test_missing_planned_outcome_rejected_not_dropped(self):
        self.data["batch/summary.json"]["rows"].pop()
        self.seal()
        with self.assertRaises(ValueError): self.prepare()

    def test_actual_scripted_batch_archive_later_provider_failure(self):
        from tests import test_cache_intent_batch as fixtures
        fixture=fixtures.BatchTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        count=[0]
        def scripted(job,payload):
            count[0]+=1
            reply=fixture.provider(fixture.contract)
            return reply if count[0]==1 else reply|{"model":"wrong"}
        result=fixture.run_batch(scripted)
        self.assertEqual(result["rows"][0]["status"],"FAILED")
        run=fixture.root/"run"
        receipt=review.prepare(run,manifest_sha=audit.hash_file(run/"artifact_hashes.json"),
            plan_sha=audit.digest(fixture.plan),output=self.root/"review")
        self.assertEqual(receipt["available_texts"],1)
        self.assertEqual(receipt["planned_slots"],12)
        mapping=json.loads((self.root/"review/organizer-only-mapping.json").read_text())
        self.assertEqual(mapping["records"][0]["later_outcome"],"FAILED")
        verify(self.root/"review")


if __name__=="__main__": unittest.main()
