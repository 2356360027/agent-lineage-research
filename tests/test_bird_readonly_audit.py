import copy
from pathlib import Path
import sqlite3
import tempfile
import unittest

from runtime import bird_readonly_audit as b


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "space # 测试.sqlite"
        conn = sqlite3.connect(self.path)
        conn.executescript("CREATE TABLE t(x); INSERT INTO t VALUES (1),(1),(2),(NULL),('');")
        conn.close()

    def test_readonly_path_uri_preserves_data(self):
        before = b.hash_file(self.path)
        out = b.execute_readonly(self.path, "SELECT x FROM t")
        self.assertEqual(out["status"], "EXECUTED")
        self.assertEqual(out["retained_rows"], 5)
        self.assertEqual(out["duplicate_rows"], 1)
        self.assertEqual(before, b.hash_file(self.path))

    def test_denies_writes_attachments_pragmas_and_extension(self):
        for sql in ("DELETE FROM t", "CREATE TABLE z(x)", "DROP TABLE t",
                    "ATTACH DATABASE ':memory:' AS other", "PRAGMA query_only=OFF",
                    "SELECT load_extension('x')", "VACUUM", "SELECT random()",
                    "SELECT * FROM pragma_table_info('t')"):
            with self.subTest(sql=sql):
                out = b.execute_readonly(self.path, sql)
                self.assertFalse(out["complete"])
                self.assertNotEqual(out["status"], "EXECUTED")

    def test_multiple_statements_rejected(self):
        self.assertFalse(b.execute_readonly(self.path, "SELECT 1; DELETE FROM t;")["complete"])

    def test_output_row_limit_is_not_complete(self):
        out = b.execute_readonly(self.path, "SELECT x FROM t", b.LIMITS | {"max_rows": 2})
        self.assertEqual(out["status"], "OUTPUT_LIMIT")
        self.assertFalse(out["complete"])
        self.assertNotIn("bag_sha256", out)

    def test_output_byte_limit(self):
        out = b.execute_readonly(self.path, "SELECT x FROM t", b.LIMITS | {"max_result_bytes": 1})
        self.assertEqual(out["status"], "OUTPUT_LIMIT")

    def test_recursive_query_soft_timeout(self):
        sql = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT SUM(x) FROM n"
        out = b.execute_readonly(self.path, sql, b.LIMITS | {"soft_seconds": 0.01})
        self.assertEqual(out["status"], "SOFT_TIMEOUT")
        self.assertFalse(out["complete"])

    def test_child_hard_timeout(self):
        out = b.run_child(self.path, "SELECT 1", b.LIMITS | {"hard_seconds": 0.001})
        self.assertEqual(out["status"], "HARD_TIMEOUT")

    def test_child_sqlite_heap_limit_before_sort_returns(self):
        sql = ("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n "
               "WHERE x<1000000) SELECT x FROM n ORDER BY x DESC")
        out = b.run_child(self.path, sql, b.LIMITS | {"sqlite_heap_bytes": 2*1024*1024})
        self.assertEqual(out["status"], "SQLITE_MEMORY_LIMIT")
        self.assertFalse(out["complete"])
        self.assertNotIn("bag_sha256", out)

    def test_bag_sequence_and_type_sensitivity(self):
        rows = [[b.typed(x)] for x in (1, 1, 2)]
        normal = b.fingerprints(rows)
        reordered = b.fingerprints(rows[::-1])
        unique = b.fingerprints(rows[1:])
        self.assertEqual(normal["bag_sha256"], reordered["bag_sha256"])
        self.assertNotEqual(normal["sequence_sha256"], reordered["sequence_sha256"])
        self.assertNotEqual(normal["bag_sha256"], unique["bag_sha256"])
        self.assertNotEqual(b.typed(None), b.typed(""))
        self.assertNotEqual(b.typed(1), b.typed(1.0))
        self.assertNotEqual(b.typed(b"x"), b.typed("x"))

    def test_missing_db_and_sidecar(self):
        missing = self.path.with_name("absent.sqlite")
        self.assertEqual(b.execute_readonly(missing, "SELECT 1")["status"], "MISSING_DATABASE")
        self.assertFalse(missing.exists())
        Path(str(self.path)+"-wal").write_bytes(b"fixture")
        self.assertEqual(b.execute_readonly(self.path, "SELECT 1")["status"], "SIDECAR_PRESENT")

    def test_empty_complete_result(self):
        out = b.execute_readonly(self.path, "SELECT x FROM t WHERE 0")
        self.assertTrue(out["complete"])
        self.assertEqual(out["rows"], [])

    def test_metadata_only_selection_deterministic(self):
        rows = [{"db_id": f"d{i//6}", "question_id": i, "difficulty": ["simple", "moderate", "challenging"][i%3],
                 "SQL": "SELECT 1", "question": "fixture"} for i in range(36)]
        dbs, selected = b.select(rows)
        _, reversed_input = b.select(rows[::-1])
        self.assertEqual(selected, reversed_input)
        self.assertEqual(len(dbs), 4)
        self.assertEqual(len(selected), 12)
        mutated = copy.deepcopy(rows)
        for row in mutated:
            row["SQL"] = "Invalid"
        _, selected2 = b.select(mutated)
        self.assertEqual([r["question_id"] for r in selected], [r["question_id"] for r in selected2])

    def test_child_success(self):
        out = b.run_child(self.path, "SELECT COUNT(*) FROM t")
        self.assertEqual(out["status"], "EXECUTED")
        self.assertEqual(out["rows"], [[["integer", "5"]]])

    def test_download_receipt_required_and_hash_bound(self):
        root = Path(self.temp.name) / "bundle"
        path = root / "databases" / "d0" / "d0.sqlite"
        path.parent.mkdir(parents=True)
        path.write_bytes(self.path.read_bytes())
        b.save_new(root / "questions.jsonl", [])
        b.save_new(root / "selected_db_ids.json", ["d0"])
        b.save_new(root / "source_manifest.json", {"files": {"questions.jsonl": {"sha256": b.hash_file(root / "questions.jsonl")}}})
        manifest = {"stage": "databases_complete", "selected_db_ids": ["d0"],
                    "source_manifest": {"sha256": b.hash_file(root / "source_manifest.json")},
                    "selection_file": {"sha256": b.hash_file(root / "selected_db_ids.json")},
                    "databases": [{"db_id": "d0", "path": "databases/d0/d0.sqlite",
                                   "sha256": b.hash_file(path), "bytes": path.stat().st_size}]}
        b.save_new(root / "dataset_manifest.json", manifest)
        b.verify_download(root, ["d0"], {"d0": b.hash_file(path)})
        with self.assertRaisesRegex(ValueError, "differs from downloaded"):
            b.verify_download(root, ["d0"], {"d0": "0"*64})


if __name__ == "__main__":
    unittest.main()
