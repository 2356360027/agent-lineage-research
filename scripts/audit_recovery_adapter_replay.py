"""Replay fixed SCRIPTED fixtures through adapter with real offline token counts.

No HTTP, model inference or SQL execution. All provider replies come from the
hash-bound software audit, never presented as real model output.
"""
import argparse
from pathlib import Path
from unittest.mock import patch
from runtime import bird_readonly_audit as audit
from runtime.cache_intent_staged_adapter import Adapter
from runtime.cache_intent_journal import Journal
from runtime.cache_intent_capacity import RecoveryCapacity
from scripts.fetch_bird_fixed import absent,no_link_ancestors,read_json
from scripts.measure_staged_context_offline import archived_counters


def main(args):
    source=Path(args.source); no_link_ancestors(source)
    manifest=source/"artifact_hashes.json"
    if audit.hash_file(manifest)!=args.manifest_sha256: raise ValueError("Wrong replay manifest")
    files=read_json(manifest)
    for name,expected in files.items():
        relative=Path(name)
        if relative.is_absolute() or ".." in relative.parts: raise ValueError("Unsafe path")
        path=source/relative; no_link_ancestors(path)
        if audit.hash_file(path)!=expected: raise ValueError("Changed replay fixture")
    report=read_json(source/"report.json")
    if (report["model_calls"]!=0 or report["protocol"]!="instrumented-cost-recovery-preview-v2"
            or report["recorded_routes"]!=4): raise ValueError("Not the fixed software audit")
    count_chat,count_text,tokenizer_receipt=archived_counters(args.archive_root,args.dependency_dir)
    output=Path(args.output); absent(output); output.mkdir(parents=True,exist_ok=False)
    results=[]
    with patch("urllib.request.OpenerDirector.open",side_effect=AssertionError("No network")):
        for route in report["routes"]:
            root=source/route["route_id"]/"trace"
            log=Journal(output/route["route_id"])
            pending=sorted(root.glob("g*.pending.json"))
            if len(pending)!=23: raise ValueError("Unexpected fixture schedule")
            position=0
            counts=[]
            envelope=read_json(root/"started.json")["preview"]
            context=RecoveryCapacity(envelope,count_chat,count_text,max_model_len=32768,
                tokenizer_sha256=tokenizer_receipt["tokenizer_files_sha256"]["tokenizer.json"],
                template_sha256=tokenizer_receipt["template_sha256_utf8"])
            def transport(directory,endpoint,method,timeout,request):
                nonlocal position
                original=read_json(pending[position])
                if request["messages"]!=original["messages"]: raise ValueError("Replay prompt changed")
                reply=read_json(root/f"g{position+1:03d}.response.json")["response"]
                position+=1
                counts.append({"generation":position,"prompt":count_chat(request["messages"]),
                               "raw_output":count_text(reply["content"])})
                return {"model":"SCRIPTED_REPLAY_NOT_MODEL","choices":[{"message":{"content":reply["content"]},
                        "finish_reason":reply["finish_reason"]}]}
            adapter=Adapter(endpoint="http://127.0.0.1:8000/v1",model="SCRIPTED_REPLAY_NOT_MODEL",
                seed=17,max_tokens=512,max_seconds=30,directory=output/"unused-transport",
                save=log,context_check=context,transport=transport,protocol="recovery-v2",text_count=count_text)
            for path in pending:
                request=read_json(path)
                log(request["generation_id"]+".input",request)
                returned=adapter(request)
                log(request["generation_id"]+".output",returned)
            log.seal()
            results.append({"route_id":route["route_id"],"scripted_dispatches":position,"counts":counts,
                            "conditional_full_history_bound":context.bound})
    audit.save_new(output/"report.json",{"status":"SCRIPTED_ADAPTER_REPLAY_ONLY","model_calls":0,
        "sql_queries":0,"http_requests":0,"source_manifest_sha256":args.manifest_sha256,
        "source_sha256":audit.hash_file(__file__),"adapter_sha256":audit.hash_file(
            Path(__file__).parents[1]/"runtime/cache_intent_staged_adapter.py"),
        "provider_equivalence_verified":False,"unconditional_context_guarantee":False,
        "capacity_source_sha256":audit.hash_file(Path(__file__).parents[1]/"runtime/cache_intent_capacity.py"),
        "tokenizer_receipt":tokenizer_receipt,"routes":results})
    with (output/"replay_source.py").open("xb") as f: f.write(Path(__file__).read_bytes())
    audit.save_new(output/"artifact_hashes.json",{p.relative_to(output).as_posix():audit.hash_file(p)
        for p in sorted(output.rglob("*")) if p.is_file()})
    print(audit.encoded({"routes":len(results),"scripted_dispatches":sum(r["scripted_dispatches"] for r in results),
        "max_prompt":max(c["prompt"] for r in results for c in r["counts"]),
        "max_raw_output":max(c["raw_output"] for r in results for c in r["counts"]),"model_calls":0}).decode())


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("source","manifest-sha256","archive-root","dependency-dir","output"):
        parser.add_argument("--"+name,required=True)
    main(parser.parse_args())
