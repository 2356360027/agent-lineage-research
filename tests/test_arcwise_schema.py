"""Synthetic, offline CSV/schema fixtures; no research queries or human approvals."""
import ast
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import audit_arcwise_schema as audit
from scripts.fetch_bird_fixed import FetchError


COLUMNS = {
    "atom": {"atom_id": "TEXT", "molecule_id": "TEXT", "element": "TEXT"},
    "bond": {"bond_id": "TEXT", "molecule_id": "TEXT", "bond_type": "TEXT"},
    "connected": {"atom_id": "TEXT", "atom_id2": "TEXT", "bond_id": "TEXT"},
    "molecule": {"molecule_id": "TEXT", "label": "TEXT"},
}


def csv_bytes(rows, headers=None, bom=False):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(audit.HEADERS if headers is None else headers)
    writer.writerows(rows)
    return ("\ufeff" if bom else "").encode("utf-8") + stream.getvalue().encode("utf-8")


def fixture_bodies():
    bodies = {}
    for table, columns in COLUMNS.items():
        rows = []
        for name, kind in columns.items():
            description = 'Fixture description with "quotes", comma and\r\nsecond line.'
            values = "+: fixture positive\r\n-: fixture negative" if table == "molecule" and name == "molecule_id" else ""
            rows.append([name, "", description, kind.lower(), values])
        bodies[table + ".csv"] = csv_bytes(rows, bom=True)
    return bodies


def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def write_json(path, data):
    path.write_bytes(json.dumps(data, sort_keys=True).encode("utf-8"))


