"""Prepare a private fixed batch PLAN only. No live execution option."""
import argparse
import hashlib
from pathlib import Path
from runtime import bird_readonly_audit as audit
from runtime.cache_intent_batch import make_plan,validate_plan,HISTORIES
from runtime.cache_intent_capacity import RecoveryCapacity
from scripts.measure_staged_context_offline import load_inputs,archived_counters
from scripts.fetch_bird_fixed import absent,strict_json,no_link_ancestors


def main(args):
    output=Path(args.output); absent(output)
    receipt,pairs=load_inputs(args.package,args.manifest_sha256,args.scope_input,args.scope_manifest_sha256)
    # Hash the bytes actually parsed; do not rely on a later filesystem read.
    manifest_path=Path(args.package)/"artifact_hashes.json"; no_link_ancestors(manifest_path)
    manifest_bytes=manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest()!=args.manifest_sha256:
        raise ValueError("Input manifest changed during preparation")
    source_hashes=strict_json(manifest_bytes.decode("utf-8"))
    caches={}
    for h in HISTORIES:
        path=Path(args.package)/(h+".result.json"); no_link_ancestors(path)
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=source_hashes[path.name]:
            raise ValueError("Cache changed during preparation")
        caches[h]=strict_json(raw.decode("utf-8"))
    plan=make_plan(pairs,caches); validate_plan(plan,caches)
    chat,text,tokenizer=archived_counters(args.archive_root,args.dependency_dir)
    capacities=[]
    for job in plan["jobs"]:
        cap=RecoveryCapacity(job["envelope"],chat,text,max_model_len=32768,
            tokenizer_sha256=tokenizer["tokenizer_files_sha256"]["tokenizer.json"],
            template_sha256=tokenizer["template_sha256_utf8"])
        capacities.append({"id":job["id"],"bound":cap.bound,
            "initial":cap({"messages":job["envelope"]["messages"],"max_tokens":512})})
    output.mkdir(parents=True,exist_ok=False)
    audit.save_new(output/"PRIVATE_plan.json",plan)
    audit.save_new(output/"PRIVATE_caches.json",caches)
    audit.save_new(output/"receipt.json",dict(status="LOCAL_PLAN_ONLY",plan_sha256=audit.digest(plan),
        input_manifest_sha256=args.manifest_sha256,scope_manifest_sha256=args.scope_manifest_sha256,
        input_receipt=receipt,capacities=capacities,tokenizer=tokenizer,model_calls=0,sql_queries=0,
        inference_authorized=False,provider_equivalence_verified=False))
    sources=(Path(__file__),Path(__file__).parents[1]/"runtime/cache_intent_batch.py")
    for path in sources:
        with (output/path.name).open("xb") as f: f.write(path.read_bytes())
    audit.save_new(output/"artifact_hashes.json",{p.name:audit.hash_file(p) for p in output.iterdir() if p.is_file()})
    print(audit.encoded(dict(status="LOCAL_PLAN_ONLY",plan_sha256=audit.digest(plan),
        trajectories=12,max_generations=276,max_tools=252,model_calls=0,inference_authorized=False)).decode())


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("package","manifest-sha256","scope-input","scope-manifest-sha256",
                 "archive-root","dependency-dir","output"):
        parser.add_argument("--"+name,required=True)
    main(parser.parse_args())
