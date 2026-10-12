"""Pinned Arcwise text vs existing BIRD snapshot: development compatibility only.

No inference, semantic approvals, cache construction or official leaderboard score.
Keeps both versions and all failures; never mutates the old dataset/selection.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import sqlite3
import sys

from runtime import bird_readonly_audit as bird
from scripts.fetch_bird_fixed import read_json, no_link_ancestors, safe_identifier


REVISION = "fe766045c55b6875a43b30e9ac7683df5582f8cf"
BASE = f"https://raw.githubusercontent.com/uiuc-kang-lab/text_to_sql_benchmarks/{REVISION}/"
FILES = {"arcwise_plat_full_with_diff.json": "data/arcwise_plat_full_with_diff.json",
         "README.md": "README.md", "LICENSE": "LICENSE"}
VERSION = "arcwise-bird-compatibility-v1"
FIELDS = ("question", "evidence", "SQL")


def canonical_id(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]*", value):
        return int(value)
    raise ValueError("Invalid question ID; expected nonnegative int or canonical decimal string")


def index_records(records):
    if not isinstance(records, list) or not records:
        raise ValueError("Expected a nonempty JSON array")
    indexed = {}
    for source_row in records:
        if not isinstance(source_row, dict):
            raise ValueError("Each question must be an object")
        if not {"question_id", "db_id", *FIELDS} <= source_row.keys():
            raise ValueError("Missing question fields")
        qid = canonical_id(source_row["question_id"])
        if qid in indexed:
            raise ValueError("Duplicate normalized question ID")
        safe_identifier(source_row["db_id"])
        for field in ("question", "SQL", "original_question", "original_SQL"):
            if field in source_row and (not isinstance(source_row[field], str) or not source_row[field].strip()):
                raise ValueError(f"Invalid text field: {field}")
        for field in ("evidence", "original_evidence"):
            if field in source_row and source_row[field] is not None and not isinstance(source_row[field], str):
                raise ValueError(f"Invalid nullable text field: {field}")
        indexed[qid] = dict(source_row, question_id=qid)
    return indexed


def verify_arcwise(directory):
    directory = Path(directory)
    no_link_ancestors(directory)
    manifest = read_json(directory / "source_manifest.json")
    if manifest.get("stage") != "text_complete" or manifest.get("revision") != REVISION:
        raise ValueError("Incomplete or unexpected Arcwise revision")
    for filename, remote in FILES.items():
        path = directory / filename
        no_link_ancestors(path)
        receipt = manifest["files"][filename]
        if receipt["url"] != BASE + remote:
            raise ValueError("Unexpected source URL")
        if not path.is_file() or path.stat().st_size != receipt["bytes"] or path.stat().st_size > 2*1024*1024:
            raise ValueError("File size differs from receipt or limit")
        if bird.hash_file(path) != receipt["sha256"]:
            raise ValueError("File digest differs from receipt")
    return manifest, index_records(read_json(directory / "arcwise_plat_full_with_diff.json"))


def describe_pair(original, revised):
    """Byte/field comparison is not semantic grading or proof of common lineage."""
    base = {"question_id": original["question_id"], "db_id": original["db_id"],
            "exposure": "development_exposed", "semantic_approval": False,
            "original_record": original, "revised_record": revised}
    if revised is None:
        return base | {"match_status": "MISSING_REVISED_ID", "can_probe": False}
    if canonical_id(original["question_id"]) != canonical_id(revised["question_id"]):
        return base | {"match_status": "QUESTION_ID_MISMATCH", "can_probe": False}
    if original["db_id"] != revised["db_id"]:
        return base | {"match_status": "DATABASE_ID_MISMATCH", "can_probe": False}
    declared = {f: original[f] == revised["original_" + f]
                for f in FIELDS if "original_" + f in revised}
    status = ("BASELINE_SOURCE_MISMATCH" if any(v is False for v in declared.values())
              else "DECLARED_ORIGINAL_FIELDS_MATCH" if declared else "NO_DECLARED_ORIGINAL_FIELDS")
    return base | {"match_status": status, "can_probe": True,
                   "changed_fields": [f for f in FIELDS if original[f] != revised[f]],
                   "declared_original_field_matches": declared,
                   "same_id_is_not_same_task": True}


def compare_outputs(original, revised):
    if not original.get("complete") or not revised.get("complete"):
        return {"status": "INCOMPLETE_PAIR", "same_typed_bag": None, "same_typed_sequence": None,
                "same_column_names": None, "semantic_equivalence": "NOT_DETERMINED"}
    return {"status": "COMPLETE_DIAGNOSTIC_ONLY",
            "same_typed_bag": original["bag_sha256"] == revised["bag_sha256"],
            "same_typed_sequence": original["sequence_sha256"] == revised["sequence_sha256"],
            "same_column_names": original.get("columns") == revised.get("columns"),
            "semantic_equivalence": "NOT_DETERMINED"}


def run(arcwise_dir, bird_dir, output, execute=False):
    arcwise_dir, bird_dir, output = map(Path, (arcwise_dir, bird_dir, output))
    no_link_ancestors(output)
    if output.exists():
        raise ValueError("Refuse overwrite of existing audit directory")
    manifest, revised = verify_arcwise(arcwise_dir)
    selection = read_json(bird_dir / "selection.json")
    questions = bird.load_questions(bird_dir / "questions.jsonl")
    databases, selected = bird.select(questions)
    if (selection["questions_sha256"] != bird.hash_file(bird_dir / "questions.jsonl") or
            selection["code_sha256"] != bird.hash_file(bird.__file__) or
            selection["selected_records"] != selected or selection["database_ids"] != databases or
            selection["selected_ids"] != [r["question_id"] for r in selected] or selection["limits"] != bird.LIMITS):
        raise ValueError("Existing selection no longer matches its frozen source/runner")
    if len(selected) != 12:
        raise ValueError("This development audit is restricted to the old 12 selected questions")
    paths = {d: bird_dir / "databases" / d / f"{d}.sqlite" for d in databases}
    for path in paths.values():
        no_link_ancestors(path)
    before = {d: bird.hash_file(p) for d, p in paths.items()}
    acquisition = bird.verify_download(bird_dir, databases, before)
    pairs = [describe_pair(r, revised.get(r["question_id"])) for r in selected]
    all_old = index_records(questions)
    coverage = {"old_question_count": len(all_old), "revised_question_count": len(revised),
                "revised_database_count": len({r["db_id"] for r in revised.values()}),
                "shared_ids": len(all_old.keys() & revised.keys()),
                "missing_revised_ids": sorted(all_old.keys() - revised.keys()),
                "new_revised_ids": sorted(revised.keys() - all_old.keys())}
    output.mkdir(parents=True, exist_ok=False)
    bird.save_new(output / "design.json", {"version": VERSION, "execute": execute,
        "created_at": datetime.now(timezone.utc).isoformat(), "max_query_calls": 24 if execute else 0,
        "selected_ids": selection["selected_ids"], "limits": bird.LIMITS,
        "purpose": "Legacy-snapshot development diagnostic; not official Arcwise reproduction",
        "arcwise_revision": REVISION, "coverage": coverage,
        "input_hashes": {"arcwise_manifest": bird.hash_file(arcwise_dir / "source_manifest.json"),
                         "bird_selection": bird.hash_file(bird_dir / "selection.json"),
                         "bird_acquisition": bird.hash_file(bird_dir / "dataset_manifest.json")}})
    bird.save_new(output / "arcwise_source_manifest.json", manifest)
    bird.save_new(output / "bird_acquisition_manifest.json", acquisition)
    bird.save_new(output / "database_hashes_before.json", before)
    for source, name in ((Path(__file__), "adapter_source.py"), (Path(bird.__file__), "readonly_source.py")):
        shutil.copyfile(source, output / name)
    for pair in pairs:
        bird.save_new(output / f"q{pair['question_id']}.metadata.json", pair)
    results = []
    try:
        for pair in pairs:
            record = {"question_id": pair["question_id"], "db_id": pair["db_id"],
                      "match_status": pair["match_status"], "comparison": {"status": "NOT_EXECUTED"}}
            if execute and pair["can_probe"]:
                for version in ("original", "revised"):
                    sql = pair[version + "_record"]["SQL"]
                    result = bird.run_child(paths[pair["db_id"]], sql)
                    result["sql_sha256"] = bird.digest(sql)
                    bird.save_new(output / f"q{pair['question_id']}.{version}.json", result)
                    record[version] = {k: v for k, v in result.items() if k != "rows"}
                record["comparison"] = compare_outputs(record["original"], record["revised"])
            elif execute:
                record["comparison"] = {"status": "NOT_PROBED_ID_MISMATCH"}
            bird.save_new(output / f"q{pair['question_id']}.comparison.json", record)
            results.append(record)
            print(f"question={pair['question_id']} match={pair['match_status']} status={record['comparison']['status']}", flush=True)
    except BaseException as exc:
        bird.save_new(output / "failure.json", {"type": type(exc).__name__, "message": str(exc),
            "note": "Partial records retained; no automatic retry or success summary"})
        raise
    finally:
        after = {d: bird.hash_file(p) for d, p in paths.items()}
        bird.save_new(output / "database_hashes_after.json", after)
    completed = [r for r in results if r["comparison"]["status"] == "COMPLETE_DIAGNOSTIC_ONLY"]
    summary = {"status": "DEVELOPMENT_COMPATIBILITY_ONLY" if before == after else "DATABASE_CHANGED",
        "execute": execute, "coverage": coverage, "selected_questions": len(pairs),
        "changed_field_counts": dict(Counter(f for p in pairs for f in p.get("changed_fields", []))),
        "match_status_counts": dict(Counter(p["match_status"] for p in pairs)),
        "query_calls": sum(v in r for r in results for v in ("original", "revised")),
        "query_status_counts": dict(Counter(r[v]["status"] for r in results for v in ("original", "revised") if v in r)),
        "complete_pairs": len(completed),
        "different_typed_bag_ids": [r["question_id"] for r in completed if not r["comparison"]["same_typed_bag"]],
        "database_hashes_unchanged": before == after, "model_calls": 0, "semantic_approvals": 0,
        "official_arcwise_environment_verified": False,
        "python": sys.version, "sqlite": sqlite3.sqlite_version,
        "warnings": ["Same IDs or equal output do not prove equal requests or semantic correctness.",
                     "Existing Mini-Dev database bytes, not a verified official Arcwise environment.",
                     "Original and revised SQL may answer different questions; differences are not model gains.",
                     "No inference, cache-effect estimates, independence claims or timing benchmark."]}
    bird.save_new(output / "summary.json", summary)
    bird.save_new(output / "artifact_hashes.json", {p.name: bird.hash_file(p) for p in sorted(output.iterdir()) if p.is_file()})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arcwise-dir", type=Path, required=True)
    parser.add_argument("--bird-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Run at most 24 bounded read-only queries, no models")
    args = parser.parse_args()
    summary = run(args.arcwise_dir, args.bird_dir, args.output, args.execute)
    print(bird.encoded(summary).decode("utf-8"))
    if not summary["database_hashes_unchanged"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
