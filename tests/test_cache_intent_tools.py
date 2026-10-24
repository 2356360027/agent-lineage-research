"""Local software tests only: no research DB, model, network or semantic review.

Each session uses a newly created temporary SQLite fixture. The backend is
mocked by default; one integration test invokes the isolated read-only worker
twice on that temporary fixture only (SELECT and a denied DELETE).
"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from runtime import cache_intent_tools as tools


REAL_RUN_CHILD = tools.audit.run_child


def result(values=((1,), (2,)), columns=None):
    rows = [[tools.audit.typed(value) for value in row] for row in values]
    return {"status": "EXECUTED", "complete": True,
            "columns": ["id"] if columns is None else columns, "rows": rows,
            **tools.audit.fingerprints(rows)}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class ResultTests(unittest.TestCase):
    def test_typed_complete_result_preserves_null_blob_numeric_text_and_duplicates(self):
        value = result([(None, b"a", 1, 1.0, "1"), (None, b"a", 1, 1.0, "1")],
                       ["null", "blob", "integer", "real", "text"])
        before = deepcopy(value)
        self.assertIsNone(tools.valid_result(value))
        self.assertEqual(value, before)
        self.assertIsNone(tools.valid_result(result([])))

    def test_incomplete_shape_type_and_fingerprint_errors_are_rejected(self):
        changes = [{"complete": False}, {"status": "DENIED"}, {"columns": []}, {"rows": [[['integer', '1'], ['integer', '2']]]},
                   {"rows": [[["integer", "01"]]]}, {"rows": [[["integer", True]]]},
                   {"rows": [[["null", "null"]]]}, {"rows": [[["blob", "ABC"]]]},
                   {"rows": [[["real", "1.0"]]]}, {"rows": [[["real", "inf"]]]},
                   {"rows": [[["real", "nan"]]]}, {"rows": [[["integer", str(2**63)]]]},
                   {"rows": [[["unknown", "x"]]]},
                   {"bag_sha256": "0" * 64}, {"sequence_sha256": "0" * 64}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                tools.valid_result(result() | change)

    def test_value_domain_rejections_are_not_masked_by_stale_fingerprints(self):
        invalid_cells = [["real", "nan"], ["real", "inf"], ["real", "-inf"],
                         ["integer", str(2**63)], ["integer", str(-(2**63)-1)],
                         ["integer", "9" * 21],
                         ["text", "x" * (2 * tools.audit.LIMITS["max_value_bytes"] + 1)]]
        for cell in invalid_cells:
            rows = [[cell]]
            record = result() | {"rows": rows} | tools.audit.fingerprints(rows)
            with self.subTest(tag=cell[0], length=len(cell[1])), \
                    self.assertRaisesRegex(ValueError, "Invalid typed value|Typed value too large"):
                tools.valid_result(record)
        self.assertIsNone(tools.valid_result(result([(None,), ("",), (b"",), (0,), (0.0,),
                                                    (-(2**63),), (2**63 - 1,)])))
        self.assertIsNone(tools.valid_result(result([])))


class ToolSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "software-fixture.sqlite"
        conn = sqlite3.connect(self.database)
        conn.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO fixture VALUES (?)", [(1,), (2,)])
        conn.commit(); conn.close()
        self.database_hash = tools.audit.hash_file(self.database)
        self.history_sql = "SELECT id FROM fixture ORDER BY id"
        self.cached = result(); self.cached["sql_sha256"] = tools.audit.digest(self.history_sql)
        self.inputs = {"database_path": self.database, "database_sha256": self.database_hash,
                       "history_sql": self.history_sql, "cached_result": self.cached,
                       "input_receipt": {"software_fixture_only": True, "private_label": "HIDDEN_REVIEW_LABEL"}}
        self.serial = 0
        patcher = mock.patch.object(tools.audit, "run_child", side_effect=lambda *a, **k: deepcopy(self.cached))
        self.child = patcher.start(); self.addCleanup(patcher.stop)

    def session(self, *, available=True, cost=1, **overrides):
        self.serial += 1
        inputs = self.inputs | {"output": self.root / f"session-{self.serial}",
                               "materialized_available": available, "cache_read_cost": cost} | overrides
        return tools.ToolSession(**inputs)

    def assert_journal(self, session, number, expected_charge):
        pending = read_json(session.output / f"attempt-{number:03d}.pending.json")
        completed = read_json(session.output / f"attempt-{number:03d}.completed.json")
        self.assertEqual(pending["charge"], expected_charge)
        self.assertEqual(pending["balance_before"] - pending["balance_after"], expected_charge)
        self.assertFalse(pending["automatic_retry"])
        for key, value in pending.items(): self.assertEqual(completed[key], value)
        self.assertEqual(completed["response"]["charged_units"], expected_charge)
        return completed

    def test_constructor_validates_bound_inputs_and_refuses_existing_output(self):
        for overrides in ({"materialized_available": 1}, {"cache_read_cost": True}, {"cache_read_cost": 2},
                          {"database_sha256": "a"}, {"history_sql": " "},
                          {"history_sql": self.history_sql + " "},
                          {"cached_result": self.cached | {"sequence_sha256": "a" * 64}}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError): self.session(**overrides)
        output = self.root / "existing"; output.mkdir(); (output / "keep").write_bytes(b"keep")
        with self.assertRaises((ValueError, FileExistsError)):
            self.session(output=output)
        self.assertEqual((output / "keep").read_bytes(), b"keep")
        self.child.assert_not_called()

    def test_description_exposes_no_private_inputs_and_factor_is_isolated(self):
        low, high = self.session(available=False), self.session(available=True)
        first, second = low.describe(), high.describe()
        self.assertFalse(first["materialized_available"])
        first["materialized_available"] = True
        self.assertEqual(first, second)
        cheap, expensive = self.session(cost=1).describe(), self.session(cost=8).describe()
        cheap["read_cost"] = 8; self.assertEqual(cheap, expensive)
        for state in (first, second, cheap, expensive):
            self.assertEqual(state["cost_basis"], "declared_relative_units")
            self.assertEqual(state["database_sha256"], self.database_hash)
            for private in ("history_sql", "cached_result", "rows", "input_receipt", "private_label"):
                self.assertNotIn(private, state)
            self.assertNotIn("HIDDEN_REVIEW_LABEL", tools.audit.encoded(state).decode())

    def test_cache_prices_are_enforced_without_backend_calls(self):
        for cost, expected in ((1, 99), (8, 92)):
            session = self.session(cost=cost)
            response = session.call("read_cache", {"cache_id": "v000"})
            self.assertEqual(response["status"], "EXECUTED")
            self.assertTrue(response["complete"])
            self.assertEqual(response["remaining_budget"], expected)
            self.assertEqual(response["rows"], self.cached["rows"])
            self.assert_journal(session, 1, cost)
        self.child.assert_not_called()

    def test_pending_and_charge_exist_before_sql_backend_executes(self):
        session = self.session()
        def execute(db, sql):
            self.assertEqual((db, sql), (self.database, self.history_sql))
            self.assertEqual(session.balance, 90)
            pending = read_json(session.output / "attempt-001.pending.json")
            self.assertEqual(pending["charge"], 10)
            self.assertEqual(pending["balance_after"], 90)
            self.assertFalse((session.output / "attempt-001.completed.json").exists())
            return deepcopy(self.cached)
        self.child.side_effect = execute
        response = session.call("run_sql", {"sql": self.history_sql})
        self.assertEqual(response["remaining_budget"], 90)
        self.assert_journal(session, 1, 10)
        self.child.assert_called_once_with(self.database, self.history_sql)

    def test_unavailable_cache_does_not_prevent_recomputing_the_same_historical_sql(self):
        session = self.session(available=False)
        absent = session.call("read_cache", {"cache_id": "v000"})
        self.assertEqual(absent["status"], "CACHE_UNAVAILABLE")
        self.assertEqual(absent["charged_units"], 0)
        self.assertNotIn("rows", absent); self.child.assert_not_called()
        computed = session.call("run_sql", {"sql": self.history_sql})
        self.assertEqual(computed["rows"], self.cached["rows"])
        self.assertEqual(computed["remaining_budget"], 90)
        again = session.call("read_cache", {"cache_id": "v000"})
        self.assertEqual(again["status"], "CACHE_UNAVAILABLE")
        self.child.assert_called_once()
        self.assert_journal(session, 1, 0); self.assert_journal(session, 2, 10); self.assert_journal(session, 3, 0)

    def test_unknown_tools_invalid_json_shapes_extra_keys_and_empty_sql_are_free_attempts(self):
        requests = [("unknown", {}), (None, {}), ("read_cache", {"cache_id": "other"}),
                    ("read_cache", {"cache_id": "v000", "cost": 0}), ("read_cache", None),
                    ("run_sql", '{"sql": "SELECT 1"}'), ("run_sql", "{invalid JSON"),
                    ("run_sql", {"sql": " "}), ("run_sql", {"sql": 1}),
                    ("run_sql", {"sql": "SELECT 1", "database": "other.sqlite"}),
                    ("run_sql", {"sql": "x" * (tools.audit.LIMITS["max_sql_bytes"] + 1)}),
                    ("run_sql", {"sql": float("nan")}), ("run_sql", {"sql": object()})]
        session = self.session()
        for number, (name, arguments) in enumerate(requests, 1):
            response = session.call(name, arguments)
            self.assertEqual(response["status"], "UNKNOWN_TOOL" if name == "unknown" else "INVALID_REQUEST")
            self.assertEqual(response["charged_units"], 0)
            self.assertEqual(response["remaining_budget"], 100)
            self.assert_journal(session, number, 0)
        self.assertEqual(session.attempts, len(requests)); self.child.assert_not_called()

    def test_invalid_request_cap_prevents_free_infinite_attempts(self):
        session = self.session()
        for _ in range(tools.MAX_ATTEMPTS): session.call("unknown", {})
        with self.assertRaisesRegex(RuntimeError, "limit"): session.call("run_sql", {"sql": self.history_sql})
        self.assertEqual(session.attempts, tools.MAX_ATTEMPTS)
        self.assertEqual(session.balance, 100)
        self.assertFalse((session.output / "attempt-021.pending.json").exists())
        self.child.assert_not_called()

    def test_budget_exhaustion_and_unaffordable_cache_are_free_rejections(self):
        session = self.session()
        for number in range(1, 11):
            response = session.call("run_sql", {"sql": self.history_sql})
            self.assertEqual(response["remaining_budget"], 100 - number * 10)
        for tool, args in (("run_sql", {"sql": self.history_sql}), ("read_cache", {"cache_id": "v000"})):
            response = session.call(tool, args)
            self.assertEqual(response["status"], "BUDGET_EXHAUSTED")
            self.assertEqual(response["charged_units"], 0)
            self.assertEqual(response["remaining_budget"], 0)
        self.assertEqual(self.child.call_count, 10)
        expensive = self.session(cost=8)
        for _ in range(12): expensive.call("read_cache", {"cache_id": "v000"})
        response = expensive.call("read_cache", {"cache_id": "v000"})
        self.assertEqual(response["status"], "BUDGET_EXHAUSTED")
        self.assertEqual(response["remaining_budget"], 4)

    def test_sql_errors_timeouts_and_partial_results_charge_once_without_row_leak(self):
        session = self.session()
        diagnostic = "BOUND_SQL_ERROR_" + "x" * 2000
        for number, status in enumerate(("EXECUTION_ERROR", "DENIED", "SOFT_TIMEOUT", "HARD_TIMEOUT", "OUTPUT_LIMIT"), 1):
            self.child.side_effect = lambda *a, status=status: {
                "status": status, "complete": False, "columns": ["secret"],
                "rows": [[["text", "PARTIAL_PRIVATE_ROW"]]], "error": diagnostic}
            response = session.call("run_sql", {"sql": "SELEC invalid but nonempty SQL"})
            self.assertEqual(response["status"], status)
            self.assertFalse(response["complete"])
            self.assertEqual(response["charged_units"], 10)
            self.assertEqual(response["remaining_budget"], 100 - number * 10)
            for hidden in ("rows", "columns"): self.assertNotIn(hidden, response)
            if status in ("EXECUTION_ERROR", "DENIED"):
                self.assertEqual(response["error"], diagnostic[:1024])
            else:
                self.assertNotIn("error", response)
            self.assertNotIn("PARTIAL_PRIVATE_ROW", tools.audit.encoded(response).decode())
            private = self.assert_journal(session, number, 10)["private"]["backend_result"]
            self.assertEqual(private["rows"], [[["text", "PARTIAL_PRIVATE_ROW"]]])
            self.assertEqual(private["error"], diagnostic)
            self.assertFalse(session.invalidated)
        self.assertEqual(self.child.call_count, 5)

    def test_cache_and_public_response_are_deep_copies_and_sql_does_not_refresh_cache(self):
        session = self.session()
        self.cached["rows"][0][0][1] = "999"
        response = session.call("read_cache", {"cache_id": "v000"})
        self.assertEqual(response["rows"], result()["rows"])
        response["rows"][0][0][1] = "888"
        self.child.side_effect = lambda *a: result([(777,)])
        session.call("run_sql", {"sql": "SELECT 777"})
        self.assertEqual(session.call("read_cache", {"cache_id": "v000"})["rows"], result()["rows"])
        self.assertEqual(session.balance, 88)

    def test_db_tampering_before_session_is_rejected_and_during_session_invalidates(self):
        session = self.session()
        with self.database.open("ab") as handle: handle.write(b"TAMPERING")
        with self.assertRaisesRegex(ValueError, "snapshot"): self.session()
        response = session.call("read_cache", {"cache_id": "v000"})
        self.assertEqual(response["status"], "ENVIRONMENT_INVALID")
        self.assertEqual(response["charged_units"], 1)
        self.assertNotIn("rows", response)
        self.assertTrue(session.invalidated)
        with self.assertRaises(RuntimeError): session.call("read_cache", {"cache_id": "v000"})
        self.child.assert_not_called()
        self.assertEqual(session.close()["status"], "ENVIRONMENT_INVALID")

    def test_after_execution_db_change_preserves_raw_result_privately(self):
        session = self.session()
        returned = result([(456,)])
        def changed_during_backend(*args):
            with self.database.open("ab") as handle: handle.write(b"MOCK_AFTER_EXECUTION_CHANGE")
            return deepcopy(returned)
        self.child.side_effect = changed_during_backend
        response = session.call("run_sql", {"sql": self.history_sql})
        self.assertEqual(response["status"], "ENVIRONMENT_INVALID")
        self.assertEqual(response["charged_units"], 10)
        self.assertNotIn("rows", response)
        recorded = self.assert_journal(session, 1, 10)
        self.assertEqual(recorded["private"]["backend_result"], returned)
        self.assertTrue(recorded["environment_invalidated"])
        self.child.assert_called_once()
        self.assertEqual(session.close()["status"], "ENVIRONMENT_INVALID")

    def test_sidecar_and_corrupt_cache_fingerprint_are_rejected(self):
        sidecar = Path(str(self.database) + "-wal"); sidecar.write_bytes(b"FIXTURE_SIDECAR")
        with self.assertRaises(ValueError): self.session()
        sidecar.unlink()
        corrupt = deepcopy(self.cached); corrupt["rows"][0][0][1] = "9"
        with self.assertRaisesRegex(ValueError, "fingerprint"): self.session(cached_result=corrupt)
        session = self.session()
        session.cache["rows"][0][0][1] = "9"
        response = session.call("read_cache", {"cache_id": "v000"})
        self.assertEqual(response["status"], "ENVIRONMENT_INVALID")
        self.assertNotIn("rows", response)
        self.assertEqual(response["charged_units"], 1)

    def test_bad_complete_backend_digest_invalidates_without_rows_or_retry(self):
        session = self.session()
        self.child.side_effect = lambda *a: result() | {"bag_sha256": "a" * 64}
        response = session.call("run_sql", {"sql": self.history_sql})
        self.assertEqual(response["status"], "ENVIRONMENT_INVALID")
        self.assertNotIn("rows", response)
        self.assertEqual(response["charged_units"], 10)
        self.child.assert_called_once()

    def test_keyboard_interrupt_preserves_charged_pending_without_resume(self):
        session = self.session(); self.child.side_effect = KeyboardInterrupt("SOFTWARE_FIXTURE_INTERRUPT")
        with self.assertRaises(KeyboardInterrupt): session.call("run_sql", {"sql": self.history_sql})
        self.assertEqual(session.balance, 90)
        self.assertTrue(session.invalidated)
        self.assertTrue((session.output / "attempt-001.pending.json").exists())
        self.assertFalse((session.output / "attempt-001.completed.json").exists())
        with self.assertRaises(RuntimeError): session.call("run_sql", {"sql": self.history_sql})
        self.child.assert_called_once()
        summary = session.close()
        self.assertEqual(summary["status"], "UNRESOLVED_ATTEMPTS")
        self.assertEqual(summary["spent_declared_units"], 10)
        self.assertFalse(summary["inference_eligible"])

    def test_completed_journal_write_failure_preserves_pending_and_invalidates(self):
        session = self.session(); original_save = tools.audit.save_new
        def write(path, value):
            if str(path).endswith(".completed.json"): raise OSError("MOCK_COMPLETION_WRITE_FAILURE")
            return original_save(path, value)
        with mock.patch.object(tools.audit, "save_new", side_effect=write), self.assertRaises(OSError):
            session.call("read_cache", {"cache_id": "v000"})
        self.assertEqual(session.balance, 99)
        self.assertTrue(session.invalidated)
        self.assertTrue((session.output / "attempt-001.pending.json").exists())
        self.assertEqual(session.close()["status"], "UNRESOLVED_ATTEMPTS")

    def test_half_written_completed_json_remains_unresolved_and_is_not_overwritten(self):
        session = self.session(); original_save = tools.audit.save_new
        partial_bytes = b'{"attempt":1,"response":'
        def write(path, value):
            if str(path).endswith(".completed.json"):
                with Path(path).open("xb") as handle: handle.write(partial_bytes)
                raise OSError("MOCK_HALF_WRITTEN_COMPLETION")
            return original_save(path, value)
        with mock.patch.object(tools.audit, "save_new", side_effect=write), self.assertRaises(OSError):
            session.call("read_cache", {"cache_id": "v000"})
        completed = session.output / "attempt-001.completed.json"
        self.assertEqual(completed.read_bytes(), partial_bytes)
        self.assertEqual(session.balance, 99)
        summary = session.close()
        self.assertEqual(summary["status"], "UNRESOLVED_ATTEMPTS")
        self.assertEqual(summary["unresolved_attempts"], [1])
        self.assertEqual(completed.read_bytes(), partial_bytes)
        with self.assertRaises(RuntimeError): session.call("read_cache", {"cache_id": "v000"})

    def test_completed_pending_field_and_response_mismatches_are_unresolved(self):
        changes = [("attempt", 2), ("charge", 8), ("balance_before", 99),
                   ("balance_after", 92), ("admission", "UNKNOWN_TOOL"),
                   ("request", {"tool": "unknown", "arguments": {}}),
                   ("automatic_retry", True), ("response.attempt", 2),
                   ("response.charged_units", 8), ("response.remaining_budget", 92)]
        for field, value in changes:
            with self.subTest(field=field):
                session = self.session(); session.call("read_cache", {"cache_id": "v000"})
                path = session.output / "attempt-001.completed.json"
                completed = read_json(path)
                if field.startswith("response."):
                    completed["response"][field.split(".", 1)[1]] = value
                else:
                    completed[field] = value
                path.write_bytes(tools.audit.encoded(completed))
                tampered = path.read_bytes()
                summary = session.close()
                self.assertEqual(summary["status"], "UNRESOLVED_ATTEMPTS")
                self.assertEqual(summary["unresolved_attempts"], [1])
                self.assertEqual(path.read_bytes(), tampered)
                self.assertEqual(summary["spent_declared_units"], 1)

    def test_close_write_hash_failures_and_interrupts_permanently_close_without_overwrite(self):
        original_save, original_hash = tools.audit.save_new, tools.audit.hash_file
        for stage in ("summary", "manifest", "hash"):
            for exception_type in (OSError, KeyboardInterrupt):
                with self.subTest(stage=stage, exception_type=exception_type.__name__):
                    session = self.session(); session.call("read_cache", {"cache_id": "v000"})
                    prior_files = {p.name: p.read_bytes() for p in session.output.iterdir() if p.is_file()}
                    partial_bytes = b'{"software_fixture_close_failure":'
                    def fail_save(path, value):
                        targeted = ((stage == "summary" and Path(path).name == "summary.json") or
                                    (stage == "manifest" and Path(path).name == "artifact_hashes.json"))
                        if targeted:
                            with Path(path).open("xb") as handle: handle.write(partial_bytes)
                            raise exception_type("MOCK_CLOSE_WRITE_FAILURE")
                        return original_save(path, value)
                    def fail_hash(path):
                        if stage == "hash" and Path(path).parent == session.output:
                            raise exception_type("MOCK_CLOSE_HASH_FAILURE")
                        return original_hash(path)
                    with mock.patch.object(tools.audit, "save_new", side_effect=fail_save), \
                            mock.patch.object(tools.audit, "hash_file", side_effect=fail_hash), \
                            self.assertRaises(exception_type):
                        session.close()
                    self.assertTrue(session.closed)
                    self.assertTrue(session.invalidated)
                    for name, value in prior_files.items():
                        self.assertEqual((session.output / name).read_bytes(), value)
                    after_failure = {p.name: p.read_bytes() for p in session.output.iterdir() if p.is_file()}
                    with self.assertRaises(RuntimeError): session.close()
                    with self.assertRaises(RuntimeError): session.call("read_cache", {"cache_id": "v000"})
                    self.assertEqual(after_failure, {p.name: p.read_bytes() for p in session.output.iterdir() if p.is_file()})
                    self.assertEqual(session.balance, 99)
        self.child.assert_not_called()

    def test_close_interrupt_during_initial_database_check_still_blocks_all_reentry(self):
        session = self.session()
        with mock.patch.object(session, "_check_database", side_effect=KeyboardInterrupt("MOCK_CLOSE_INTERRUPT")), \
                self.assertRaises(KeyboardInterrupt):
            session.close()
        self.assertTrue(session.closed)
        self.assertTrue(session.invalidated)
        before = {p.name: p.read_bytes() for p in session.output.iterdir() if p.is_file()}
        with self.assertRaises(RuntimeError): session.close()
        with self.assertRaises(RuntimeError): session.call("run_sql", {"sql": self.history_sql})
        self.assertEqual(before, {p.name: p.read_bytes() for p in session.output.iterdir() if p.is_file()})
        self.child.assert_not_called()

    def test_close_never_overwrites_preexisting_summary_or_manifest(self):
        for name in ("summary.json", "artifact_hashes.json"):
            with self.subTest(name=name):
                session = self.session(); existing = session.output / name
                existing.write_bytes(b"PREEXISTING_KEEP_UNCHANGED")
                with self.assertRaises((FileExistsError, ValueError)): session.close()
                self.assertTrue(session.closed)
                self.assertTrue(session.invalidated)
                self.assertEqual(existing.read_bytes(), b"PREEXISTING_KEEP_UNCHANGED")
                with self.assertRaises(RuntimeError): session.close()
                self.assertEqual(existing.read_bytes(), b"PREEXISTING_KEEP_UNCHANGED")

    def test_pending_write_failure_does_not_execute_or_charge(self):
        session = self.session()
        with mock.patch.object(tools.audit, "save_new", side_effect=OSError("MOCK_PENDING_WRITE_FAILURE")), self.assertRaises(OSError):
            session.call("run_sql", {"sql": self.history_sql})
        self.assertEqual(session.balance, 100)
        self.assertEqual(session.attempts, 0)
        self.assertTrue(session.invalidated)
        with self.assertRaises(RuntimeError): session.call("run_sql", {"sql": self.history_sql})
        self.child.assert_not_called()

    def test_close_freezes_log_hashes_and_never_labels_semantic_or_model_success(self):
        session = self.session(); session.call("read_cache", {"cache_id": "v000"})
        summary = session.close()
        self.assertEqual(summary["status"], "TOOL_SESSION_CLOSED")
        self.assertEqual(summary["spent_declared_units"], 1)
        self.assertEqual(summary["model_calls_by_this_component"], 0)
        self.assertNotIn("model_calls", summary)
        self.assertEqual(summary["semantic_approvals"], 0)
        self.assertFalse(summary["inference_eligible"])
        for name, expected in read_json(session.output / "artifact_hashes.json").items():
            self.assertEqual(tools.audit.hash_file(session.output / name), expected)
        with self.assertRaises(RuntimeError): session.close()
        with self.assertRaises(RuntimeError): session.call("read_cache", {"cache_id": "v000"})

    def test_real_isolated_worker_select_and_denied_delete_on_temporary_fixture_only(self):
        session = self.session(available=False)
        with mock.patch.object(tools.audit, "run_child", side_effect=REAL_RUN_CHILD) as real_child:
            selected = session.call("run_sql", {"sql": self.history_sql})
            deleted = session.call("run_sql", {"sql": "DELETE FROM fixture"})
        self.assertEqual(real_child.call_count, 2)
        self.assertTrue(selected["complete"])
        self.assertEqual(selected["rows"], self.cached["rows"])
        self.assertEqual(deleted["status"], "DENIED")
        self.assertFalse(deleted["complete"])
        self.assertEqual(deleted["charged_units"], 10)
        self.assertEqual(session.balance, 80)
        self.assertEqual(tools.audit.hash_file(self.database), self.database_hash)
        self.assertFalse(session.invalidated)
        self.assertEqual(session.close()["status"], "TOOL_SESSION_CLOSED")


if __name__ == "__main__":
    unittest.main()
