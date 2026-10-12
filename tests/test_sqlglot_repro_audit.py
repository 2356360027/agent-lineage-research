import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from runtime import bird_readonly_audit as ro
from runtime import sqlglot_repro_audit as audit


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, rows, name="t.sqlite"):
        path = self.root / name
        with closing(sqlite3.connect(path)) as con:
            con.execute("CREATE TABLE t(id INTEGER, x INTEGER, g TEXT)")
            con.executemany("INSERT INTO t VALUES(?,?,?)", rows)
            con.commit()
        return path

    def test_incomplete_is_not_equivalence_or_inequivalence(self):
        for a, b in (({}, {}), ({"complete": True}, {"complete": False})):
            self.assertEqual(audit.compare(a, b), {"status": "UNRESOLVED_INCOMPLETE"})

    def test_bag_sequence_and_column_names_separate(self):
        db = self.fixture([[1, 1, "A"], [2, 1, "A"], [3, 2, "B"]])
        a = ro.execute_readonly(db, "SELECT x AS old FROM t ORDER BY id")
        b = ro.execute_readonly(db, "SELECT x AS new FROM t ORDER BY id DESC")
        compare = audit.compare(a, b)
        self.assertTrue(compare["typed_bag_equal"])
        self.assertFalse(compare["typed_sequence_equal"])
        self.assertFalse(compare["column_names_equal"])
        distinct = ro.execute_readonly(db, "SELECT DISTINCT x FROM t")
        self.assertFalse(audit.compare(a, distinct)["typed_bag_equal"])

    def test_authored_control_expectations_and_data_unchanged(self):
        cases = audit.controls()
        self.assertEqual(len(cases), 8)
        self.assertEqual(len({c["id"] for c in cases}), 8)
        for case in cases:
            for state in ("base", "counter"):
                with self.subTest(case=case["id"], state=state):
                    db = self.fixture(case[state], case["id"] + state + ".sqlite")
                    before = ro.hash_file(db)
                    a, b = [ro.execute_readonly(db, case[arm]) for arm in ("a", "b")]
                    self.assertTrue(a["complete"], a)
                    self.assertTrue(b["complete"], b)
                    expected_equal = state == "base" or case["kind"] != "negative"
                    self.assertEqual(audit.compare(a, b)["typed_bag_equal"], expected_equal)
                    self.assertEqual(before, ro.hash_file(db))

    def test_schema_metadata_quotes_identifier_and_no_invented_constraints(self):
        db = self.root / "schema.sqlite"
        with closing(sqlite3.connect(db)) as con:
            con.execute('CREATE TABLE "odd\"\"table"("x" INTEGER, "y")')
            con.commit()
        before = ro.hash_file(db)
        result = audit.schema_readonly(db)
        self.assertEqual(result["schema"], {'odd"table': {"x": "INTEGER", "y": "BLOB"}})
        self.assertEqual(before, ro.hash_file(db))
        self.assertIn("not guarantees", result["typing_caution"])

    def test_schema_sidecar_is_rejected(self):
        db = self.fixture([])
        Path(str(db) + "-wal").touch()
        with self.assertRaisesRegex(ValueError, "sidecar"):
            audit.schema_readonly(db)

    def test_bad_wheel_is_rejected(self):
        wheel = self.root / "wrong.whl"
        wheel.write_bytes(b"not verified upstream")
        with self.assertRaisesRegex(ValueError, "Unverified"):
            audit.load_upstream(wheel)

    def test_transform_timeout_preserves_original_and_failed_modes(self):
        with patch.object(audit.subprocess, "run", side_effect=subprocess.TimeoutExpired("test", 15)):
            result = audit.transform_child("SELECT 1", {}, "test.whl")
        self.assertEqual(result["original"]["sql"], "SELECT 1")
        self.assertEqual(result["optimize"]["status"], "TRANSFORM_FAILED")
        self.assertEqual(result["parse_render"]["status"], "TRANSFORM_FAILED")

    def test_evaluate_does_not_run_failed_transforms(self):
        transformed = {"original": {"sql": "SELECT 1", "status": "AVAILABLE"},
                       "parse_render": {"status": "TRANSFORM_ERROR"},
                       "optimize": {"status": "TRANSFORM_ERROR"}}
        with patch.object(audit, "transform_child", return_value=transformed), \
             patch.object(ro, "run_child", return_value={"status": "HARD_TIMEOUT", "complete": False}) as child:
            result = audit.evaluate("SELECT 1", {}, "unused", "unused")
        self.assertEqual(child.call_count, 1)
        self.assertEqual(result["comparisons"]["optimize"]["status"], "UNRESOLVED_INCOMPLETE")

    def test_started_batch_cannot_be_repeated(self):
        ro.save_new(self.root / "design.json", {})
        ro.save_new(self.root / "execution_started.json", {})
        with self.assertRaisesRegex(ValueError, "Refuse repeat"):
            audit.run(self.root)

    def test_save_is_exclusive(self):
        path = self.root / "immutable.json"
        ro.save_new(path, {"original": True})
        with self.assertRaises(FileExistsError):
            ro.save_new(path, {"original": False})
        self.assertEqual(json.loads(path.read_text()), {"original": True})


if __name__ == "__main__":
    unittest.main()
