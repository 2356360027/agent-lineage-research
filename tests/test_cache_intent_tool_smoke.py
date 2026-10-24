"""Harness tests with tiny local byte fixtures and mocked SQL/input acquisition."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from runtime import cache_intent_tool_smoke as smoke


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "not-executed.sqlite"
        self.database.write_bytes(b"Synthetic bytes; backend is mocked and never opens this file")
        self.output = self.root / "run"
        sql = "SELECT 1"
        rows = [[["integer", "1"]]]
        self.record = {"status": "EXECUTED", "complete": True, "columns": ["1"],
                       "rows": rows, "sql_sha256": smoke.audit.digest(sql),
                       **smoke.audit.fingerprints(rows)}
        self.bound = {"database_path": self.database, "database_sha256": smoke.audit.hash_file(self.database),
                      "history_sql": sql, "cached_result": self.record,
                      "input_receipt": {"synthetic_fixture": True}}
        self.loader = mock.patch.object(smoke.inputs, "load_inputs", return_value=self.bound).start()
        self.addCleanup(mock.patch.stopall)
        self.child = mock.patch.object(smoke.audit, "run_child", return_value=self.record).start()
        mock.patch("sqlite3.connect", side_effect=AssertionError("No SQLite in smoke fixtures")).start()
        mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No network")).start()

    def test_plan_has_no_loading_output_sql_or_model(self):
        with mock.patch("sys.argv", ["smoke", "--output", str(self.output)]), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            smoke.main()
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "PLAN_NO_EXECUTION")
        self.assertEqual(result["max_new_readonly_sql_calls"], 3)
        self.assertFalse(self.output.exists())
        self.loader.assert_not_called(); self.child.assert_not_called()

    def test_three_sessions_execute_same_history_exactly_once_each(self):
        result = smoke.run("package", "bird", self.output)
        self.loader.assert_called_once_with("package", "bird", "h200a")
        self.assertEqual(self.child.call_count, 3)
        for call in self.child.call_args_list:
            self.assertEqual(call.args, (self.database, "SELECT 1"))
        self.assertEqual(result["declared_units_by_session"], [10, 11, 18])
        self.assertEqual(result["new_readonly_sql_calls"], 3)
        self.assertEqual(result["attempts"], 6)
        self.assertTrue(result["checks_passed"])
        self.assertEqual(result["independent_research_samples"], 0)
        self.assertEqual(result["model_calls"], 0)
        self.assertFalse(result["inference_eligible"])
        hashes = json.loads((self.output / "artifact_hashes.json").read_text("utf-8"))
        self.assertEqual(set(hashes), {p.relative_to(self.output).as_posix()
            for p in self.output.rglob("*") if p.is_file() and p != self.output / "artifact_hashes.json"})
        for name, expected in hashes.items():
            self.assertEqual(smoke.audit.hash_file(self.output / name), expected)

    def test_existing_output_is_refused_before_loading_or_execution(self):
        self.output.mkdir(); marker = self.output / "keep"; marker.write_bytes(b"unchanged")
        with self.assertRaises(ValueError): smoke.run("package", "bird", self.output)
        self.assertEqual(marker.read_bytes(), b"unchanged")
        self.loader.assert_not_called(); self.child.assert_not_called()

    def test_first_backend_failure_stops_and_retains_failure_without_retry(self):
        self.child.return_value = {"status": "HARD_TIMEOUT", "complete": False}
        with self.assertRaisesRegex(ValueError, "wiring check failed"):
            smoke.run("package", "bird", self.output)
        self.assertEqual(self.child.call_count, 1)
        self.assertTrue((self.output / "session-0/summary.json").is_file())
        self.assertFalse((self.output / "session-1").exists())
        self.assertFalse((self.output / "summary.json").exists())
        failure = json.loads((self.output / "failure.json").read_text("utf-8"))
        self.assertEqual(failure["completed_session_checks"], 1)
        self.assertFalse(failure["automatic_retry"])

    def test_interrupted_backend_retains_unresolved_attempt(self):
        self.child.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): smoke.run("package", "bird", self.output)
        self.child.assert_called_once()
        summary = json.loads((self.output / "session-0/summary.json").read_text("utf-8"))
        self.assertEqual(summary["status"], "UNRESOLVED_ATTEMPTS")
        self.assertEqual(summary["unresolved_attempts"], [2])
        self.assertEqual(summary["spent_declared_units"], 10)
        self.assertFalse((self.output / "summary.json").exists())
        self.assertTrue((self.output / "failure.json").is_file())

    def test_final_manifest_partial_write_is_failure_not_a_successful_run(self):
        original_save = smoke.audit.save_new
        def write(path, value):
            if path == self.output / "artifact_hashes.json":
                path.write_bytes(b'{"partial":')
                raise OSError("MOCK_ROOT_MANIFEST_FAILURE")
            return original_save(path, value)
        with mock.patch.object(smoke.audit, "save_new", side_effect=write), \
                self.assertRaisesRegex(OSError, "ROOT_MANIFEST"):
            smoke.run("package", "bird", self.output)
        self.assertEqual(self.child.call_count, 3)
        self.assertEqual((self.output / "artifact_hashes.json").read_bytes(), b'{"partial":')
        self.assertTrue((self.output / "summary.json").is_file())
        self.assertEqual(json.loads((self.output / "failure.json").read_text("utf-8"))[
            "completed_session_checks"], 3)
        before = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob("*") if p.is_file()}
        with self.assertRaises(ValueError): smoke.run("package", "bird", self.output)
        self.assertEqual(before, {p.relative_to(self.output): p.read_bytes()
                                 for p in self.output.rglob("*") if p.is_file()})

    def test_failed_failure_receipt_never_masks_original_interruption(self):
        original_save = smoke.audit.save_new
        self.child.side_effect = KeyboardInterrupt("MOCK_ORIGINAL_INTERRUPT")
        def write(path, value):
            if path == self.output / "failure.json": raise OSError("MOCK_FAILURE_RECEIPT")
            return original_save(path, value)
        with mock.patch.object(smoke.audit, "save_new", side_effect=write), \
                self.assertRaisesRegex(KeyboardInterrupt, "ORIGINAL_INTERRUPT"):
            smoke.run("package", "bird", self.output)
        self.child.assert_called_once()
        self.assertFalse((self.output / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
