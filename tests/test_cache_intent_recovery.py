"""V2 software fixtures, no model calls or research-task labels."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from runtime import bird_readonly_audit as audit
from runtime import cache_intent_tools as tools
from runtime import cache_intent_recovery_preview as preview
from runtime import cache_intent_staged_loop as loop
from runtime import cache_intent_prompts as prompts
from runtime.cache_intent_observation_bound import ObservationBound


class FakeSession:
    def __init__(self,price=1):
        self.price=price; self.attempts=0; self.balance=210; self.calls=[]
    def describe(self):
        return dict(cache_id="v000",materialized_available=True,read_cost=self.price,
            sql_call_cost=10,budget_profile=tools.RECOVERY_PROFILE,
            nonbinding_for_configured_horizon=True,initial_budget=210,remaining_budget=self.balance,
            max_attempts=21,attempts_used=self.attempts,cost_basis="declared_relative_units",
            database_sha256="a"*64,exploration_attempts=20,exploration_allowance=200,
            recovery_allowance=10,last_attempt_sql_only=True)
    def call(self,name,args):
        self.calls.append((name,args)); self.attempts+=1
        cost=self.price if name=="read_cache" else 10
        self.balance-=cost
        return dict(status="EXECUTED",complete=True,charged_units=cost,
                    remaining_budget=self.balance,attempt=self.attempts,columns=["x"],rows=[])


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.pair=preview.make_pair("List IDs",None,["CREATE TABLE x(id TEXT)"],
            {"historical_request":"List IDs","sql":"SELECT id FROM x"},"a"*64)
        self.contract=json.dumps(dict.fromkeys(prompts.CONTRACT_FIELDS,"fixture"))
        self.sql='{"kind":"run_sql","sql":"SELECT 1"}'
        self.cache='{"kind":"read_cache","cache_id":"v000"}'
        self.answer='{"kind":"answer","text":"fixture"}'
        self.records={}; self.requests=[]; self.session=FakeSession()

    def save(self,key,value):
        if key in self.records: raise FileExistsError(key)
        self.records[key]=deepcopy(value)

    def run_script(self,responses,count=lambda messages:100):
        iterator=iter(responses)
        def generate(request):
            self.requests.append(deepcopy(request))
            return {"content":next(iterator),"finish_reason":"stop"}
        return loop.run_recovery_trajectory(self.pair[0],self.session,generate,self.save,ObservationBound(count))

    def test_price_is_only_initial_difference(self):
        self.assertEqual(prompts._diff(*(prompts._expanded(x) for x in self.pair)),
                         [("messages",1,"content","context","cache","read_cost")])
        self.assertIn("separate reserved 10 units",preview.SYSTEM)
        self.assertNotIn("within the 20-attempt horizon",preview.SYSTEM)

    def test_full_exploration_spend_preserves_one_sql_and_answer(self):
        result=self.run_script([self.contract]+[self.sql]*21+[self.answer])
        self.assertEqual(result["status"],"ANSWERED")
        self.assertEqual(result["generation_calls"],23)
        self.assertEqual(self.session.attempts,21)
        self.assertEqual(self.session.balance,0)
        self.assertEqual(self.requests[-2]["stage"],"recovery")
        self.assertEqual(self.requests[-1]["stage"],"terminal")
        self.assertEqual(self.records["t020.response"]["observation"]["remaining_budget"],10)
        self.assertEqual(self.records["t021.pending"]["generation_id"],"g022")

    def test_recovery_disallows_cache_and_terminal_disallows_second_sql(self):
        result=self.run_script([self.contract]+[self.cache]*21)
        self.assertEqual(result["status"],"RECOVERY_ACTION_VIOLATION")
        self.assertEqual(self.session.attempts,20)
        self.setUp()
        result=self.run_script([self.contract]+[self.cache]*20+[self.sql]*2)
        self.assertEqual(result["status"],"TOOL_HORIZON_VIOLATION")
        self.assertEqual(self.session.attempts,21)

    def test_early_answer_does_not_force_recompute(self):
        result=self.run_script([self.contract,self.answer])
        self.assertEqual(result["status"],"ANSWERED")
        self.assertEqual(self.session.attempts,0)

    def test_exposure_failure_keeps_raw_and_does_not_retry(self):
        with self.assertRaises(ValueError):
            self.run_script([self.contract,self.cache],count=lambda messages:999)
        self.assertIn("t001.response",self.records)
        self.assertIn("t001.exposure_failure",self.records)
        self.assertNotIn("t001.exposure",self.records)
        self.assertNotIn("g003.pending",self.records)

    def test_oversize_whole_result_is_withheld_not_row_selected(self):
        original_call=self.session.call
        def large(name,args):
            result=original_call(name,args)
            result["rows"]=[[["text","oversize-marker"]]]
            return result
        self.session.call=large
        count=lambda messages:600 if "oversize-marker" in messages[-1]["content"] else 100
        self.run_script([self.contract,self.cache,self.answer],count=count)
        self.assertIn("oversize-marker",str(self.records["t001.response"]))
        self.assertNotIn("oversize-marker",str(self.requests[-1]))
        exposure=self.records["t001.exposure"]
        self.assertEqual(exposure["observation"]["status"],"OBSERVATION_TOO_LARGE")
        self.assertFalse(exposure["observation"]["complete"])
        self.assertNotIn("rows",exposure["observation"])
        self.assertEqual(exposure["raw_observation_sha256"],audit.digest(self.records["t001.response"]["observation"]))

    def test_small_observation_unchanged_and_invalid_counter_rejected(self):
        raw=FakeSession().call("read_cache",{})
        self.assertEqual(ObservationBound(lambda m:100).apply(raw)["observation"],raw)
        for value in (True,0,-1,1.5):
            with self.assertRaises(ValueError): ObservationBound(lambda m:value).apply(raw)


class RealToolReserveTests(unittest.TestCase):
    def test_real_readonly_backend_reserve_for_both_prices_and_max_spend(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); db=root/"fixture.sqlite"
            conn=sqlite3.connect(db)
            conn.execute("CREATE TABLE x(id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO x VALUES ('fixture')")
            conn.commit(); conn.close()
            sql="SELECT id FROM x"
            cached=audit.run_child(db,sql)
            self.assertTrue(cached["complete"])
            cached["sql_sha256"]=audit.digest(sql)
            fingerprint=audit.hash_file(db)
            for price in (1,8):
                for action in ("read_cache","run_sql"):
                    with self.subTest(price=price,action=action):
                        session=tools.ToolSession(database_path=db,database_sha256=fingerprint,
                            history_sql=sql,cached_result=cached,input_receipt={"fixture":True},
                            output=root/f"{price}-{action}",materialized_available=True,
                            cache_read_cost=price,budget_profile=tools.RECOVERY_PROFILE)
                        try:
                            args={"cache_id":"v000"} if action=="read_cache" else {"sql":sql}
                            for i in range(20): self.assertTrue(session.call(action,args)["complete"])
                            self.assertGreaterEqual(session.balance,10)
                            recovered=session.call("run_sql",{"sql":sql})
                            self.assertTrue(recovered["complete"])
                            self.assertEqual(recovered["rows"],cached["rows"])
                            with self.assertRaises(RuntimeError): session.call("run_sql",{"sql":sql})
                        finally:
                            summary=session.close()
                        self.assertEqual(summary["status"],"TOOL_SESSION_CLOSED")
                        self.assertEqual(summary["attempts"],21)
                        self.assertEqual(summary["spent_declared_units"],
                                         210 if action=="run_sql" else 20*price+10)
            self.assertEqual(audit.hash_file(db),fingerprint)


if __name__ == "__main__": unittest.main()
