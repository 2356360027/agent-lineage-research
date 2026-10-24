"""Offline structural reconstruction, not semantic scoring, of the 12-call run.

Requires a separately SHA-verified download and the original private deployment
bundle. It does not run models, SQL, or frozen executable entry points.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys
import zipfile

from scripts.audit_contract_pilot_results import (
    block_effects, encoded, inventory, member, read_json, require, safe, sha, verify_hashes,
)
from scripts.inspect_contract_pilot_pairs import parse_received, qwen_prompt

BUNDLE_SHA = "1ce205c7a5213f424e5b97bfd5fa14b3e77f7a40645ce24d3291b9c0fe1ef370"
PLAN_SHA = "f10fed100719736eec657a42f212c207e7394c6014de652619db8560a06d2f94"


def audit(root, bundle):
    root, bundle = safe(root), safe(bundle)
    require(sha(bundle.read_bytes()) == BUNDLE_SHA, "Not the original deployment bundle")
    checked = verify_hashes(root, "SUPERVISOR_HASHES.json")
    finished = read_json(root / "finished.json")
    report = {"status": "VERIFIED_PRESERVED_FAILURE", "supervisor_files_checked": checked,
              "semantic_scoring_performed": False, "human_approvals": 0,
              "loaded_weight_attestation": False, "timing_independently_reconstructed": False,
              "zip_transfer_hash_checked_by_this_tool": False}
    with zipfile.ZipFile(bundle) as archive:
        prefix = "contract-interface-20261014-001/"
        expected = json.loads(archive.read(prefix + "SOURCE_MANIFEST.json"))
        require(read_json(root / "SOURCE_MANIFEST.json") == expected, "Source manifest differs from upload")
        require((root / "supervisor.py").read_bytes() == archive.read(
            prefix + "scripts/run_contract_interface_server.py"), "Supervisor differs from frozen upload")
        reference = json.loads(archive.read(prefix + "EXPECTED_MODEL.json"))
        require(read_json(root / "native-current.json") == reference, "Native reference differs")
    require(sha(encoded(read_json(root / "plan.json"))) == PLAN_SHA, "Plan identity mismatch")
    exp = root / "experiment"
    report["recorded_attempt_directories"] = len(list(exp.glob("call-*")))
    if finished.get("status") != "COMPLETE_UNREVIEWED":
        report["note"] = "Only archive integrity checked for failed run; do not retry uncertain calls."
        return report
    require(finished["runner_exit_code"] == 0 and not finished["cleanup_errors"], "Completion/cleanup mismatch")
    report["experiment_files_checked"] = verify_hashes(exp, "artifact_hashes.json")
    started, plan = read_json(exp / "started.json"), read_json(exp / "plan.json")
    require(sha(encoded(plan)) == PLAN_SHA == started["plan_sha256"], "Experiment plan mismatch")
    sources = inventory(exp / "source")
    require(sources == started["source_sha256"], "Source inventory mismatch")
    for name, digest in sources.items():
        require(expected["files"][name]["sha256"] == digest, "Source not from deployment: " + name)
    require(read_json(exp / "native_model.json") == reference and
            sha(encoded(reference)) == started["native_manifest_sha256"], "Native manifest mismatch")
    sys.path.insert(0, str(exp / "source"))
    frozen = importlib.import_module("runtime.contract_interface_diagnostic")
    require(Path(frozen.__file__).resolve() == member(exp / "source", "runtime/contract_interface_diagnostic.py").resolve(),
            "Wrong frozen client imported")
    frozen.validate(plan)

    def raw_response(folder):
        require(not (folder / "worker_error.json").exists() and not (folder / "client_stop.json").exists(),
                "Unexpected failed worker in complete run")
        receipt = read_json(folder / "transport.json")
        raw = (folder / "response.raw").read_bytes()
        require(receipt["status"] == "ok" and receipt["http_status"] == 200 and
                not receipt["response_truncated"] and receipt["response_bytes"] == len(raw) and
                receipt["response_sha256"] == sha(raw), "Transport integrity failure")
        return frozen.old.strict_json(raw.decode("utf-8"))

    models = raw_response(exp / "model-preflight")["data"]
    require(any(m.get("id") == reference["model"] and m.get("root") == reference["model_path"] for m in models),
            "Model preflight mismatch")
    require(report["recorded_attempt_directories"] == 12 and len(list(exp.glob("assessment-*.json"))) == 12,
            "Wrong complete-run denominator")
    records, response_ids = [], {}
    for call in plan["calls"]:
        index = call["index"]
        folder = exp / f"call-{index:03d}"
        require((folder / "request.json").read_bytes() == encoded(call["request"]), "Request differs from frozen plan")
        attempt = read_json(folder / "attempt.json")
        require(attempt["method"] == "POST" and attempt["state"] == "DISPATCH_PENDING", "Missing attempt receipt")
        raw = raw_response(folder)
        require(raw.get("model") == reference["model"], "Response model mismatch")
        require(raw["id"] not in response_ids, "Duplicate model response ID")
        response_ids[raw["id"]] = index
        record = {k: call[k] for k in ("index", "history_id", "interface_arm", "paired_seed", "request_sha256")}
        record.update(status="format_valid", semantic_status="UNREVIEWED", reported_usage=frozen.old.valid_usage(raw))
        try:
            record["contract"] = frozen.old.parse_contract(raw)
        except (ValueError, TypeError, KeyError):
            record["status"] = "format_invalid"
        require(record == read_json(exp / f"assessment-{index:03d}.json"), "Assessment reconstruction differs")
        records.append(record)
    usages = [r["reported_usage"] for r in records if r["reported_usage"] is not None]
    counts = {"status": "COMPLETE_UNREVIEWED", "planned_calls": 12, "attempted_calls": 12, "unattempted_calls": 0,
              "stop_reason": None, "human_approvals": 0, "semantic_scoring_performed": False,
              "calls_with_reported_usage": len(usages), "usage_complete_for_attempts": len(usages) == 12,
              "reported_prompt_tokens": sum(u["prompt_tokens"] for u in usages),
              "reported_completion_tokens": sum(u["completion_tokens"] for u in usages),
              "format_valid": sum(r["status"] == "format_valid" for r in records),
              "format_invalid": sum(r["status"] == "format_invalid" for r in records), "transport_failed": 0,
              "warnings": plan["warnings"]}
    summary = read_json(exp / "summary.json")
    require(all(summary[k] == v for k, v in counts.items()), "Summary reconstruction differs")
    seen, definition_counts = set(), {"legacy": 0, "visible_definitions": 0}
    for line in (root / "server-startup.log").read_text(encoding="utf-8").splitlines():
        parsed = parse_received(line)
        if parsed is None:
            continue
        response_id, prompt, schema = parsed
        require(response_id in response_ids and response_id not in seen, "Unknown/duplicate logged request")
        seen.add(response_id)
        call = plan["calls"][response_ids[response_id]]
        request = call["request"]
        require(prompt == qwen_prompt(request["messages"]) and schema == request["response_format"]["json_schema"]["schema"],
                "Served prompt/schema differs from request")
        visibility = [value["description"] in prompt for value in schema["properties"].values()]
        require(all(visibility) if call["interface_arm"] == "visible_definitions" else not any(visibility),
                "Actual logged field-description exposure differs from arm")
        definition_counts[call["interface_arm"]] += int(all(visibility))
    require(seen == set(response_ids), "Missing received-request log")
    pair_differences = []
    for i in range(0, 12, 2):
        require(records[i]["history_id"] == records[i+1]["history_id"], "Not paired by history")
        pair_differences.append({"history_id": records[i]["history_id"], "different_string_fields": [
            field for field in records[i].get("contract", {})
            if records[i]["contract"][field] != records[i+1].get("contract", {}).get(field)],
            "semantic_label": None})
    report.update(status="VERIFIED_COMPLETE_UNREVIEWED", structural_counts=counts,
                  independent_targets=2, constructed_histories=6, method_efficacy_claim=False,
                  logged_prompts_and_schemas_verified=len(seen),
                  calls_with_all_definitions_in_logged_text_by_arm=definition_counts,
                  post_outcome_string_comparison=pair_differences,
                  logged_client_seconds=summary["client_loop_seconds"],
                  logged_supervised_seconds=finished["supervised_seconds"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    block_effects()
    result = audit(args.run, args.bundle)
    output = safe(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(encoded(result))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
