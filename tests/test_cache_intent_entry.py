"""Source/input admission fixtures only; no model, SQL or network calls."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime import bird_readonly_audit as audit
from runtime import cache_intent_batch as batch
from runtime import cache_intent_staged_preview as preview
from runtime import cache_intent_entry as entry
from scripts import prepare_staged_source_bundle as bundle


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.inputs=self.root/"inputs"; self.inputs.mkdir()
        sql="SELECT 1"; result=dict(status="EXECUTED",complete=True,columns=["1"],rows=[[["integer","1"]]],sql_sha256=audit.digest(sql))
        result.update(audit.fingerprints(result["rows"]))
        self.caches={h:deepcopy(result) for h in batch.HISTORIES}
        pairs=[dict(history_id=h,arms=preview.make_pair("Select one",None,["fixture"],
            dict(historical_request="Select one",sql=sql),"a"*64)) for h in batch.HISTORIES]
        self.plan=batch.make_plan(pairs,self.caches)
        for name,value in (("PRIVATE_plan.json",self.plan),("PRIVATE_caches.json",self.caches),
                           ("receipt.json",dict(status="LOCAL_PLAN_ONLY",plan_sha256=audit.digest(self.plan),inference_authorized=False))):
            audit.save_new(self.inputs/name,value)
        for name in ("cache_intent_batch.py","prepare_recovery_batch.py"):
            (self.inputs/name).write_bytes(b"# inert fixture, never executed")
        audit.save_new(self.inputs/"artifact_hashes.json",{p.name:audit.hash_file(p) for p in self.inputs.iterdir()})
        self.manifest=audit.hash_file(self.inputs/"artifact_hashes.json")

    def load(self): return entry.load_private_inputs(self.inputs,self.manifest,audit.digest(self.plan))

    def test_roundtrip_with_all_planned_slots(self):
        plan,caches,receipt=self.load()
        self.assertEqual(plan,self.plan); self.assertEqual(caches,self.caches)
        self.assertFalse(receipt["inference_authorized"])

    def test_mutation_extra_file_and_wrong_plan_rejected(self):
        with self.assertRaises(ValueError): entry.load_private_inputs(self.inputs,self.manifest,"b"*64)
        (self.inputs/"unmanifested.txt").write_bytes(b"extra")
        with self.assertRaises(ValueError): self.load()

    def test_changed_input_bytes_rejected(self):
        (self.inputs/"PRIVATE_plan.json").write_bytes(b"{}")
        with self.assertRaises(ValueError): self.load()

    def test_source_identity_checks_current_tree(self):
        source=Path(__file__).resolve().parents[1]
        result=bundle.build(source,self.root/"source")
        checked=entry.check_running_source(self.root/"source",result["source_manifest_sha256"])
        self.assertTrue(checked["running_source_matches"])
        (self.root/"source/source/runtime/cache_intent_entry.py").write_bytes(b"changed")
        with self.assertRaises(ValueError): entry.check_running_source(self.root/"source",result["source_manifest_sha256"])

    def test_execute_flag_rejected_before_inspection(self):
        args=["--inputs","fixture","--source-bundle","fixture","--source-sha256","a"*64,
              "--database","fixture","--output","fixture","--execute"]
        with patch.object(entry,"inspect_inputs") as check:
            with self.assertRaises(SystemExit) as error: entry.main(args)
            self.assertEqual(error.exception.code,2)
            check.assert_not_called()


if __name__=="__main__": unittest.main()
