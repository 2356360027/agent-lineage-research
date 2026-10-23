"""Scripted generation + REAL tiny SQLite/ToolSession/disk journal.

No model or research database is used. Temporary fixture files are cleaned.
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_journal as journal
from runtime import cache_intent_tools as tools
from runtime import cache_intent_staged_preview as preview
from runtime import cache_intent_staged_loop as loop
from runtime.cache_intent_prompts import CONTRACT_FIELDS


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_real_sql_cache_both_prices_and_disk_linkage(self):
        database = self.root / "synthetic.sqlite"
        with sqlite3.connect(database) as conn:
            conn.execute("CREATE TABLE items(id TEXT PRIMARY KEY)")
            conn.executemany("INSERT INTO items VALUES(?)", [("fixture-a",),("fixture-b",)])
        conn.close()
        fingerprint = audit.hash_file(database)
        sql = "SELECT id FROM items ORDER BY id"
        cached = audit.run_child(database, sql)
        self.assertTrue(cached["complete"])
        # The acquisition layer binds SQL; raw run_child returns execution only.
        # Match cache_intent_dev_package, never weaken ToolSession verification.
        cached["sql_sha256"] = audit.digest(sql)
        pair = preview.make_pair("List item IDs", None,
            ["CREATE TABLE items(id TEXT PRIMARY KEY)"],
            {"historical_request":"List item IDs", "sql":sql}, fingerprint)
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No network")):
            for index, cost in enumerate((1,8)):
                with self.subTest(cost=cost):
                    session = tools.ToolSession(database_path=database,
                        database_sha256=fingerprint, history_sql=sql, cached_result=cached,
                        input_receipt={"synthetic_fixture_only":True}, output=self.root/f"tools-{cost}",
                        materialized_available=True, cache_read_cost=cost,
                        budget_profile=tools.NONBINDING_PROFILE)
                    log = journal.Journal(self.root/f"trace-{cost}")
                    responses = iter([
                        json.dumps(dict.fromkeys(CONTRACT_FIELDS,"fixture")),
                        json.dumps({"kind":"read_cache","cache_id":"v000"}),
                        json.dumps({"kind":"run_sql","sql":sql}),
                        json.dumps({"kind":"answer","text":"fixture-a, fixture-b"})])
                    try:
                        result = loop.run_trajectory(pair[index], session,
                            lambda request: {"content":next(responses),"finish_reason":"stop",
                                             "backend":"SCRIPTED_NOT_MODEL", "usage":None}, log)
                    finally:
                        tool_summary = session.close()
                    self.assertEqual(result["status"],"ANSWERED")
                    self.assertEqual(tool_summary["spent_declared_units"],cost+10)
                    self.assertEqual(tool_summary["sql_calls"],1)
                    self.assertEqual(journal.verify(session.output)["status"],"INTEGRITY_VERIFIED_ONLY")
                    self.assertEqual(log.seal()["status"],"INTEGRITY_VERIFIED_ONLY")
                    read = lambda name: json.loads((log.directory/name).read_text("utf-8"))
                    self.assertEqual(read("t001.response.json")["observation"]["rows"],
                                     read("t002.response.json")["observation"]["rows"])
                    for number in range(1,5):
                        pending = read(f"g{number:03d}.pending.json")
                        response = read(f"g{number:03d}.response.json")
                        self.assertEqual(response["request_sha256"],audit.digest(pending))
                    self.assertEqual(read("t002.pending.json")["generation_id"],"g003")
        self.assertEqual(audit.hash_file(database), fingerprint)

    def test_no_overwrite_or_silent_reopen(self):
        log = journal.Journal(self.root/"journal")
        log("started", {"fixture":True})
        original = (log.directory/"started.json").read_bytes()
        with self.assertRaises(FileExistsError): log("started", {"fixture":False})
        self.assertEqual((log.directory/"started.json").read_bytes(), original)
        with self.assertRaises(RuntimeError): log.seal()
        with self.assertRaises(ValueError): journal.Journal(log.directory)

    def test_mutation_or_extra_file_breaks_seal_verification(self):
        log = journal.Journal(self.root/"journal")
        log("started", {"fixture":True})
        log.seal()
        with self.assertRaises(RuntimeError): log("late", {})
        (log.directory/"started.json").write_bytes(b"{}")
        with self.assertRaises(ValueError): journal.verify(log.directory)

    def test_pending_failure_is_preserved_but_not_completion(self):
        log = journal.Journal(self.root/"journal")
        log("g001.pending", {"fixture":True})
        log("g001.failure", {"type":"TimeoutError", "retry":False})
        self.assertEqual(log.seal()["files"],2)
        self.assertFalse((log.directory/"summary.json").exists())

    def test_extra_file_breaks_verification(self):
        log = journal.Journal(self.root/"journal")
        log("started", {})
        log.seal()
        (log.directory/"extra.json").write_bytes(b"{}")
        with self.assertRaises(ValueError): journal.verify(log.directory)


if __name__ == "__main__": unittest.main()
