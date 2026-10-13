"""Prepare an allowlisted PRIVATE upload archive; no transfer or execution."""
from __future__ import annotations

import argparse
from pathlib import Path
import zipfile
import json

from runtime import contract_interface_diagnostic as diagnostic
from runtime.cache_intent_contract_pilot import encoded, read_small, save_new, sha, strict_json
from scripts.audit_contract_pilot_results import safe, require

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "contract-interface-20261014-001"
FILES = (
    "runtime/__init__.py", "runtime/contract_interface_diagnostic.py", "runtime/cache_intent_contract_pilot.py",
    "runtime/cache_intent_prompts.py", "runtime/cache_intent_prompts_v2.py", "runtime/native.py", "runtime/e1.py",
    "scripts/__init__.py", "scripts/audit_contract_pilot_results.py", "scripts/fetch_bird_fixed.py",
    "scripts/prepare_contract_interface_diagnostic.py", "scripts/run_contract_interface_server.py",
    "scripts/run_contract_pilot_server.py", diagnostic.DEFAULT_PLAN.as_posix(),
)
REFERENCE = ROOT / "runtime/runs/tool-input-audit-001/provenance/migration-20261002/native-new.json"


def build(output):
    plan = diagnostic.load_plan(ROOT / diagnostic.DEFAULT_PLAN)
    content = {name: read_small(ROOT / name) for name in FILES}
    reference = strict_json(read_small(REFERENCE).decode("utf-8"))
    native = {key: reference[key] for key in
              ("deployment", "model", "model_path", "model_files", "model_content_sha256", "packages")}
    require(native["model"] == diagnostic.MODEL and native["packages"] == diagnostic.PACKAGES and
            native["model_content_sha256"] == diagnostic.MODEL_SHA == sha(encoded(native["model_files"])),
            "Historical reference differs from fixed model")
    content["EXPECTED_MODEL.json"] = encoded(native)
    manifest = {"plan_sha256": diagnostic.PLAN_SHA, "private_upload_only": True,
                "participant_answers_included": False, "model_weights_included": False,
                "reference_is_not_current_verification": True,
                "files": {name: {"sha256": sha(raw), "bytes": len(raw)} for name, raw in content.items()}}
    content["SOURCE_MANIFEST.json"] = encoded(manifest)
    output = safe(output)
    receipt = output.with_suffix(".receipt.json")
    require(not output.exists() and not receipt.exists(), "Preserve existing bundle/receipt")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(content.items()):
            info = zipfile.ZipInfo(PREFIX + "/" + name, (2026, 10, 14, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, raw)
    result = {"status": "BUILT_NOT_UPLOADED_NOT_EXECUTED", "sha256": sha(output.read_bytes()),
              "bytes": output.stat().st_size, "members": len(content), "plan_sha256": diagnostic.PLAN_SHA,
              "planned_calls": len(plan["calls"]), "executed_calls": 0,
              "files": manifest["files"], "reference_model_note": "Historical content reference; reverify on host"}
    save_new(receipt, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.output)
    print(json.dumps({k: v for k, v in result.items() if k != "files"}, indent=2))


if __name__ == "__main__":
    main()