class Tests(unittest.TestCase):
    def setUp(self):
        for name in ("urllib.request.OpenerDirector.open", "urllib.request.urlopen", "sqlite3.connect"):
            guard = mock.patch(name, side_effect=AssertionError("No live network or SQLite in software fixtures"))
            guard.start()
            self.addCleanup(guard.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="arcwise-schema-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prior, self.output = self.root / "prior", self.root / "output"
        self.prior.mkdir()
        self.record = {"complete": True, "rows": [
            [["text", f"CREATE TABLE `{table}` (\n" + ",\n".join(
                f"  `{name}` {kind}" for name, kind in columns.items()) + "\n)"]]
            for table, columns in COLUMNS.items()]}
        self.bind_record(self.record)

    def bind_record(self, record):
        write_json(self.prior / "schema_result.json", record)
        write_json(self.prior / "artifact_hashes.json", {
            "schema_result.json": hashlib.sha256((self.prior / "schema_result.json").read_bytes()).hexdigest()})

    def run_fixture(self, bodies=None, delivered=None):
        bodies = fixture_bodies() if bodies is None else bodies
        delivered = bodies if delivered is None else delivered
        pinned = {name: (len(raw), blob(raw)) for name, raw in bodies.items()}
        self.calls = []

        def fetch(opener, url, destination, budget, limit, timeout):
            self.calls.append(destination.name)
            self.assertEqual(url, f"{audit.SOURCE_BASE}/data/schemas/toxicology/database_description/{destination.name}")
            self.assertIn("/" + audit.REVISION + "/", url)
            self.assertEqual((limit, timeout), (audit.MAX_FILE_BYTES, 20))
            raw = delivered[destination.name]
            budget.consume(len(raw))
            with destination.open("xb") as handle:
                handle.write(raw)
            return {"url": url, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

        with mock.patch.object(audit, "FILES", pinned), \
             mock.patch.object(audit, "make_opener", return_value=object()), \
             mock.patch.object(audit, "fetch_small", side_effect=fetch):
            return audit.run(self.output, self.prior)

    def test_real_pin_is_four_revision_bound_files_and_1451_bytes(self):
        self.assertEqual(set(audit.FILES), {"atom.csv", "bond.csv", "connected.csv", "molecule.csv"})
        self.assertEqual(sum(size for size, _ in audit.FILES.values()), 1451)
        self.assertRegex(audit.REVISION, r"^[0-9a-f]{40}$")
        self.assertIn("/" + audit.REVISION, audit.SOURCE_BASE)
        for size, digest in audit.FILES.values():
            self.assertGreater(size, 0)
            self.assertLess(size, audit.MAX_FILE_BYTES)
            self.assertRegex(digest, r"^[0-9a-f]{40}$")

    def test_git_blob_hash_includes_git_header(self):
        for raw in (b"", b"hello\n", b"\xef\xbb\xbfraw\r\n"):
            self.assertEqual(audit.git_blob_sha(raw), blob(raw))
            self.assertNotEqual(audit.git_blob_sha(raw), hashlib.sha1(raw).hexdigest())

    def test_cli_defaults_to_no_read_download_or_output(self):
        with mock.patch("sys.argv", ["audit", "--output", str(self.output)]), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout, \
             mock.patch.object(audit, "stored_columns") as stored, \
             mock.patch.object(audit, "fetch_small") as fetch, \
             mock.patch.object(audit, "make_opener") as opener:
            audit.main()
        result = ast.literal_eval(stdout.getvalue())
        self.assertEqual(result, {"status": "PLAN_NO_DOWNLOAD", "csv_files": 4,
                                 "expected_body_bytes": 1451, "sql_calls": 0, "model_calls": 0})
        self.assertFalse(self.output.exists())
        stored.assert_not_called(); fetch.assert_not_called(); opener.assert_not_called()

    def test_existing_directory_is_refused_before_source_reads_or_fetch(self):
        self.output.mkdir()
        sentinel = self.output / "unchanged.txt"; sentinel.write_bytes(b"preserve")
        with mock.patch.object(audit, "stored_columns") as stored, \
             mock.patch.object(audit, "make_opener") as opener, self.assertRaises(FetchError):
            audit.run(self.output, self.prior)
        stored.assert_not_called(); opener.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"preserve")
        self.assertEqual(list(self.output.iterdir()), [sentinel])

    def test_success_preserves_raw_csv_prior_and_source_bytes(self):
        bodies = fixture_bodies()
        prior_before = {p.name: p.read_bytes() for p in self.prior.iterdir()}
        script_before = Path(audit.__file__).read_bytes()
        summary = self.run_fixture(bodies)
        self.assertEqual(self.calls, list(bodies))
        self.assertEqual({p.name: p.read_bytes() for p in self.prior.iterdir()}, prior_before)
        self.assertEqual(Path(audit.__file__).read_bytes(), script_before)
        self.assertEqual((self.output / "audit_source.py").read_bytes(), script_before)
        receipt = json.loads((self.output / "source_manifest.json").read_text("utf-8"))
        for name, raw in bodies.items():
            self.assertEqual((self.output / name).read_bytes(), raw)
            self.assertEqual(receipt["files"][name]["bytes"], len(raw))
            self.assertEqual(receipt["files"][name]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(receipt["files"][name]["git_blob_sha1"], blob(raw))
        self.assertFalse(receipt["raw_csv_modified"])
        self.assertEqual(receipt["transfer"]["fetched_body_bytes"], sum(map(len, bodies.values())))
        self.assertEqual(summary["description_columns"], 11)
        self.assertEqual(summary["status"], "SCHEMA_DESCRIPTION_AUDIT_ONLY")
        self.assertEqual(summary["binary_database_binding"], "UNRESOLVED")
        self.assertEqual((summary["sql_calls"], summary["model_calls"], summary["human_approvals"]), (0, 0, 0))
        self.assertTrue(all(c["names_and_declared_types_match"] for c in summary["table_checks"]))
        hashes = json.loads((self.output / "artifact_hashes.json").read_text("utf-8"))
        self.assertEqual(set(hashes), {p.name for p in self.output.iterdir() if p.name != "artifact_hashes.json"})
        self.assertTrue(all(hashlib.sha256((self.output / name).read_bytes()).hexdigest() == value
                            for name, value in hashes.items()))

    def test_wrong_pinned_blob_or_size_is_retained_as_failure_not_complete(self):
        for kind in ("same_size", "changed_size"):
            with self.subTest(kind=kind):
                self.output = self.root / kind
                bodies = fixture_bodies(); delivered = dict(bodies)
                delivered["atom.csv"] = (b"X" + bodies["atom.csv"][1:]) if kind == "same_size" else bodies["atom.csv"] + b"\n"
                with self.assertRaisesRegex(FetchError, "pinned Git tree"):
                    self.run_fixture(bodies, delivered)
                self.assertEqual(self.calls, ["atom.csv"])
                self.assertEqual((self.output / "atom.csv").read_bytes(), delivered["atom.csv"])
                self.assertFalse((self.output / "source_manifest.json").exists())
                self.assertFalse((self.output / "summary.json").exists())
                failure = json.loads((self.output / "failure.json").read_text("utf-8"))
                self.assertFalse(failure["automatic_retry"])
                self.assertEqual(failure["completed_files"], {})
                with self.assertRaises(FetchError): self.run_fixture(bodies)

    def test_exact_header_order_required_even_duplicate_or_extra_headers(self):
        bad = [audit.HEADERS[::-1], audit.HEADERS[:-1], audit.HEADERS + ["extra"],
               [audit.HEADERS[0]] * len(audit.HEADERS),
               [" original_column_name", *audit.HEADERS[1:]]]
        for headers in bad:
            with self.subTest(headers=headers), self.assertRaisesRegex(FetchError, "header"):
                audit.parse_description(csv_bytes([["x", "", "", "text", ""]], headers))

    def test_multiline_quotes_unicode_bom_and_blank_cells_are_preserved(self):
        rows = [["molecule_id", "", 'A "quoted", multiline\r\n说明', "text", "+ first\n- second"],
                ["label", "", "", "text", ""]]
        raw = csv_bytes(rows, bom=True); before = raw
        parsed = audit.parse_description(raw)
        self.assertEqual(parsed, [dict(zip(audit.HEADERS, row)) for row in rows])
        self.assertEqual(raw, before)
        self.assertEqual(parsed[1]["value_description"], "")

    def test_blank_physical_lines_skipped_but_empty_named_rows_rejected(self):
        raw = csv_bytes([["x", "", "", "text", ""]]) + b"\r\n\r\n"
        self.assertEqual(len(audit.parse_description(raw)), 1)
        for raw in (csv_bytes([]), csv_bytes([["", "", "", "", ""]])):
            with self.assertRaises(FetchError): audit.parse_description(raw)

    def test_ragged_rows_and_duplicate_or_unsafe_column_names_rejected(self):
        for row in (["x", "", "", "text"], ["x", "", "", "text", "", "extra"]):
            with self.subTest(row=row), self.assertRaisesRegex(FetchError, "Ragged"):
                audit.parse_description(csv_bytes([row]))
        for name in ("", "X", "../id", "two words", "id\nnext"):
            with self.subTest(name=name), self.assertRaises(FetchError):
                audit.parse_description(csv_bytes([[name, "", "", "text", ""]]))
        with self.assertRaisesRegex(FetchError, "duplicate"):
            audit.parse_description(csv_bytes([["x", "", "", "text", ""]] * 2))

    def test_malformed_quote_and_non_utf8_are_not_silently_repaired(self):
        with self.assertRaises(csv.Error):
            audit.parse_description(csv_bytes([]) + b'x,,"unterminated,text,\r\n')
        with self.assertRaises(UnicodeDecodeError): audit.parse_description(b"\xff")

    def test_stored_schema_matches_fixture_without_opening_sqlite(self):
        before = {p.name: p.read_bytes() for p in self.prior.iterdir()}
        self.assertEqual(audit.stored_columns(self.prior), COLUMNS)
        self.assertEqual({p.name: p.read_bytes() for p in self.prior.iterdir()}, before)

    def test_stored_manifest_mismatch_prevents_output_and_fetch(self):
        for manifest in ({}, {"schema_result.json": "0" * 64}):
            with self.subTest(manifest=manifest):
                write_json(self.prior / "artifact_hashes.json", manifest)
                with mock.patch.object(audit, "make_opener") as opener, \
                     self.assertRaisesRegex(FetchError, "hash-bound"):
                    audit.run(self.output, self.prior)
                opener.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_stored_schema_rejects_incomplete_duplicate_and_wrong_structure(self):
        variants = []
        incomplete = deepcopy(self.record); incomplete["complete"] = False; variants.append(incomplete)
        duplicate = deepcopy(self.record); duplicate["rows"].append(duplicate["rows"][0]); variants.append(duplicate)
        missing = deepcopy(self.record); missing["rows"].pop(); variants.append(missing)
        wrongtype = deepcopy(self.record); wrongtype["rows"][0][0][0] = "blob"; variants.append(wrongtype)
        wrongform = deepcopy(self.record); wrongform["rows"][0][0][1] = "DROP TABLE atom"; variants.append(wrongform)
        duplicatecol = deepcopy(self.record)
        duplicatecol["rows"][0][0][1] = "CREATE TABLE `atom` (\n  `atom_id` TEXT,\n  `atom_id` TEXT\n)"
        variants.append(duplicatecol)
        for index, record in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(FetchError):
                self.bind_record(record)
                audit.stored_columns(self.prior)

    def test_diagnostic_reports_literal_placement_without_relocating_or_correcting(self):
        descriptions = {Path(name).stem: audit.parse_description(raw) for name, raw in fixture_bodies().items()}
        before = deepcopy(descriptions)
        result = audit.inspect_descriptions(descriptions, COLUMNS)
        observation = result["molecule_description_observation"]
        self.assertTrue(observation["value_description_on_molecule_id_mentions_both_signs"])
        self.assertTrue(observation["value_description_on_label_is_blank"])
        self.assertIn("Literal cell placement", observation["interpretation"])
        self.assertIn("not a corrected source or human adjudication", observation["interpretation"])
        self.assertEqual(descriptions, before)
        moved = deepcopy(descriptions)
        moved["molecule"][1]["value_description"] = moved["molecule"][0]["value_description"]
        moved["molecule"][0]["value_description"] = ""
        changed = audit.inspect_descriptions(moved, COLUMNS)["molecule_description_observation"]
        self.assertFalse(changed["value_description_on_molecule_id_mentions_both_signs"])
        self.assertFalse(changed["value_description_on_label_is_blank"])
        self.assertEqual(descriptions, before)

    def test_type_or_name_mismatch_is_diagnostic_not_auto_repair(self):
        descriptions = {Path(name).stem: audit.parse_description(raw) for name, raw in fixture_bodies().items()}
        descriptions["atom"][0]["data_format"] = "integer"
        descriptions["bond"][0]["original_column_name"] = "other_id"
        before = deepcopy(descriptions)
        checks = {r["table"]: r["names_and_declared_types_match"]
                  for r in audit.inspect_descriptions(descriptions, COLUMNS)["table_checks"]}
        self.assertEqual(checks, {"atom": False, "bond": False, "connected": True, "molecule": True})
        self.assertEqual(descriptions, before)


if __name__ == "__main__":
    unittest.main()
