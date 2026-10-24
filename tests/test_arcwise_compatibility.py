"""Software fixtures only; mocked execution is not SQL or research evidence."""
import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from runtime import arcwise_compatibility as a
from runtime import bird_readonly_audit as b


def row(qid=1, db="fixture_db", **changes):
    value = {"question_id": qid, "db_id": db, "question": "Count fixture rows.",
             "evidence": "", "SQL": "SELECT COUNT(*) FROM t", "difficulty": "simple"}
    value.update(changes)
    return value


def executed(values, columns=None):
    typed_rows = [[b.typed(v) for v in values_row] for values_row in values]
    return {"status": "EXECUTED", "complete": True, "rows": typed_rows,
            "columns": columns if columns is not None else ["fixture"],
            **b.fingerprints(typed_rows)}


class RecordTests(unittest.TestCase):
    def test_canonical_ids_and_duplicate_after_normalization(self):
        for value in (0, 23, "0", "23"):
            self.assertEqual(a.canonical_id(value), int(value))
        for value in (True, False, -1, 1.0, None, "01", "+1", " 1", "1 ", "-1", "1.0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                a.canonical_id(value)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            a.index_records([row(1), row("1")])

    def test_index_preserves_source_and_supports_nullable_evidence(self):
        source = [row("1", evidence=None, original_evidence=None, provenance_note="fixture")]
        before = copy.deepcopy(source)
        result = a.index_records(source)
        self.assertEqual(result[1]["question_id"], 1)
        self.assertIsNone(result[1]["evidence"])
        self.assertEqual(source, before)
        self.assertIsNot(result[1], source[0])

    def test_index_rejects_invalid_schema(self):
        invalid = [None, {}, [], [None], ["row"]]
        for missing in ("question_id", "db_id", *a.FIELDS):
            candidate = row()
            candidate.pop(missing)
            invalid.append([candidate])
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                a.index_records(value)

    def test_index_rejects_bad_paths_and_field_types(self):
        for database in ("../db", "db/file", "db\\file", "CON", "", 1, None):
            with self.subTest(database=database), self.assertRaises(ValueError):
                a.index_records([row(db=database)])
        for field in ("question", "SQL", "original_question", "original_SQL"):
            for value in (None, "", "   ", 1, False):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    a.index_records([row(**{field: value})])
        for field in ("evidence", "original_evidence"):
            for value in ([], {}, 1, True):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    a.index_records([row(**{field: value})])

    def test_missing_revised_id_and_changed_database_are_not_probed(self):
        missing = a.describe_pair(row(), None)
        self.assertEqual(missing["match_status"], "MISSING_REVISED_ID")
        self.assertFalse(missing["can_probe"])
        changed = a.describe_pair(row(), row(db="another_db"))
        self.assertEqual(changed["match_status"], "DATABASE_ID_MISMATCH")
        self.assertFalse(changed["can_probe"])
        for result in (missing, changed):
            self.assertFalse(result["semantic_approval"])
            self.assertEqual(result["exposure"], "development_exposed")

    def test_direct_pair_id_mismatch_is_not_probed(self):
        result = a.describe_pair(row(1), row(2))
        self.assertEqual(result["match_status"], "QUESTION_ID_MISMATCH")
        self.assertFalse(result["can_probe"])
        self.assertFalse(result["semantic_approval"])
        normalized = a.describe_pair(row(1), row("1"))
        self.assertTrue(normalized["can_probe"])

    def test_declared_original_fields_and_all_field_changes(self):
        original = row()
        revised = row(question="Count distinct fixture values.", evidence="Duplicates count once.",
                      SQL="SELECT COUNT(DISTINCT x) FROM t")
        for field in a.FIELDS:
            revised["original_" + field] = original[field]
        before = copy.deepcopy((original, revised))
        result = a.describe_pair(original, revised)
        self.assertEqual(result["match_status"], "DECLARED_ORIGINAL_FIELDS_MATCH")
        self.assertEqual(result["changed_fields"], list(a.FIELDS))
        self.assertEqual(result["declared_original_field_matches"], dict.fromkeys(a.FIELDS, True))
        self.assertTrue(result["can_probe"])
        self.assertFalse(result["semantic_approval"])
        self.assertEqual((original, revised), before)

    def test_each_text_field_change_is_not_semantic_approval(self):
        for field in a.FIELDS:
            original = row()
            revised = row(**{field: original[field] + " "})
            result = a.describe_pair(original, revised)
            self.assertEqual(result["changed_fields"], [field])
            self.assertEqual(result["match_status"], "NO_DECLARED_ORIGINAL_FIELDS")
            self.assertFalse(result["semantic_approval"])
        self.assertEqual(a.describe_pair(row(), row())["changed_fields"], [])

    def test_declared_original_mismatch_remains_visible_and_probe_is_diagnostic(self):
        revised = row(original_question="Different upstream wording.", original_SQL=row()["SQL"])
        result = a.describe_pair(row(), revised)
        self.assertEqual(result["match_status"], "BASELINE_SOURCE_MISMATCH")
        self.assertEqual(result["declared_original_field_matches"], {"question": False, "SQL": True})
        self.assertTrue(result["can_probe"])
        self.assertFalse(result["semantic_approval"])
        self.assertTrue(result["same_id_is_not_same_task"])


class ComparisonTests(unittest.TestCase):
    def test_identical_complete_values_do_not_prove_semantics(self):
        original = executed([(1,), (1,), (None,)])
        result = a.compare_outputs(original, copy.deepcopy(original))
        self.assertEqual(result["status"], "COMPLETE_DIAGNOSTIC_ONLY")
        self.assertTrue(result["same_typed_bag"])
        self.assertTrue(result["same_typed_sequence"])
        self.assertTrue(result["same_column_names"])
        self.assertEqual(result["semantic_equivalence"], "NOT_DETERMINED")

    def test_rows_are_bags_not_sets_and_pairings_are_preserved(self):
        for left, right in (([(1,), (1,)], [(1,)]),
                            ([("A", 1), ("B", 2)], [("A", 2), ("B", 1)])):
            with self.subTest(left=left, right=right):
                result = a.compare_outputs(executed(left), executed(right))
                self.assertFalse(result["same_typed_bag"])
                self.assertFalse(result["same_typed_sequence"])

    def test_type_and_column_order_changes_are_not_erased(self):
        for left, right in ((1, 1.0), (1, "1"), (None, ""), (b"a", "a")):
            with self.subTest(left=left, right=right):
                self.assertFalse(a.compare_outputs(executed([(left,)]), executed([(right,)]))["same_typed_bag"])
        result = a.compare_outputs(executed([("A", 1)], ["name", "count"]),
                                   executed([(1, "A")], ["count", "name"]))
        self.assertFalse(result["same_typed_bag"])
        self.assertFalse(result["same_column_names"])

    def test_column_names_and_row_order_are_separate_diagnostics(self):
        original = executed([(1,), (2,)], ["old_name"])
        renamed = executed([(1,), (2,)], ["new_name"])
        result = a.compare_outputs(original, renamed)
        self.assertTrue(result["same_typed_bag"])
        self.assertTrue(result["same_typed_sequence"])
        self.assertFalse(result["same_column_names"])
        reversed_rows = a.compare_outputs(original, executed([(2,), (1,)], ["old_name"]))
        self.assertTrue(reversed_rows["same_typed_bag"])
        self.assertFalse(reversed_rows["same_typed_sequence"])

    def test_incomplete_pair_never_compares_partial_hashes(self):
        complete = executed([(1,)])
        for status in ("SOFT_TIMEOUT", "HARD_TIMEOUT", "OUTPUT_LIMIT", "DENIED", "WORKER_ERROR"):
            partial = {"status": status, "complete": False,
                       "bag_sha256": complete["bag_sha256"], "sequence_sha256": complete["sequence_sha256"]}
            for left, right in ((complete, partial), (partial, complete), (partial, partial)):
                with self.subTest(status=status):
                    result = a.compare_outputs(left, right)
                    self.assertEqual(result["status"], "INCOMPLETE_PAIR")
                    self.assertIsNone(result["same_typed_bag"])
                    self.assertIsNone(result["same_typed_sequence"])
                    self.assertIsNone(result["same_column_names"])
                    self.assertEqual(result["semantic_equivalence"], "NOT_DETERMINED")

    def test_empty_complete_results_are_distinct_from_failures(self):
        empty = executed([])
        self.assertEqual(a.compare_outputs(empty, empty)["status"], "COMPLETE_DIAGNOSTIC_ONLY")
        self.assertEqual(a.compare_outputs(empty, {"complete": False})["status"], "INCOMPLETE_PAIR")


class MockIntegrationTests(unittest.TestCase):
    """All database files are inert fixture bytes; run_child is always mocked."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bird_dir = self.root / "old"
        self.arcwise_dir = self.root / "revised"
        self.bird_dir.mkdir()
        self.arcwise_dir.mkdir()
        questions = [row(i, f"db{i // 125}", difficulty=("simple", "moderate", "challenging")[i % 3],
                         SQL=f"SELECT {i}") for i in range(500)]
        b.save_new(self.bird_dir / "questions.jsonl", questions)
        self.databases, self.selected = b.select(questions)
        b.save_new(self.bird_dir / "selected_db_ids.json", self.databases)
        b.save_new(self.bird_dir / "selection.json", {
            "questions_sha256": b.hash_file(self.bird_dir / "questions.jsonl"),
            "code_sha256": b.hash_file(b.__file__), "selected_records": self.selected,
            "database_ids": self.databases,
            "selected_ids": [r["question_id"] for r in self.selected], "limits": b.LIMITS})
        b.save_new(self.bird_dir / "source_manifest.json", {"files": {"questions.jsonl": {
            "sha256": b.hash_file(self.bird_dir / "questions.jsonl")}}})
        self.paths = {}
        receipts = []
        for database in self.databases:
            relative = f"databases/{database}/{database}.sqlite"
            path = self.bird_dir / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b"SOFTWARE_FIXTURE_NOT_SQLITE_" + database.encode())
            self.paths[database] = path
            receipts.append({"db_id": database, "path": relative,
                             "bytes": path.stat().st_size, "sha256": b.hash_file(path)})
        b.save_new(self.bird_dir / "dataset_manifest.json", {
            "stage": "databases_complete", "selected_db_ids": self.databases,
            "source_manifest": {"sha256": b.hash_file(self.bird_dir / "source_manifest.json")},
            "selection_file": {"sha256": b.hash_file(self.bird_dir / "selected_db_ids.json")},
            "databases": receipts})
        revised = []
        for original in self.selected:
            revised.append({**original, "SQL": original["SQL"] + " AS revised",
                            **{"original_" + field: original[field] for field in a.FIELDS}})
        b.save_new(self.arcwise_dir / "arcwise_plat_full_with_diff.json", revised)
        (self.arcwise_dir / "README.md").write_text("SOFTWARE FIXTURE", encoding="utf-8")
        (self.arcwise_dir / "LICENSE").write_text("SOFTWARE FIXTURE", encoding="utf-8")
        self.refresh_manifest()

    def refresh_manifest(self):
        path = self.arcwise_dir / "source_manifest.json"
        value = {"stage": "text_complete", "revision": a.REVISION, "files": {
            filename: {"url": a.BASE + remote, "bytes": (self.arcwise_dir / filename).stat().st_size,
                       "sha256": b.hash_file(self.arcwise_dir / filename)}
            for filename, remote in a.FILES.items()}}
        path.write_bytes(b.encoded(value))

    def source_bytes(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for folder in (self.bird_dir, self.arcwise_dir) for p in folder.rglob("*") if p.is_file()}

    def run_mock(self, output, execute, effect=None):
        effect = effect if effect is not None else lambda *args: executed([(1,)])
        with mock.patch.object(b, "run_child", side_effect=effect) as child, contextlib.redirect_stdout(io.StringIO()):
            summary = a.run(self.arcwise_dir, self.bird_dir, output, execute=execute)
        return summary, child

    def test_full_pairing_makes_24_mock_calls_on_same_snapshot_without_approvals(self):
        before = self.source_bytes()
        output = self.root / "audit"
        summary, child = self.run_mock(output, True)
        self.assertEqual(child.call_count, 24)
        self.assertEqual(summary["query_calls"], 24)
        self.assertEqual(summary["selected_questions"], 12)
        self.assertEqual(summary["complete_pairs"], 12)
        self.assertEqual(summary["model_calls"], 0)
        self.assertEqual(summary["semantic_approvals"], 0)
        self.assertFalse(summary["official_arcwise_environment_verified"])
        self.assertEqual(summary["status"], "DEVELOPMENT_COMPATIBILITY_ONLY")
        for i, original in enumerate(self.selected):
            first, second = child.call_args_list[2 * i: 2 * i + 2]
            self.assertEqual(first.args, (self.paths[original["db_id"]], original["SQL"]))
            self.assertEqual(second.args, (self.paths[original["db_id"]], original["SQL"] + " AS revised"))
            metadata = a.read_json(output / f"q{original['question_id']}.metadata.json")
            self.assertFalse(metadata["semantic_approval"])
            self.assertEqual(metadata["exposure"], "development_exposed")
        self.assertEqual(before, self.source_bytes())
        self.assertTrue(summary["database_hashes_unchanged"])
        for name, expected in a.read_json(output / "artifact_hashes.json").items():
            self.assertEqual(expected, b.hash_file(output / name))
        with mock.patch.object(b, "run_child") as child, self.assertRaisesRegex(ValueError, "overwrite"):
            a.run(self.arcwise_dir, self.bird_dir, output, execute=True)
        child.assert_not_called()

    def test_dry_run_executes_nothing(self):
        summary, child = self.run_mock(self.root / "dry", False)
        child.assert_not_called()
        self.assertEqual(summary["query_calls"], 0)
        self.assertEqual(summary["complete_pairs"], 0)
        self.assertEqual(summary["semantic_approvals"], 0)

    def test_incomplete_mock_output_stays_in_fixed_denominator(self):
        count = 0
        def effect(*args):
            nonlocal count
            count += 1
            return ({"status": "SOFT_TIMEOUT", "complete": False}
                    if count == 2 else executed([(1,)]))
        output = self.root / "partial_result"
        summary, child = self.run_mock(output, True, effect)
        self.assertEqual(child.call_count, 24)
        self.assertEqual(summary["selected_questions"], 12)
        self.assertEqual(summary["complete_pairs"], 11)
        self.assertEqual(summary["query_status_counts"]["SOFT_TIMEOUT"], 1)
        comparison = a.read_json(output / f"q{self.selected[0]['question_id']}.comparison.json")
        self.assertEqual(comparison["comparison"]["status"], "INCOMPLETE_PAIR")

    def test_exception_retains_partial_records_and_no_success_summary(self):
        calls = [executed([(1,)]), RuntimeError("MOCK WORKER FAILURE")]
        output = self.root / "interrupted"
        with mock.patch.object(b, "run_child", side_effect=calls) as child, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "MOCK WORKER FAILURE"):
                a.run(self.arcwise_dir, self.bird_dir, output, execute=True)
        self.assertEqual(child.call_count, 2)
        self.assertTrue((output / f"q{self.selected[0]['question_id']}.original.json").is_file())
        self.assertTrue((output / "failure.json").is_file())
        self.assertTrue((output / "database_hashes_after.json").is_file())
        self.assertFalse((output / "summary.json").exists())

    def test_mutated_download_bytes_fail_before_output_or_execution(self):
        (self.arcwise_dir / "README.md").write_text("changed fixture", encoding="utf-8")
        output = self.root / "invalid"
        with mock.patch.object(b, "run_child") as child, self.assertRaises(ValueError):
            a.run(self.arcwise_dir, self.bird_dir, output, execute=True)
        self.assertFalse(output.exists())
        child.assert_not_called()


if __name__ == "__main__":
    unittest.main()
