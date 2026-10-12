"""Synthetic software fixtures only: no SQL, network, model or human review."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from runtime import cache_intent_dev_package as c


def typed_rows(values):
    return [[c.sql.typed(value) for value in row] for row in values]


def executed(values):
    rows = typed_rows(values)
    return {"status": "EXECUTED", "complete": True, "rows": rows,
            **c.sql.fingerprints(rows)}


def fixture_plan():
    plan = {"version": "cache-intent-pairs-dev-v1", "target_ids": [200, 234],
            "independent_target_count": 2, "human_review_status": "pending", "targets": []}
    for qid in plan["target_ids"]:
        repair = ("filter_positive_label_project_id" if qid == 200
                  else "count_distinct_nonnull_ids")
        histories = []
        relations = ["direct", "cache_only_transformable", "insufficient_without_base_reads"]
        for suffix, relation, transform in zip("abc", relations, ["identity", repair, None]):
            histories.append({"id": f"h{qid}{suffix}", "candidate_relation": relation,
                              "historical_request": f"HISTORY_SENTINEL_{qid}{suffix}",
                              "sql": f"MOCK_HISTORICAL_SQL_{qid}{suffix}",
                              "transform": transform, "relation_basis": "BASIS_SENTINEL"})
        plan["targets"].append({"question_id": qid, "histories": histories,
                               "candidate_contract": {field: f"CONTRACT_SENTINEL_{qid}_{field}"
                                                      for field in c.prompts.CONTRACT_FIELDS}})
    return plan


class TransformTests(unittest.TestCase):
    def test_identity_preserves_types_order_nulls_and_duplicate_rows(self):
        rows = typed_rows([(None,), ("1",), (1,), ("1",)])
        before = copy.deepcopy(rows)
        self.assertEqual(c.transform_rows(rows, "identity"), before)
        self.assertEqual(rows, before)

    def test_positive_filter_projects_deduplicates_and_excludes_null_label(self):
        rows = typed_rows([("b", "+"), ("a", "+"), ("a", "+"), ("minus", "-"),
                           ("unknown", None), ("spacing", "+ "), ("case", "PLUS")])
        before = copy.deepcopy(rows)
        actual = c.transform_rows(rows, "filter_positive_label_project_id")
        self.assertEqual(c.sql.fingerprints(actual)["bag_sha256"],
                         c.sql.fingerprints(typed_rows([("a",), ("b",)]))["bag_sha256"])
        self.assertEqual(rows, before)
        self.assertEqual(c.transform_rows([], "filter_positive_label_project_id"), [])

    def test_count_distinct_text_ids_excludes_null_not_empty_string(self):
        rows = typed_rows([("b1",), ("b1",), ("b2",), (None,), ("",), (None,)])
        before = copy.deepcopy(rows)
        self.assertEqual(c.transform_rows(rows, "count_distinct_nonnull_ids"), typed_rows([(3,)]))
        self.assertEqual(c.transform_rows([], "count_distinct_nonnull_ids"), typed_rows([(0,)]))
        self.assertEqual(c.transform_rows(typed_rows([(None,)]), "count_distinct_nonnull_ids"),
                         typed_rows([(0,)]))
        self.assertEqual(rows, before)

    def test_unknown_transform_and_wrong_column_shape_fail(self):
        for rows, kind in ((typed_rows([(1,)]), "filter_positive_label_project_id"),
                           (typed_rows([(1, 2)]), "count_distinct_nonnull_ids"),
                           ([], "invented_transform")):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                c.transform_rows(rows, kind)
        self.assertIsNone(c.transform_rows(typed_rows([(1,)]), None))


class PlanAndReviewTests(unittest.TestCase):
    def test_fixture_and_real_plan_validate_without_mutation_or_approval(self):
        plan = fixture_plan()
        before = copy.deepcopy(plan)
        self.assertIsNone(c.validate_plan(plan))
        self.assertEqual(plan, before)
        real_plan = c.read_json(Path(c.__file__).parents[1] / "configs/cache_intent_pairs_dev_v1.json")
        self.assertIsNone(c.validate_plan(real_plan))
        self.assertEqual(real_plan["human_review_status"], "pending")

    def test_wrong_version_target_count_ids_review_status_and_order_fail(self):
        for key, value in (("version", "other"), ("independent_target_count", 6),
                           ("target_ids", [200, 230]), ("human_review_status", "approved")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                plan = fixture_plan(); plan[key] = value; c.validate_plan(plan)
        plan = fixture_plan(); plan["targets"].reverse()
        with self.assertRaises(ValueError): c.validate_plan(plan)

    def test_contract_requires_all_eight_nonempty_text_values(self):
        for mode in ("missing", "extra", "blank", "null", "number"):
            plan = fixture_plan(); contract = plan["targets"][0]["candidate_contract"]
            if mode == "missing": contract.pop("entity")
            elif mode == "extra": contract["answer"] = "private"
            else: contract["entity"] = {"blank": " ", "null": None, "number": 1}[mode]
            with self.subTest(mode=mode), self.assertRaises(ValueError): c.validate_plan(plan)

    def test_relationships_transforms_and_unique_safe_ids_fail_closed(self):
        for mode in ("relation", "missing_history", "direct_null", "repair_identity", "unknown",
                     "insufficient_identity", "duplicate", "unsafe_id"):
            plan = fixture_plan(); histories = plan["targets"][0]["histories"]
            if mode == "relation": histories[1]["candidate_relation"] = "direct"
            elif mode == "missing_history": histories.pop()
            elif mode == "direct_null": histories[0]["transform"] = None
            elif mode == "repair_identity": histories[1]["transform"] = "identity"
            elif mode == "unknown": histories[1]["transform"] = "unknown"
            elif mode == "insufficient_identity": histories[2]["transform"] = "identity"
            elif mode == "duplicate": histories[2]["id"] = histories[0]["id"]
            else: histories[0]["id"] = "../outside"
            with self.subTest(mode=mode), self.assertRaises(ValueError): c.validate_plan(plan)

    def test_history_fields_must_be_nonempty_text(self):
        for field in ("historical_request", "sql", "relation_basis"):
            for value in (" ", None, 1):
                plan = fixture_plan(); plan["targets"][0]["histories"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    c.validate_plan(plan)

    def test_review_text_uses_only_current_request_evidence_schema_and_blank_fields(self):
        target = {"question_id": 200, "question": "PUBLIC_CURRENT_REQUEST", "evidence": "PUBLIC_EVIDENCE",
                  "SQL": "PRIVATE_REFERENCE_SQL", "candidate_contract": "PRIVATE_CONTRACT",
                  "history": "PRIVATE_HISTORY", "answer": "PRIVATE_RESULT"}
        text = c.review_text([target], ["CREATE TABLE fixture (x TEXT)"], "a" * 64)
        for public in ("PUBLIC_CURRENT_REQUEST", "PUBLIC_EVIDENCE", "CREATE TABLE fixture", "a" * 64):
            self.assertIn(public, text)
        for hidden in ("PRIVATE_REFERENCE_SQL", "PRIVATE_CONTRACT", "PRIVATE_HISTORY", "PRIVATE_RESULT"):
            self.assertNotIn(hidden, text)
        for field in c.prompts.CONTRACT_FIELDS:
            self.assertIn(f"- {field}", text)
        self.assertIn("____", text)
        self.assertIn("不算人工确认", text)

    def test_cli_without_execute_does_not_build_or_read_dataset(self):
        with mock.patch.object(c, "build") as build, mock.patch("sys.argv", ["prog", "--output", "unused"]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            c.main()
        build.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())["status"], "PLAN_NO_EXECUTION")


class MockPackageTests(unittest.TestCase):
    """Inert database bytes and mocked query boundary; not an execution benchmark."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bird, self.arcwise, self.prior = [self.root / name for name in ("bird", "arcwise", "prior")]
        for folder in (self.bird, self.arcwise, self.prior): folder.mkdir()
        self.db = self.bird / "databases/toxicology/toxicology.sqlite"
        self.db.parent.mkdir(parents=True); self.db.write_bytes(b"INERT_NOT_A_SQLITE_DATABASE")
        self.db_hashes = {"toxicology": c.sql.hash_file(self.db)}
        c.sql.save_new(self.bird / "dataset_manifest.json", {"selected_db_ids": ["toxicology"]})
        c.sql.save_new(self.arcwise / "source_manifest.json", {"software_fixture": True})
        self.plan_path = self.root / "plan.json"; c.sql.save_new(self.plan_path, fixture_plan())
        self.sources = {qid: {"question_id": qid, "db_id": "toxicology", "question": f"PUBLIC_REQUEST_{qid}",
                              "evidence": f"PUBLIC_EVIDENCE_{qid}", "SQL": f"REFERENCE_SQL_SENTINEL_{qid}"}
                        for qid in (200, 234)}
        self.references = {200: executed([("REFERENCE_ROW_A",), ("REFERENCE_ROW_B",)]), 234: executed([(2,)])}
        for qid in (200, 234):
            self.references[qid]["sql_sha256"] = c.sql.digest(self.sources[qid]["SQL"])
            c.sql.save_new(self.prior / f"q{qid}.metadata.json", {"revised_record": self.sources[qid]})
            c.sql.save_new(self.prior / f"q{qid}.revised.json", self.references[qid])
        c.sql.save_new(self.prior / "summary.json", {"status": "DEVELOPMENT_COMPATIBILITY_ONLY",
                                                   "database_hashes_unchanged": True})
        c.sql.save_new(self.prior / "database_hashes_after.json", self.db_hashes)
        self.refresh_prior_manifest()
        self.schema = "CREATE TABLE fixture (id TEXT, label TEXT)"
        self.responses = [executed([(self.schema,)]), copy.deepcopy(self.references[200]),
                          executed([("REFERENCE_ROW_A", "+"), ("REFERENCE_ROW_B", "+"), ("excluded", "-")]),
                          executed([("REFERENCE_ROW_A",), ("REFERENCE_ROW_B",), ("excluded",)]),
                          copy.deepcopy(self.references[234]), executed([("b1",), ("b2",)]), executed([(7,)])]

    def refresh_prior_manifest(self):
        hashes = {path.name: c.sql.hash_file(path) for path in self.prior.iterdir()
                  if path.name != "artifact_hashes.json"}
        (self.prior / "artifact_hashes.json").write_bytes(c.sql.encoded(hashes))

    def source_bytes(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for folder in (self.bird, self.arcwise, self.prior) for path in folder.rglob("*") if path.is_file()} | {
                    "plan.json": self.plan_path.read_bytes()}

    def build_mock(self, output, responses=None):
        with mock.patch.object(c.arc, "verify_arcwise", return_value=({}, self.sources)), \
                mock.patch.object(c.sql, "verify_download", return_value={}), \
                mock.patch.object(c.sql, "run_child", side_effect=copy.deepcopy(
                    self.responses if responses is None else responses)) as child, \
                contextlib.redirect_stdout(io.StringIO()):
            summary = c.build(self.plan_path, self.arcwise, self.bird, self.prior, output)
        return summary, child

    def test_seven_mock_queries_freeze_previews_with_no_semantic_approval(self):
        before = self.source_bytes(); output = self.root / "package"
        summary, child = self.build_mock(output)
        self.assertEqual(child.call_count, 7)
        self.assertEqual(child.call_args_list[0].args, (self.db, c.SCHEMA_QUERY))
        expected_sql = [h["sql"] for t in fixture_plan()["targets"] for h in t["histories"]]
        self.assertEqual([call.args for call in child.call_args_list[1:]], [(self.db, sql) for sql in expected_sql])
        self.assertEqual(summary["status"], "CANDIDATES_PENDING_HUMAN_REVIEW")
        self.assertEqual(summary["new_sql_calls"], 7)
        self.assertEqual(summary["prior_target_outputs_reused"], 2)
        self.assertEqual(summary["declared_transform_checks"], 4)
        self.assertEqual(summary["transform_checks_matching"], 4)
        self.assertEqual(summary["preview_pairs"], 12)
        self.assertEqual(summary["preview_envelopes"], 24)
        self.assertEqual(summary["human_approvals"], 0)
        self.assertEqual(summary["model_calls"], 0)
        self.assertFalse(summary["inference_eligible"])
        self.assertFalse(summary["official_arcwise_environment_verified"])
        self.assertEqual(before, self.source_bytes())
        cases = c.read_json(output / "PRIVATE_cases.json")
        self.assertEqual(len(cases), 6)
        for case in cases:
            self.assertFalse(case["diagnostic"]["semantic_approval"])
            self.assertEqual(case["human_reviews"], [])
            self.assertEqual(case["split"], "development_exposed")
        for preview in c.read_json(output / "PRIVATE_prompt_previews.json"):
            c.prompts.assert_pair(*preview["envelopes"], factor=preview["factor"])
            for envelope, expected_hash in zip(preview["envelopes"], preview["envelope_hashes"]):
                self.assertEqual(c.sql.digest(envelope), expected_hash)
                text = c.sql.encoded(envelope).decode("utf-8")
                for private in ("CONTRACT_SENTINEL", "REFERENCE_SQL_SENTINEL", "REFERENCE_ROW", "BASIS_SENTINEL",
                                preview["private_history_id"], "candidate_relation", "target_id"):
                    self.assertNotIn(private, text)
        for name, expected in c.read_json(output / "artifact_hashes.json").items():
            self.assertEqual(c.sql.hash_file(output / name), expected)

    def test_stage_one_review_files_and_packet_never_contain_private_answers(self):
        output = self.root / "reviews"; self.build_mock(output)
        packet = c.read_json(output / "task_review_packet.json")
        self.assertEqual(set(packet), {"targets", "schema"})
        for target in packet["targets"]:
            self.assertEqual(set(target), {"question_id", "question", "evidence"})
        for name in ("REVIEWER_1_TASKS.md", "REVIEWER_2_TASKS.md", "task_review_packet.json"):
            text = (output / name).read_text(encoding="utf-8")
            for marker in ("CONTRACT_SENTINEL", "HISTORY_SENTINEL", "MOCK_HISTORICAL_SQL", "REFERENCE_SQL_SENTINEL",
                           "REFERENCE_ROW", "BASIS_SENTINEL", "cache_only_transformable"):
                self.assertNotIn(marker, text)
        self.assertEqual((output / "REVIEWER_1_TASKS.md").read_bytes(), (output / "REVIEWER_2_TASKS.md").read_bytes())

    def test_existing_output_refused_before_any_queries(self):
        output = self.root / "existing"; output.mkdir(); (output / "keep").write_bytes(b"keep")
        with mock.patch.object(c.sql, "run_child") as child, self.assertRaisesRegex(ValueError, "overwrite"):
            c.build(self.plan_path, self.arcwise, self.bird, self.prior, output)
        child.assert_not_called(); self.assertEqual((output / "keep").read_bytes(), b"keep")

    def test_prior_artifact_byte_tampering_and_manifest_omission_fail(self):
        path = self.prior / "q200.revised.json"; original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "changed"): c.verify_prior(self.prior)
        path.write_bytes(original)
        hashes = c.read_json(self.prior / "artifact_hashes.json"); hashes.pop("q200.revised.json")
        (self.prior / "artifact_hashes.json").write_bytes(c.sql.encoded(hashes))
        with self.assertRaisesRegex(ValueError, "omits"): c.verify_prior(self.prior)

    def test_prior_manifest_rejects_path_traversal_and_unsuccessful_summary(self):
        hashes = c.read_json(self.prior / "artifact_hashes.json"); hashes["../outside"] = "a" * 64
        (self.prior / "artifact_hashes.json").write_bytes(c.sql.encoded(hashes))
        with self.assertRaisesRegex(ValueError, "artifact name"): c.verify_prior(self.prior)
        for summary in ({"status": "INCOMPLETE", "database_hashes_unchanged": True},
                        {"status": "DEVELOPMENT_COMPATIBILITY_ONLY", "database_hashes_unchanged": False}):
            (self.prior / "summary.json").write_bytes(c.sql.encoded(summary)); self.refresh_prior_manifest()
            with self.assertRaises(ValueError): c.verify_prior(self.prior)

    def test_forged_row_hash_detected_even_with_refreshed_artifact_manifest(self):
        result = c.read_json(self.prior / "q200.revised.json"); result["rows"] = typed_rows([("changed",)])
        (self.prior / "q200.revised.json").write_bytes(c.sql.encoded(result)); self.refresh_prior_manifest()
        output = self.root / "bad_rows"
        with self.assertRaisesRegex(ValueError, "rows differ"): self.build_mock(output)
        self.assertFalse(output.exists())

    def test_changed_database_snapshot_rejected_before_output(self):
        self.db.write_bytes(b"CHANGED_INERT_FIXTURE")
        output = self.root / "bad_db"
        with self.assertRaisesRegex(ValueError, "snapshot differs"): self.build_mock(output)
        self.assertFalse(output.exists())

    def test_incomplete_history_keeps_record_but_no_ready_preview(self):
        responses = copy.deepcopy(self.responses); responses[2] = {"status": "SOFT_TIMEOUT", "complete": False}
        output = self.root / "partial"; summary, child = self.build_mock(output, responses)
        self.assertEqual(child.call_count, 7)
        self.assertEqual(summary["status"], "INCOMPLETE_HISTORY_PACKAGE")
        self.assertEqual(summary["history_count"], 6)
        self.assertEqual(summary["preview_pairs"], 10)
        self.assertEqual(summary["query_status_counts"]["SOFT_TIMEOUT"], 1)
        previews = c.read_json(output / "PRIVATE_prompt_previews.json")
        self.assertNotIn("h200b", [p["private_history_id"] for p in previews])
        record = c.read_json(output / "PRIVATE_cases.json")[1]
        self.assertIsNone(record["diagnostic"]["transform_bag_matches_target"])
        self.assertFalse(record["diagnostic"]["semantic_approval"])
        self.assertFalse(summary["inference_eligible"])

    def test_schema_failure_preserves_failure_and_after_hash_without_summary(self):
        output = self.root / "schema_failure"
        with self.assertRaisesRegex(ValueError, "Schema read incomplete"):
            self.build_mock(output, [{"status": "SOFT_TIMEOUT", "complete": False}])
        self.assertTrue((output / "schema_result.json").exists())
        self.assertTrue((output / "failure.json").exists())
        self.assertFalse(c.read_json(output / "failure.json")["automatic_retry"])
        self.assertEqual(c.read_json(output / "database_hashes_after.json"), self.db_hashes)
        self.assertFalse((output / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
