"""Synthetic SQLite + scripted backend; no live server or research samples."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from runtime import bird_readonly_audit as audit
from runtime import cache_intent_batch as batch
from runtime import cache_intent_staged_preview as preview
from runtime.cache_intent_capacity import frame,PREFIX
from runtime.cache_intent_prompts import CONTRACT_FIELDS
from runtime.cache_intent_journal import verify


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.db=self.root/"fixture.sqlite"
        conn=sqlite3.connect(self.db)
        conn.execute("CREATE TABLE x(id INTEGER)"); conn.execute("INSERT INTO x VALUES (7)")
        conn.commit(); conn.close()
        self.sql="SELECT id FROM x"; sha=audit.hash_file(self.db)
        cached=dict(status="EXECUTED",complete=True,columns=["id"],rows=[[["integer","7"]]],
                    sql_sha256=audit.digest(self.sql))
        cached.update(audit.fingerprints(cached["rows"]))
        self.caches={h:deepcopy(cached) for h in batch.HISTORIES}
        pairs=[dict(history_id=h,arms=preview.make_pair("List IDs",None,["CREATE TABLE x(id INTEGER)"],
            dict(historical_request="List IDs",sql=self.sql),sha)) for h in batch.HISTORIES]
        self.plan=batch.make_plan(pairs,self.caches)
        self.pairs=pairs
        self.contract=json.dumps(dict.fromkeys(CONTRACT_FIELDS,"fixture"))
        self.text=lambda s:len(s.encode())
        self.chat=lambda ms:sum(self.text(frame(m["role"],m["content"])) for m in ms)+self.text(PREFIX)

    def run_batch(self,transport,clock=None):
        kw={} if clock is None else {"clock":clock}
        with patch("urllib.request.OpenerDirector.open",side_effect=AssertionError("No network")):
            return batch.scripted_batch(self.plan,expected_plan_sha256=audit.digest(self.plan),
                caches=self.caches,database=self.db,output=self.root/"run",count_chat=self.chat,
                count_text=self.text,tokenizer_sha256="a"*64,template_sha256="b"*64,
                transport=transport,**kw)

    def provider(self,content):
        return dict(model="SCRIPTED_NOT_MODEL",choices=[dict(message=dict(content=content),finish_reason="stop")])

    def test_fixed_schedule_and_reference_fields_rejected(self):
        self.assertEqual(len(self.plan["jobs"]),12)
        self.assertEqual([j["read_price"] for j in self.plan["jobs"][:4]],[1,8,8,1])
        for i in range(0,12,2):
            self.assertEqual(self.plan["jobs"][i]["seed"],self.plan["jobs"][i+1]["seed"])
        for mutate in (lambda p:p["jobs"].reverse(),
                       lambda p:p.update(reference_contract={}),
                       lambda p:p["jobs"][0].update(seed=900)):
            changed=deepcopy(self.plan); mutate(changed)
            with self.assertRaises(ValueError): batch.validate_plan(changed,self.caches)

    def test_serialized_pairs_and_plan_roundtrip(self):
        plan=batch.make_plan(json.loads(json.dumps(self.pairs)),self.caches)
        self.assertEqual(plan,self.plan)
        batch.validate_plan(json.loads(json.dumps(plan)),self.caches)

    def test_all_twelve_complete_full_recovery_without_network(self):
        counts={}
        def transport(job,payload):
            n=counts.get(job,0)+1; counts[job]=n
            content=(self.contract if n==1 else
                json.dumps({"kind":"read_cache","cache_id":"v000"}) if n<=21 else
                json.dumps({"kind":"run_sql","sql":self.sql}) if n==22 else
                json.dumps({"kind":"answer","text":"7"}))
            return self.provider(content)
        before=audit.hash_file(self.db)
        result=self.run_batch(transport)
        self.assertEqual(result["status"],"SCRIPTED_BATCH_FINISHED")
        self.assertEqual(result["scripted_dispatches"],276)
        self.assertTrue(all(r["status"]=="ANSWERED" for r in result["rows"]))
        self.assertEqual(sum(r["tool_summary"]["sql_calls"] for r in result["rows"]),12)
        self.assertEqual(audit.hash_file(self.db),before)
        verify(self.root/"run/batch")
        with self.assertRaises(ValueError): self.run_batch(transport)

    def test_provider_failure_preserves_fixed_denominator_and_raw(self):
        def transport(job,payload): return self.provider(self.contract)|{"model":"wrong"}
        result=self.run_batch(transport)
        self.assertEqual(result["scripted_dispatches"],1)
        self.assertEqual(result["rows"][0]["status"],"FAILED")
        self.assertEqual(sum(r["status"]=="NOT_STARTED" for r in result["rows"]),11)
        self.assertTrue((self.root/"run/h200a-p1/trace/http-g001.provider.json").is_file())

    def test_invalid_contract_is_not_retried_or_silently_excluded(self):
        result=self.run_batch(lambda job,payload:self.provider("not-json"))
        self.assertEqual(result["scripted_dispatches"],12)
        self.assertTrue(all(r["status"]=="INVALID_CONTRACT" for r in result["rows"]))
        self.assertEqual(result["semantic_judgments"],0)

    def test_changed_cache_rejected_before_output_creation(self):
        self.caches["h200a"]["rows"]=[[["integer","8"]]]
        self.caches["h200a"].update(audit.fingerprints(self.caches["h200a"]["rows"]))
        with self.assertRaises(ValueError): self.run_batch(lambda *args:self.fail("must not dispatch"))
        self.assertFalse((self.root/"run").exists())

    def test_batch_deadline_stops_even_after_provider_returns(self):
        now=[0]
        def transport(job,payload):
            now[0]=1801
            return self.provider(self.contract)
        result=self.run_batch(transport,clock=lambda:now[0])
        self.assertEqual(result["stop_reason"],"TimeoutError")
        self.assertEqual(result["scripted_dispatches"],1)
        self.assertEqual(sum(r["status"]=="NOT_STARTED" for r in result["rows"]),11)


if __name__=="__main__": unittest.main()
