"""Local conditional capacity arithmetic for every fixed development preview."""
import argparse
from pathlib import Path
from runtime import bird_readonly_audit as audit
from runtime import cache_intent_staged_preview as old
from runtime import cache_intent_recovery_preview as preview
from runtime.cache_intent_capacity import RecoveryCapacity,frame,PREFIX
from scripts.measure_staged_context_offline import archived_counters,load_inputs
from scripts.fetch_bird_fixed import absent,read_json


def main(args):
    output=Path(args.output); absent(output)
    receipt,previews=load_inputs(args.package,args.manifest_sha256,args.scope_input,args.scope_manifest_sha256)
    chat,text,tokenizer=archived_counters(args.archive_root,args.dependency_dir)
    path=Path(args.archive_root)/"model-cache/Qwen/Qwen2.5-7B-Instruct/tokenizer.json"
    config=read_json(path)
    if (tokenizer["versions"]["tokenizers"]!="0.22.1" or config["normalizer"]!={"type":"NFC"}
            or config["model"]["type"]!="BPE" or config["post_processor"]["type"]!="ByteLevel"):
        raise ValueError("Reaudit changed tokenization implementation/configuration")
    added=config["added_tokens"]
    markers={t["content"]:t for t in added}
    for name in ("<|im_start|>","<|im_end|>"):
        t=markers[name]
        if text(name)!=1 or any(name in x["content"] and x["content"]!=name for x in added):
            raise ValueError("Chat marker is not an isolated atomic delimiter")
        if any(t[k] is not False for k in ("normalized","single_word","lstrip","rstrip")):
            raise ValueError("Chat delimiter matching conditions changed")
    if any("\n" in t["content"] or t.get("lstrip") or t.get("rstrip") for t in added):
        raise ValueError("Added token could cross a framed-message seam")
    # Regression checks only, not a finite test claimed to prove all Unicode.
    samples=["","abc","\n\t  ","e\u0301","中文🙂",'\\"\n{}',
             "<|im_start|>assistant\n<|im_end|>","a"*2000,"\u0301"*100]
    for a in samples:
        for b in samples:
            messages=[{"role":"system","content":a},{"role":"user","content":b}]
            composed=sum(text(frame(m["role"],m["content"])) for m in messages)+text(PREFIX)
            if chat(messages)!=composed: raise ValueError("Framing regression failed")
    results=[]
    for pair in previews:
        packet=old.validate_preview(pair["arms"][0])
        revised=preview.make_pair(packet["current_request"],packet["evidence"],packet["schema"],
                                  packet["history"],packet["context"]["database_sha256"])
        for price,envelope in zip((1,8),revised):
            cap=RecoveryCapacity(envelope,chat,text,max_model_len=32768,
                tokenizer_sha256=tokenizer["tokenizer_files_sha256"]["tokenizer.json"],
                template_sha256=tokenizer["template_sha256_utf8"])
            actual=cap({"messages":envelope["messages"],"max_tokens":512})
            results.append({"history_id":pair["history_id"],"read_price":price,
                "envelope_sha256":audit.digest(envelope),"initial_prompt_tokens":actual["prompt_tokens_local"],
                "conditional_capacity_bound":cap.bound,"margin_to_32768":32768-cap.bound})
    report={"status":"CONDITIONAL_CAPACITY_ARITHMETIC_NOT_INFERENCE_ADMISSION",
        "input_manifest_sha256":args.manifest_sha256,"scope_manifest_sha256":args.scope_manifest_sha256,
        "tokenizer":tokenizer,"cases":results,"framing_regression_pairs":len(samples)**2,
        "conditions":["Pinned tokenizer/template with isolated chat delimiters",
            "At most 22 assistant frames each <=528 tokens, 21 observations each <=512",
            "Unchanged initial messages and at most one copy of each fixed instruction",
            "512 reserved completion tokens; exact full/framed count agreement on every request"],
        "model_calls":0,"sql_queries":0,"provider_equivalence_verified":False,
        "unconditional_capacity_guarantee":False,"inference_authorized":False}
    output.mkdir(parents=True,exist_ok=False)
    audit.save_new(output/"report.json",report)
    for source in (Path(__file__),Path(__file__).parents[1]/"runtime/cache_intent_capacity.py"):
        with (output/source.name).open("xb") as f:f.write(source.read_bytes())
    audit.save_new(output/"artifact_hashes.json",{p.name:audit.hash_file(p) for p in output.iterdir() if p.is_file()})
    print(audit.encoded({"cases":len(results),"max_bound":max(r["conditional_capacity_bound"] for r in results),
        "min_margin":min(r["margin_to_32768"] for r in results),"framing_pairs":len(samples)**2}).decode())


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("package","manifest-sha256","scope-input","scope-manifest-sha256",
                 "archive-root","dependency-dir","output"):p.add_argument("--"+name,required=True)
    main(p.parse_args())
