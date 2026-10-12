"""Acquire four pinned Toxicology CSV descriptions and compare stored DDL metadata.

Read-only source audit. No SQLite opens, migrations, inference, CSV edits or
semantic approvals. The complete raw CSV bytes remain separate from diagnostics.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
from pathlib import Path
import re
import shutil
import sys

from scripts.fetch_arcwise_fixed import REVISION, SOURCE_BASE
from scripts.fetch_bird_fixed import (Budget, absent, fetch_small, make_opener,
    no_link_ancestors, read_json, require, write_json_exclusive)
from runtime.bird_readonly_audit import hash_file

FILES = {
    "atom.csv": (538, "a6fdc32f821f18382fc129f5bd3eb36779e63c23"),
    "bond.csv": (382, "c7c3538d4076d073ad4c9b76d3a106ebd81f69a1"),
    "connected.csv": (238, "b3bf12322ebc287c77148b1f48d38cda47bb0742"),
    "molecule.csv": (293, "d13e491a4421bf0a0a0c41ab283e199817f8dbf7"),
}
HEADERS = ["original_column_name", "column_name", "column_description", "data_format", "value_description"]
DEFAULT_PRIOR = Path("runtime/runs/cache-intent-dev-package-20261012-001")
MAX_FILE_BYTES, MAX_TOTAL_BYTES = 16 * 1024, 128 * 1024


def git_blob_sha(raw):
    return hashlib.sha1(f"blob {len(raw)}\0".encode("ascii") + raw).hexdigest()


def parse_description(raw):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    require(reader.fieldnames == HEADERS, "Unexpected schema description header")
    rows, seen = [], set()
    for row in reader:
        require(set(row) == set(HEADERS) and all(isinstance(v, str) for v in row.values()),
                "Ragged schema description row")
        name = row["original_column_name"]
        require(re.fullmatch(r"[a-z][a-z0-9_]*", name) is not None and name not in seen,
                "Missing, unsafe or duplicate original column name")
        seen.add(name)
        rows.append(row)
    require(bool(rows), "Empty schema description")
    return rows


def stored_columns(prior):
    """Restricted parser for the already captured four backtick-quoted DDLs.

    Does not execute DDL and is not a general SQLite schema parser.
    """
    prior = Path(prior)
    no_link_ancestors(prior)
    manifest = read_json(prior / "artifact_hashes.json")
    path = prior / "schema_result.json"
    no_link_ancestors(path)
    require(manifest.get(path.name) == hash_file(path), "Prior schema record not hash-bound")
    record = read_json(path)
    require(record.get("complete") is True, "Prior schema read incomplete")
    result = {}
    for row in record["rows"]:
        require(len(row) == 1 and row[0][0] == "text", "Unexpected captured schema cell")
        ddl = row[0][1]
        match = re.match(r"CREATE TABLE `([a-z0-9_]+)` \(", ddl)
        require(match is not None, "Unexpected captured DDL form")
        table = match[1]
        require(table not in result, "Duplicate captured table")
        columns = re.findall(r"^\s+`([a-z0-9_]+)` (TEXT|INTEGER|REAL|BLOB|NUMERIC)\b", ddl, re.M)
        require(bool(columns) and len(dict(columns)) == len(columns), "Missing or repeated captured columns")
        result[table] = dict(columns)
    require(set(result) == {Path(name).stem for name in FILES}, "Unexpected table set")
    return result


def inspect_descriptions(descriptions, columns):
    matches = []
    for table, rows in descriptions.items():
        declared = {row["original_column_name"]: row["data_format"].upper() for row in rows}
        matches.append({"table": table, "columns": len(rows),
                        "names_and_declared_types_match": declared == columns[table]})
    molecule = {r["original_column_name"]: r for r in descriptions["molecule"]}
    return {"table_checks": matches,
        "molecule_description_observation": {
            "value_description_on_molecule_id_mentions_both_signs": all(
                s in molecule["molecule_id"]["value_description"] for s in ("+", "-")),
            "value_description_on_label_is_blank": molecule["label"]["value_description"] == "",
            "interpretation": "Literal cell placement observation, not a corrected source or human adjudication."}}


def run(output, prior=DEFAULT_PRIOR, proxy=None):
    output, prior = Path(output), Path(prior)
    absent(output)
    columns = stored_columns(prior)
    output.mkdir(parents=True, exist_ok=False)
    budget, receipts, descriptions = Budget(MAX_TOTAL_BYTES), {}, {}
    write_json_exclusive(output / "started.json", {"revision": REVISION,
        "utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
        "files": FILES, "per_file_limit_bytes": MAX_FILE_BYTES, "total_limit_bytes": MAX_TOTAL_BYTES,
        "prior_schema_sha256": hash_file(prior / "schema_result.json"), "sql_calls": 0, "model_calls": 0})
    shutil.copyfile(__file__, output / "audit_source.py")
    try:
        opener = make_opener(proxy)
        for name, (expected_bytes, expected_git_blob) in FILES.items():
            url = f"{SOURCE_BASE}/data/schemas/toxicology/database_description/{name}"
            receipt = fetch_small(opener, url, output / name, budget, MAX_FILE_BYTES, 20)
            raw = (output / name).read_bytes()
            require(len(raw) == expected_bytes and git_blob_sha(raw) == expected_git_blob,
                    "CSV differs from pinned Git tree entry")
            receipt["git_blob_sha1"] = expected_git_blob
            receipts[name] = receipt
            descriptions[Path(name).stem] = parse_description(raw)
        diagnostics = inspect_descriptions(descriptions, columns)
        write_json_exclusive(output / "descriptions_parsed.json", descriptions)
        write_json_exclusive(output / "source_manifest.json", {"revision": REVISION,
            "files": receipts, "transfer": budget.receipt(), "tls_verification": True,
            "license": "CC-BY-SA-4.0, upstream repository", "raw_csv_modified": False})
        summary = {"status": "SCHEMA_DESCRIPTION_AUDIT_ONLY", "description_files": len(receipts),
            "description_columns": sum(len(r) for r in descriptions.values()),
            "body_bytes": budget.used, **diagnostics, "sql_calls": 0, "model_calls": 0,
            "human_approvals": 0, "binary_database_binding": "UNRESOLVED",
            "warnings": ["Matching names/types is not matching database bytes or contents.",
                         "Upstream value descriptions were not corrected or injected into prompts.",
                         "Only Toxicology was inspected; no whole-repository claim."]}
        write_json_exclusive(output / "summary.json", summary)
        write_json_exclusive(output / "artifact_hashes.json", {p.name: hash_file(p) for p in sorted(output.iterdir())})
        return summary
    except BaseException as exc:
        write_json_exclusive(output / "failure.json", {"error_type": type(exc).__name__,
            "completed_files": receipts, "transfer": budget.receipt(), "automatic_retry": False})
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--prior", type=Path, default=DEFAULT_PRIOR)
    p.add_argument("--proxy")
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()
    result = run(a.output, a.prior, a.proxy) if a.execute else {"status": "PLAN_NO_DOWNLOAD",
        "csv_files": 4, "expected_body_bytes": sum(s for s, _ in FILES.values()), "sql_calls": 0, "model_calls": 0}
    print(result)


if __name__ == "__main__":
    main()
