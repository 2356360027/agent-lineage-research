"""Offline audit of the frozen six-history development package; no inference.

Reads archived results, never opens SQLite or connects to a model. Byte sizes
are NOT token estimates. Output is private and must use a new directory.
Does not import candidate contracts, reference SQL or review labels into prompts.
"""
import argparse
from pathlib import Path
import re

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_prompts as legacy
from runtime import cache_intent_staged_preview as staged
from runtime.cache_intent_tools import valid_result
from scripts.fetch_bird_fixed import absent, no_link_ancestors, read_json


def inspect(package, expected_manifest_sha256):
    package = Path(package)
    no_link_ancestors(package)
    manifest = package / "artifact_hashes.json"
    if audit.hash_file(manifest) != expected_manifest_sha256:
        raise ValueError("Input manifest hash mismatch")
    files = read_json(manifest)
    for name, fingerprint in files.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in (".", ".."):
            raise ValueError("Unsafe manifest name")
        path = package / name
        no_link_ancestors(path)
        if audit.hash_file(path) != fingerprint:
            raise ValueError("Input changed: " + name)
    required = {"PRIVATE_cases.json", "task_review_packet.json", "database_hashes_after.json"}
    if not required <= files.keys():
        raise ValueError("Required inputs absent from verified manifest")
    cases = read_json(package / "PRIVATE_cases.json")
    if (len(cases) != 6 or {c["history"]["id"] for c in cases} !=
            {"h200a", "h200b", "h200c", "h234a", "h234b", "h234c"}):
        raise ValueError("Not the six-history development package")
    schema = read_json(package / "task_review_packet.json")["schema"]
    database_sha = read_json(package / "database_hashes_after.json")["toxicology"]
    rows, previews = [], []
    for case in cases:
        target, history = case["target_record"], case["history"]
        name = history["id"] + ".result.json"
        if name not in files:
            raise ValueError("Cache missing from verified manifest")
        result = read_json(package / name)
        valid_result(result)
        if result["sql_sha256"] != audit.digest(history["sql"]):
            raise ValueError("Cache SQL binding mismatch")
        pair = staged.make_pair(target["question"], target.get("evidence"), schema,
            {k: history[k] for k in ("historical_request", "sql")}, database_sha)
        for envelope in pair:
            staged.validate_preview(envelope)
        differences = legacy._diff(*(legacy._expanded(e) for e in pair))
        if differences != [("messages", 1, "content", "context", "cache", "read_cost")]:
            raise ValueError("Unexpected paired intervention")
        # Exactly the exposed content fields, not private backend timing/labels.
        visible = {"columns": result["columns"], "rows": result["rows"]}
        rows.append({"history_id": history["id"], "target_id": case["target_id"],
            "complete_cache": True, "cache_rows": len(result["rows"]),
            "cache_content_utf8_bytes": len(audit.encoded(visible)),
            "initial_messages_utf8_bytes": [len(audit.encoded(e["messages"])) for e in pair],
            "prompt_sha256": [audit.digest(e) for e in pair],
            "pair_diff": [list(p) for p in differences]})
        previews.append({"history_id": history["id"], "arms": pair})
    # Detect a concurrent edit during this read-only audit as well.
    if audit.hash_file(manifest) != expected_manifest_sha256 or any(
            audit.hash_file(package / n) != h for n, h in files.items()):
        raise ValueError("Package changed during audit")
    report = {"status": "OFFLINE_INPUT_EXPOSURE_AUDIT_NOT_RUN_ADMISSION",
        "input_manifest_sha256": expected_manifest_sha256, "verified_files": len(files),
        "database_sha256_from_receipt": database_sha, "database_rehashed_this_audit": False,
        "source_sha256": audit.hash_file(__file__),
        "staged_preview_source_sha256": audit.hash_file(staged.__file__),
        "prompt_source_sha256": audit.hash_file(legacy.__file__),
        "cases": rows, "model_calls": 0, "sql_queries": 0,
        "token_counts": None, "context_feasibility": "UNVERIFIED",
        "human_approval_created": False, "inference_authorized": False,
        "warnings": ["Byte sizes are not tokenizer counts or full rollout bounds.",
            "Small archived caches do not bound arbitrary run_sql outputs.",
            "Snapshot scope agreement does not establish generic DDL equivalence.",
            "Original unresolved fields in the old package are not rewritten."]}
    return report, previews


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    absent(output)
    report, previews = inspect(args.package, args.manifest_sha256)
    output.mkdir(parents=True, exist_ok=False)
    audit.save_new(output / "report.json", report)
    audit.save_new(output / "PRIVATE_staged_previews.json", previews)
    with (output / "audit_source.py").open("xb") as stream:
        stream.write(Path(__file__).read_bytes())
    audit.save_new(output / "artifact_hashes.json", {
        p.name: audit.hash_file(p) for p in sorted(output.iterdir()) if p.is_file()})
    print(audit.encoded(report).decode("utf-8"))


if __name__ == "__main__":
    main()
