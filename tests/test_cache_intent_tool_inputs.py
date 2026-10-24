"""Synthetic local-only loader tests; no real dataset, SQLite or network access."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from runtime import cache_intent_tool_inputs as loader
from runtime import bird_readonly_audit as bird
from runtime.cache_intent_prompts import CONTRACT_FIELDS


def write_json(path, value):
    path.write_bytes(bird.encoded(value))


def digest_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cached(sql, rows=None):
    rows = [[['text', 'fixture']]] if rows is None else rows
    from collections import Counter
    return {"status": "EXECUTED", "complete": True, "denied": [],
            "columns": [f"c{i}" for i in range(len(rows[0]) if rows else 1)],
            "rows": rows, "sql_sha256": bird.digest(sql), **bird.fingerprints(rows),
            "retained_rows": len(rows), "result_bytes": sum(len(bird.encoded(r)) for r in rows),
            "value_types": dict(Counter(c[0] for r in rows for c in r))}


class Tests(unittest.TestCase):
    def setUp(self):
        for target in ("sqlite3.connect", "urllib.request.urlopen", "urllib.request.OpenerDirector.open",
                       "runtime.bird_readonly_audit.run_child", "runtime.bird_readonly_audit.execute_readonly"):
            guard = mock.patch(target, side_effect=AssertionError("No database execution or network in fixtures"))
            guard.start(); self.addCleanup(guard.stop)
        temp = tempfile.TemporaryDirectory(prefix="cache-tool-inputs-fixture-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.package, self.dataset = self.root / "package", self.root / "bird"
        self.package.mkdir(); self.dataset.mkdir()
        self.database_ids = ["toxicology", "debit_card_specializing", "codebase_community", "formula_1"]
        before, receipts = {}, []
        for name in self.database_ids:
            path = self.dataset / "databases" / name / f"{name}.sqlite"
            path.parent.mkdir(parents=True)
            path.write_bytes(("NOT A REAL SQLITE FILE: " + name).encode())
            before[name] = digest_file(path)
            receipts.append({"db_id": name, "path": path.relative_to(self.dataset).as_posix(),
                             "bytes": path.stat().st_size, "sha256": before[name]})
        self.acquisition = {"stage": "databases_complete", "selected_db_ids": self.database_ids,
                            "databases": receipts}
        write_json(self.dataset / "dataset_manifest.json", self.acquisition)
        for name in ("source_manifest.json", "questions.jsonl", "selected_db_ids.json"):
            write_json(self.dataset / name, {"fixture": "not research data"})
        self.plan = {"version": "cache-intent-pairs-dev-v1", "independent_target_count": 2,
                     "target_ids": [200, 234], "human_review_status": "pending", "targets": []}
        self.sqls = {}
        for qid in (200, 234):
            target = {"question_id": qid, "candidate_contract": {k: "PRIVATE_CANDIDATE_GOLD" for k in CONTRACT_FIELDS},
                      "histories": []}
            repair = "filter_positive_label_project_id" if qid == 200 else "count_distinct_nonnull_ids"
            for suffix, relation, transform in zip("abc", ("direct", "cache_only_transformable", "insufficient_without_base_reads"),
                                                   ("identity", repair, None)):
                ident = f"h{qid}{suffix}"; sql = f"SELECT '{ident} fixture'"; self.sqls[ident] = sql
                target["histories"].append({"id": ident, "candidate_relation": relation,
                    "historical_request": f"Fixture historical task {ident}", "sql": sql,
                    "transform": transform, "relation_basis": "PRIVATE_RELATION_LABEL"})
                write_json(self.package / f"{ident}.result.json", cached(sql))
            self.plan["targets"].append(target)
        write_json(self.package / "candidate_plan.json", self.plan)
        write_json(self.package / "summary.json", {"status": "CANDIDATES_PENDING_HUMAN_REVIEW",
            "inference_eligible": False, "database_hashes_unchanged": True, "human_approvals": 0, "model_calls": 0})
        write_json(self.package / "started.json", {"plan_sha256": digest_file(self.package / "candidate_plan.json"),
            "limits": bird.LIMITS, "database_hashes_before": before})
        write_json(self.package / "database_hashes_after.json", before)
        (self.package / "readonly_source.py").write_bytes(Path(bird.__file__).read_bytes())
        # Deliberately not JSON: the loader must hash, never parse, private gold artifacts.
        (self.package / "PRIVATE_cases.json").write_bytes(b"PRIVATE_CURRENT_GOLD_SQL_MUST_NOT_BE_PARSED")
        self.seal()
        verifier = mock.patch.object(bird, "verify_download", side_effect=self.verify_acquisition)
        self.verify = verifier.start()
        self.addCleanup(verifier.stop)

    def seal(self):
        hashes = {p.name: digest_file(p) for p in self.package.iterdir()
                  if p.is_file() and p.name != "artifact_hashes.json"}
        write_json(self.package / "artifact_hashes.json", hashes)
        self.manifest_sha = digest_file(self.package / "artifact_hashes.json")

    def verify_acquisition(self, directory, ids, actual):
        self.assertEqual(directory, self.dataset)
        self.assertEqual(ids, self.database_ids)
        self.assertEqual(actual, {r["db_id"]: r["sha256"] for r in self.acquisition["databases"]})
        return deepcopy(self.acquisition)

    def load(self, ident="h200a"):
        return loader.load_inputs(self.package, self.dataset, ident, self.manifest_sha)

    def mutate(self, filename, callback):
        path = self.package / filename
        value = json.loads(path.read_text("utf-8")); callback(value); write_json(path, value)
        self.seal()

    def test_load_exact_five_keys_with_provenance_without_gold(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.load("h234b")
        self.assertEqual(set(result), {"database_path", "database_sha256", "history_sql", "cached_result", "input_receipt"})
        self.assertIsInstance(result["database_path"], Path)
        self.assertEqual(result["database_path"], self.dataset / "databases/toxicology/toxicology.sqlite")
        self.assertEqual(result["database_sha256"], digest_file(result["database_path"]))
        self.assertEqual(result["history_sql"], self.sqls["h234b"])
        receipt = result["input_receipt"]
        self.assertEqual(receipt["package_manifest_sha256"], self.manifest_sha)
        self.assertEqual(receipt["history_result_sha256"], digest_file(self.package / "h234b.result.json"))
        self.assertEqual(receipt["readonly_source_sha256"], digest_file(Path(bird.__file__)))
        self.assertFalse(receipt["inference_eligible"]); self.assertEqual(receipt["human_approvals"], 0)
        public_dump = json.dumps(result, default=str)
        for private in ("PRIVATE_CURRENT_GOLD", "PRIVATE_CANDIDATE_GOLD", "PRIVATE_RELATION_LABEL", "target_id", "candidate_contract"):
            self.assertNotIn(private, public_dump)
        self.verify.assert_called_once()
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_returned_cache_is_independent_and_files_unchanged(self):
        first, second = self.load(), self.load()
        first["cached_result"]["rows"][0][0][1] = "mutated"
        first["input_receipt"]["history_id"] = "mutated"
        self.assertEqual(second["cached_result"]["rows"][0][0][1], "fixture")
        self.assertEqual(self.load(), second)

    def test_default_manifest_is_pinned_and_untrusted_override_format_rejected(self):
        self.assertEqual(loader.EXPECTED_MANIFEST_SHA256, "6ee741f4935bcfe2b30688401ecebf83b658a38b847b026b4a360746d7a048e7")
        with self.assertRaises(ValueError): loader.load_inputs(self.package, self.dataset, "h200a")
        for bad in (None, "A" * 64, "0" * 63):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                loader.load_inputs(self.package, self.dataset, "h200a", bad)
        self.verify.assert_not_called()

    def test_artifact_bytes_missing_coverage_and_extra_entries_rejected(self):
        path = self.package / "PRIVATE_cases.json"; original = path.read_bytes()
        path.write_bytes(original + b"tamper")
        with self.assertRaises(ValueError): self.load()
        path.write_bytes(original)
        extra = self.package / "unlisted.txt"; extra.write_bytes(b"extra")
        with self.assertRaises(ValueError): self.load()
        extra.unlink()
        path.unlink()
        with self.assertRaises(ValueError): self.load()
        path.write_bytes(original)
        (self.package / "nested").mkdir()
        with self.assertRaises(ValueError): self.load()
        self.verify.assert_not_called()

    def test_artifact_names_cannot_traverse_or_use_windows_special_paths(self):
        for name in ("../outside", "..\\outside", "/absolute", "C:stream", "CON", "artifact_hashes.json"):
            with self.subTest(name=name):
                self.seal()
                manifest = json.loads((self.package / "artifact_hashes.json").read_text("utf-8"))
                manifest[name] = "0" * 64
                write_json(self.package / "artifact_hashes.json", manifest)
                self.manifest_sha = digest_file(self.package / "artifact_hashes.json")
                with self.assertRaises(ValueError): self.load()

    def test_link_guard_applies_to_artifact_and_database_ancestors(self):
        original = loader.no_link_ancestors
        for forbidden in (self.package / "PRIVATE_cases.json", self.dataset / "databases/toxicology/toxicology.sqlite"):
            def guard(path, forbidden=forbidden):
                if Path(path) == forbidden: raise ValueError("Mocked link/reparse point")
                return original(path)
            with self.subTest(path=forbidden), mock.patch.object(loader, "no_link_ancestors", side_effect=guard), \
                 self.assertRaisesRegex(ValueError, "link/reparse"):
                self.load()

    def test_only_exact_six_history_ids_allowed(self):
        for ident in ("h200d", "H200a", "../h200a", "h200a.result.json", "h200a ", None, 200):
            with self.subTest(ident=ident), self.assertRaises(ValueError): self.load(ident)
        for ident in sorted(loader.HISTORY_IDS):
            self.assertEqual(self.load(ident)["history_sql"], self.sqls[ident])

    def test_pending_offline_summary_and_unchanged_database_are_mandatory(self):
        original = (self.package / "summary.json").read_bytes()
        for key, value in (("status", "APPROVED"), ("inference_eligible", True),
                           ("database_hashes_unchanged", False), ("human_approvals", 1),
                           ("model_calls", 1), ("inference_eligible", 0)):
            with self.subTest(key=key, value=value):
                (self.package / "summary.json").write_bytes(original)
                self.mutate("summary.json", lambda x: x.update({key: value}))
                with self.assertRaises(ValueError): self.load()

    def test_archived_plan_pin_validation_and_selected_history_uniqueness(self):
        self.mutate("candidate_plan.json", lambda x: x.update(human_review_status="approved"))
        with self.assertRaises(ValueError): self.load()
        self.mutate("started.json", lambda x: x.update(plan_sha256=digest_file(self.package / "candidate_plan.json")))
        with self.assertRaises(ValueError): self.load()
        write_json(self.package / "candidate_plan.json", self.plan)
        self.mutate("candidate_plan.json", lambda x: x["targets"][0]["histories"][1].update(id="h200a"))
        self.mutate("started.json", lambda x: x.update(plan_sha256=digest_file(self.package / "candidate_plan.json")))
        with self.assertRaises(ValueError): self.load()

    def test_cache_status_sql_rows_and_each_fingerprint_are_checked(self):
        name = "h200a.result.json"; original = (self.package / name).read_bytes()
        alterations = [lambda x: x.update(complete=False), lambda x: x.update(status="OUTPUT_LIMIT"),
                       lambda x: x.update(sql_sha256="0" * 64), lambda x: x.update(bag_sha256="0" * 64),
                       lambda x: x.update(sequence_sha256="0" * 64), lambda x: x.update(retained_rows=True),
                       lambda x: x.update(result_bytes=0), lambda x: x.update(denied=["denied"]),
                       lambda x: x["rows"][0][0].__setitem__(1, "changed"),
                       lambda x: x.update(columns=["a", "b"])]
        for index, alter in enumerate(alterations):
            with self.subTest(index=index):
                (self.package / name).write_bytes(original); self.mutate(name, alter)
                with self.assertRaises(ValueError): self.load()

    def test_supported_typed_cells_and_empty_complete_results(self):
        cells = [['null', None], ['integer', '-3'], ['real', '0x1.8000000000000p+0'],
                 ['text', 'Unicode \u6d4b\u8bd5'], ['blob', '00ab']]
        for rows in ([cells], []):
            write_json(self.package / "h200a.result.json", cached(self.sqls["h200a"], rows))
            self.seal()
            self.assertEqual(self.load()["cached_result"]["rows"], rows)

    def test_malformed_typed_cells_are_not_accepted_even_with_matching_hashes(self):
        for cell in (['integer', 3], ['integer', '03'], ['real', '1.5'], ['blob', 'AA'],
                     ['null', 'null'], ['unknown', 'x'], ['text'], ['text', 'x', 'extra']):
            with self.subTest(cell=cell):
                write_json(self.package / "h200a.result.json", cached(self.sqls['h200a'], [[cell]]))
                self.seal()
                with self.assertRaises(ValueError): self.load()

    def test_current_backend_must_match_archive(self):
        (self.package / "readonly_source.py").write_bytes(b"changed backend")
        self.seal()
        with self.assertRaisesRegex(ValueError, "backend"): self.load()
        self.verify.assert_not_called()

    def test_database_must_match_before_after_and_acquisition(self):
        self.mutate("database_hashes_after.json", lambda x: x.update(toxicology="0" * 64))
        with self.assertRaises(ValueError): self.load()
        started = json.loads((self.package / "started.json").read_text("utf-8"))
        write_json(self.package / "database_hashes_after.json", started['database_hashes_before']); self.seal()
        db = self.dataset / "databases/toxicology/toxicology.sqlite"; original = db.read_bytes()
        db.write_bytes(original + b"changed")
        with self.assertRaises(ValueError): self.load()
        db.write_bytes(original)
        with mock.patch.object(bird, "verify_download", side_effect=ValueError("Acquisition rejected")), \
             self.assertRaisesRegex(ValueError, "Acquisition rejected"):
            self.load()

    def test_database_sidecar_is_refused_without_querying(self):
        path = self.dataset / "databases/toxicology/toxicology.sqlite-wal"
        path.write_bytes(b"fixture journal")
        with self.assertRaisesRegex(ValueError, "sidecar"): self.load()
        self.verify.assert_not_called()

    def test_acquisition_database_set_and_unsafe_identifiers_rejected(self):
        for ids in (["toxicology"], self.database_ids + ["toxicology"], ["../escape"]):
            with self.subTest(ids=ids):
                modified = deepcopy(self.acquisition); modified['selected_db_ids'] = ids
                write_json(self.dataset / "dataset_manifest.json", modified)
                with self.assertRaises(ValueError): self.load()


if __name__ == "__main__":
    unittest.main()
