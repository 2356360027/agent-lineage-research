"""Build a private, allowlisted upload ZIP; no SSH, Git credentials, or downloads."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from runtime.cache_intent_contract_pilot import encoded, load_plan, read_small, sha
from scripts.fetch_bird_fixed import no_link_ancestors, strict_json

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "contract-pilot-74024cf-20261013"
FILES = (
    "runtime/__init__.py", "scripts/__init__.py", "runtime/cache_intent_contract_pilot.py",
    "runtime/cache_intent_prompts.py", "runtime/native.py", "runtime/e1.py",
    "scripts/fetch_bird_fixed.py", "scripts/run_contract_pilot_server.py",
    "scripts/contract_pilot_bootstrap.py",
    "configs/CACHE_INTENT_CONTRACT_PILOT_README.md",
    "configs/CONTRACT_PILOT_SERVER_HANDOFF.md",
    "runtime/runs/cache-intent-dev-package-20261012-001/PRIVATE_prompt_previews.json",
)
REFERENCE = ROOT / "runtime/runs/tool-input-audit-001/provenance/migration-20261002/native-new.json"


def build(output):
    if sha(encoded(load_plan())) != "73fc13d1256a76281e42c3f0aca15a92ff875322e59b1bcc9ff03348cdeea5e3":
        raise ValueError("Fixed plan differs")
    content = {name: read_small(ROOT / name) for name in FILES}
    if sha(content["runtime/cache_intent_contract_pilot.py"]) != "20c3f39fba6e5f866ef79c4befbbd55e6df70c36a65c714f2cbe899204383ba0":
        raise ValueError("Original scientific runner differs")
    old = strict_json(read_small(REFERENCE).decode("utf-8"))
    keys = ("deployment", "model", "model_path", "model_files", "model_content_sha256", "packages")
    content["EXPECTED_MODEL.json"] = encoded({key: old[key] for key in keys})
    manifest = {
        "scientific_source_commit": "74024cf28842902d0ccaf3d02e6c2d0b50de8459",
        "deployment_helper_identity": "files SHA-256, new helper not part of scientific-source commit",
        "private_upload_only": True, "participant_responses_included": False,
        "reference_model_note": "Oct 2 expected-content reference; not current verification or attestation",
        "files": {name: {"bytes": len(raw), "sha256": sha(raw)} for name, raw in content.items()},
    }
    content["DEPLOY_MANIFEST.json"] = encoded(manifest)
    output = Path(output).absolute()
    no_link_ancestors(output)
    receipt_path = output.with_suffix(".receipt.json")
    if output.exists() or receipt_path.exists():
        raise FileExistsError("Deployment artifact exists; do not overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("__main__.py", content["scripts/contract_pilot_bootstrap.py"])
        for name, raw in sorted(content.items()):
            info = zipfile.ZipInfo(PREFIX + "/" + name, (2026, 10, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, raw)
    with output.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    receipt = {"path": str(output), "sha256": digest, "bytes": output.stat().st_size,
               "members": len(content) + 1, "files": manifest["files"],
               "privacy": "No participant answers, credentials, full databases or model weights included"}
    with receipt_path.open("xb") as handle:
        handle.write(encoded(receipt))
    print(json.dumps({k: v for k, v in receipt.items() if k != "files"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "deployment" / (PREFIX + ".pyz"))
    args = parser.parse_args()
    import os
    os.chdir(ROOT)
    build(args.output)
