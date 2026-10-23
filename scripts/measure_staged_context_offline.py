"""Count archived-tokenizer tokens, never run a model or contact a service.

The repeated-cache trajectory is SCRIPTED, not an observed rollout or a worst
case bound. Counts do not certify compatibility with a future serving template.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_staged_preview as staged
from runtime.cache_intent_staged_loop import ACTION_INSTRUCTION
from runtime.cache_intent_prompts import CONTRACT_FIELDS
from scripts.fetch_bird_fixed import absent, no_link_ancestors, read_json
from scripts.audit_staged_input_exposure import inspect


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package", required=True)
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--archive-root", required=True)
    p.add_argument("--dependency-dir", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    output = Path(args.output); absent(output)
    package = Path(args.package)
    input_receipt, previews = inspect(package, args.manifest_sha256)
    root = Path(args.archive_root); no_link_ancestors(root)
    model = root / "model-cache/Qwen/Qwen2.5-7B-Instruct"
    manifest = read_json(root / "MANIFEST.json")
    fingerprints = {}
    for name in ("tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt", "config.json"):
        path = model / name; no_link_ancestors(path)
        measured = audit.hash_file(path)
        if manifest["files"][path.relative_to(root).as_posix()]["sha256"] != measured:
            raise ValueError("Archived tokenizer file mismatch")
        fingerprints[name] = measured
    dependency = Path(args.dependency_dir).resolve(); no_link_ancestors(dependency)
    sys.path.insert(0, str(dependency))
    import tokenizers
    import jinja2
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    tokenizer = tokenizers.Tokenizer.from_file(str(model / "tokenizer.json"))
    tokenizer.no_truncation(); tokenizer.no_padding()
    config = read_json(model / "tokenizer_config.json")
    template = config["chat_template"]
    renderer = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True).from_string(template)

    def count(messages):
        # Our adapter supports only these plain-text roles and has a system first.
        if messages[0]["role"] != "system" or any(
            set(m) != {"role", "content"} or m["role"] not in {"system","user","assistant"}
            or type(m["content"]) is not str for m in messages):
            raise ValueError("Unsupported chat branch for this offline audit")
        text = renderer.render(messages=messages, tools=None, add_generation_prompt=True)
        manual = "".join("<|im_start|>"+m["role"]+"\n"+m["content"]+"<|im_end|>\n"
                         for m in messages) + "<|im_start|>assistant\n"
        if text != manual:
            raise ValueError("Archived template differs from independently constructed plain-role framing")
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    rows = []
    contract = json.dumps(dict.fromkeys(CONTRACT_FIELDS, "unspecified"))
    action = '{"kind":"read_cache","cache_id":"v000"}'
    for pair in previews:
        cache = read_json(package / (pair["history_id"]+".result.json"))
        for envelope in pair["arms"]:
            price = staged.validate_preview(envelope)["context"]["cache"]["read_cost"]
            initial = count(envelope["messages"])
            messages = staged.next_generation_messages(envelope, contract)
            messages[-1]["content"] += "\n" + ACTION_INSTRUCTION
            checkpoints = {"0": count(messages)}
            for i in range(1, 21):
                observation = {"status":"EXECUTED", "complete":True, "charged_units":price,
                    "remaining_budget":200-i*price, "attempt":i,
                    "columns":deepcopy(cache["columns"]), "rows":deepcopy(cache["rows"])}
                messages.extend([{"role":"assistant", "content":action}, {"role":"user",
                    "content":json.dumps({"tool_observation":observation},ensure_ascii=False,allow_nan=False)}])
                if i in (1,5,10,20): checkpoints[str(i)] = count(messages)
            messages.append({"role":"user", "content":
                "Tool horizon reached. Return only answer or abstain; no further tools."})
            rows.append({"history_id":pair["history_id"], "read_price":price,
                "initial_prompt_tokens":initial, "scripted_action_prompt_tokens":checkpoints,
                "scripted_terminal_prompt_tokens":count(messages)})
    report = {"status":"OFFLINE_TOKENIZATION_NOT_MODEL_EXPERIMENT", "cases":rows,
        "input_manifest_sha256":args.manifest_sha256, "tokenizer_files_sha256":fingerprints,
        "archive_manifest_sha256":audit.hash_file(root/"MANIFEST.json"),
        "template_sha256_utf8":audit.hashlib.sha256(template.encode("utf-8")).hexdigest(),
        "versions":{"tokenizers":tokenizers.__version__,"jinja2":jinja2.__version__},
        "source_sha256":audit.hash_file(__file__), "source_input_audit":input_receipt["source_sha256"],
        "model_calls":0, "sql_queries":0, "provider_equivalence_verified":False,
        "actual_serving_context_limit":None, "completion_reservation_used":None,
        "warnings":["Twenty repeated cache reads and placeholder contract are scripted, not observed.",
            "Counts omit completion reservation; add the chosen cap before admission.",
            "Actual model SQL/action output can be larger; these are not upper bounds.",
            "Historical archive hashes are checked, not a current server installation."]}
    output.mkdir(parents=True, exist_ok=False)
    audit.save_new(output/"report.json", report)
    with (output/"measurement_source.py").open("xb") as stream:
        stream.write(Path(__file__).read_bytes())
    audit.save_new(output/"artifact_hashes.json", {
        f.name:audit.hash_file(f) for f in output.iterdir() if f.is_file()})
    print(audit.encoded(report).decode("utf-8"))


if __name__ == "__main__": main()
