"""Read-only recovery batch admission entry. No network/model execution option.

Verifies the exact private input bytes, imported source tree and frozen database.
Live execution requires a separately authorized deployment integration; neither
this receipt nor the prepared plan is a permission token.
"""
import argparse
import hashlib
from pathlib import Path
from runtime import bird_readonly_audit as audit
from runtime.cache_intent_batch import validate_plan
from runtime.cache_intent_recovery_preview import validate_preview
from runtime.cache_intent_journal import Journal
from scripts.fetch_bird_fixed import no_link_ancestors,strict_json
from scripts import prepare_staged_source_bundle as bundle

INPUT_FILES={"PRIVATE_plan.json","PRIVATE_caches.json","receipt.json",
             "cache_intent_batch.py","prepare_recovery_batch.py"}
FIXED_PLAN_SHA="3666fbbcbe8e16c22bc76eca6b827fd2e111905ebd35c21ca41de0999dfccdab"
FIXED_INPUT_SHA="4374a5e72939aafec5e4a02efacd5d290a4d6da7a7fbd7b4ee1633cec07c2b21"


def pinned_bytes(path,expected):
    path=Path(path); no_link_ancestors(path)
    if not path.is_file() or path.stat().st_size>8*1024*1024:
        raise ValueError("Missing or oversized admission artifact")
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected: raise ValueError("Artifact hash mismatch")
    return raw


def load_private_inputs(directory,manifest_sha256,plan_sha256):
    root=Path(directory); no_link_ancestors(root)
    manifest=strict_json(pinned_bytes(root/"artifact_hashes.json",manifest_sha256).decode("utf-8"))
    if type(manifest) is not dict or set(manifest)!=INPUT_FILES:
        raise ValueError("Unexpected private input file set")
    if {p.name for p in root.iterdir()}!=INPUT_FILES|{"artifact_hashes.json"}:
        raise ValueError("Unmanifested input content")
    raw={name:pinned_bytes(root/name,sha) for name,sha in manifest.items()}
    plan=strict_json(raw["PRIVATE_plan.json"].decode("utf-8"))
    caches=strict_json(raw["PRIVATE_caches.json"].decode("utf-8"))
    receipt=strict_json(raw["receipt.json"].decode("utf-8"))
    validate_plan(plan,caches)
    if (audit.digest(plan)!=plan_sha256 or receipt.get("plan_sha256")!=plan_sha256
            or receipt.get("status")!="LOCAL_PLAN_ONLY" or receipt.get("inference_authorized") is not False):
        raise ValueError("Plan/receipt identity mismatch")
    return plan,caches,receipt


def check_running_source(source_bundle,manifest_sha256,running_root=None):
    """Check disk source identity; not a defense against hostile Python hooks."""
    bundle.verify(source_bundle,manifest_sha256)
    manifest=strict_json(pinned_bytes(Path(source_bundle)/"source_manifest.json",
                                      manifest_sha256).decode("utf-8"))
    if set(manifest["files"])!=set(bundle.FILES):
        raise ValueError("Old component snapshot is not the current entry deployment")
    root=Path(__file__).resolve().parents[1] if running_root is None else Path(running_root)
    for name,sha in manifest["files"].items(): pinned_bytes(root/name,sha)
    # Imported dependencies must come from that same tree, not PYTHONPATH shadows.
    import sys
    for name,module in tuple(sys.modules.items()):
        if name=="runtime" or name=="scripts" or name.startswith(("runtime.","scripts.")):
            location=getattr(module,"__file__",None)
            if location and not Path(location).resolve().is_relative_to(root.resolve()):
                raise ValueError("Imported project module is outside the frozen source tree")
            if location:
                relative=Path(location).resolve().relative_to(root.resolve()).as_posix()
                if relative not in manifest["files"]:
                    raise ValueError("Imported project module is absent from the source manifest")
    return {"source_manifest_sha256":manifest_sha256,"source_files":len(manifest["files"]),
            "running_source_matches":True,"runtime_attestation":False}


def inspect_inputs(*,inputs,input_sha,plan_sha,source_bundle,source_sha,database):
    source_receipt=check_running_source(source_bundle,source_sha)
    plan,caches,receipt=load_private_inputs(inputs,input_sha,plan_sha)
    identities={validate_preview(j["envelope"])["context"]["database_sha256"] for j in plan["jobs"]}
    database=Path(database); no_link_ancestors(database)
    if (len(identities)!=1 or any(Path(str(database)+s).exists() for s in ("-wal","-shm","-journal"))
            or not database.is_file() or audit.hash_file(database) not in identities):
        raise ValueError("Frozen database identity or sidecar mismatch")
    # Only hash and count private material in the public-facing receipt.
    return dict(status="LOCAL_INPUT_AND_SOURCE_CHECK_PASSED",**source_receipt,
        input_manifest_sha256=input_sha,plan_sha256=plan_sha,database_sha256=next(iter(identities)),
        trajectories=12,independent_targets=2,max_generation_attempts=276,
        cached_results=len(caches),model_calls=0,sql_queries=0,network_requests=0,
        inference_authorized=False,live_model_and_tokenizer_verified=False,
        semantic_labels_generated=False,provider_equivalence_verified=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs",required=True)
    parser.add_argument("--source-bundle",required=True)
    parser.add_argument("--source-sha256",required=True)
    parser.add_argument("--database",required=True)
    parser.add_argument("--output",required=True)
    args=parser.parse_args(argv)
    result=inspect_inputs(inputs=args.inputs,input_sha=FIXED_INPUT_SHA,plan_sha=FIXED_PLAN_SHA,
        source_bundle=args.source_bundle,source_sha=args.source_sha256,database=args.database)
    journal=Journal(args.output)
    journal("admission",result); journal.seal()
    print(audit.encoded(result).decode())


if __name__=="__main__": main()
