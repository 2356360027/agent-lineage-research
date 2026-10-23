"""Offline allow-listed source snapshot. Not an executable/authorized batch.

No model weights, datasets, review forms, raw results, credentials or downloads.
Use only a new output directory; partial failures remain for inspection.
"""
import argparse
import json
from pathlib import Path

from runtime.bird_readonly_audit import encoded, hash_file
from scripts.fetch_bird_fixed import absent, no_link_ancestors, strict_json

LEGACY_FILES = (
    "runtime/__init__.py", "scripts/__init__.py",
    "runtime/cache_intent_staged_preview.py", "runtime/cache_intent_staged_loop.py",
    "runtime/cache_intent_staged_adapter.py", "runtime/cache_intent_journal.py",
    "runtime/cache_intent_context.py",
    "runtime/cache_intent_tools.py", "runtime/cache_intent_prompts.py",
    "runtime/cache_intent_prompts_v2.py", "runtime/cache_intent_contract_pilot.py",
    "runtime/bird_readonly_audit.py", "runtime/native.py", "runtime/e1.py",
    "scripts/fetch_bird_fixed.py", "scripts/prepare_staged_source_bundle.py",
)
RECOVERY_FILES = LEGACY_FILES + (
    "runtime/cache_intent_recovery_preview.py",
    "runtime/cache_intent_observation_bound.py",
    "runtime/cache_intent_capacity.py",
)
FILES = RECOVERY_FILES + ("runtime/cache_intent_batch.py",)


def build(root, output):
    root, output = Path(root).resolve(), Path(output).absolute()
    absent(output)
    sources = {}
    for name in FILES:
        path = root/name
        no_link_ancestors(path)
        if not path.is_file() or path.stat().st_size > 4*1024*1024:
            raise ValueError("Missing/oversized source: " + name)
        sources[name] = path.read_bytes()
    output.mkdir(parents=True, exist_ok=False)
    for name, data in sources.items():
        destination = output/"source"/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(data)
    manifest = {"purpose":"SOURCE_COMPONENT_SNAPSHOT_NOT_APPROVED_RUN",
                "inference_authorized":False,
                "files":{name:hash_file(output/"source"/name) for name in FILES}}
    with (output/"source_manifest.json").open("xb") as stream:
        stream.write(encoded(manifest))
    return verify(output, hash_file(output/"source_manifest.json"))


def verify(output, expected_manifest_sha256):
    output = Path(output)
    no_link_ancestors(output)
    manifest_path = output/"source_manifest.json"
    no_link_ancestors(manifest_path)
    if hash_file(manifest_path) != expected_manifest_sha256:
        raise ValueError("Source manifest hash mismatch")
    manifest = strict_json(manifest_path.read_text("utf-8"))
    if (set(manifest) != {"purpose","inference_authorized","files"}
            or manifest["purpose"] != "SOURCE_COMPONENT_SNAPSHOT_NOT_APPROVED_RUN"
            or manifest["inference_authorized"] is not False
            or type(manifest["files"]) is not dict or frozenset(manifest["files"]) not in {
                frozenset(FILES),
                frozenset(RECOVERY_FILES),
                frozenset(LEGACY_FILES),
                frozenset(name for name in LEGACY_FILES if name != "runtime/cache_intent_context.py")}):
        raise ValueError("Unexpected source manifest")
    source = output/"source"
    entries = list(source.rglob("*"))
    for path in entries:
        no_link_ancestors(path)
    if {p.relative_to(source).as_posix() for p in entries if p.is_file()} != set(manifest["files"]):
        raise ValueError("Unexpected source file set")
    for name, expected in manifest["files"].items():
        if hash_file(source/name) != expected:
            raise ValueError("Source content mismatch: " + name)
    return {"status":"SOURCE_HASHES_VERIFIED", "files":len(manifest["files"]),
            "source_manifest_sha256":expected_manifest_sha256,
            "model_calls":0, "inference_authorized":False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verify-sha256")
    args = parser.parse_args()
    result = (verify(args.output,args.verify_sha256) if args.verify_sha256 else
              build(Path(__file__).resolve().parents[1],args.output))
    print(json.dumps(result,indent=2))


if __name__ == "__main__": main()
