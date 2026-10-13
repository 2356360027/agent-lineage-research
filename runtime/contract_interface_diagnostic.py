"""Separate fixed twelve-call diagnostic client; default is offline validation.

No training, SQL, semantic grading, retries, resumption or plan substitution.
Use the separate 600-second supervisor for authorized real execution.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import sys
import time
import json

from runtime import cache_intent_contract_pilot as old
from scripts.audit_contract_pilot_results import member, require, safe

ROOT = Path(__file__).resolve().parents[1]
PLAN_SHA = "f10fed100719736eec657a42f212c207e7394c6014de652619db8560a06d2f94"
MODEL_SHA = "d6d12ae13c606e1d3fdc29eb937828be32b1b09a755f1632b4e59654f228ff1e"
MODEL = "Qwen/Qwen2.5-7B-Instruct"
PACKAGES = {"torch": "2.6.0+cu124", "vllm": "0.8.3", "transformers": "4.57.1", "modelscope": "1.34.0"}
LIMIT = 600
DEFAULT_PLAN = Path("runtime/runs/contract-interface-diagnostic-preparation-20261013-001/plan.json")


def validate(plan):
    require(old.sha(old.encoded(plan)) == PLAN_SHA, "Not the frozen twelve-call plan")
    require(len(plan["calls"]) == 12, "Wrong fixed call count")
    for index, call in enumerate(plan["calls"]):
        require(call["index"] == index and old.sha(old.encoded(call["request"])) == call["request_sha256"],
                "Request hash/index mismatch")


def load_plan(path=DEFAULT_PLAN):
    raw = old.read_small(path)
    require(old.sha(raw) == PLAN_SHA, "Frozen plan bytes changed")
    plan = old.strict_json(raw.decode("utf-8"))
    validate(plan)
    for name, digest in plan["preparation_source_sha256"].items():
        require(old.sha(old.read_small(member(ROOT, name))) == digest, "Preparation source changed: " + name)
    return plan


def read_native(path):
    config = old.strict_json(old.read_small(path).decode("utf-8"))
    keys = ("deployment", "model", "model_path", "model_files", "model_content_sha256", "packages")
    config = {key: config[key] for key in keys}
    require(config["deployment"] == "native" and config["model"] == MODEL, "Native model ID mismatch")
    require(config["packages"] == PACKAGES, "Package reference changed")
    require(config["model_content_sha256"] == MODEL_SHA and
            old.sha(old.encoded(config["model_files"])) == MODEL_SHA, "Model content reference changed")
    require(Path(config["model_path"]).is_absolute(), "Local absolute model path required")
    return config


def run_calls(plan, output, endpoint, *, transport=None, clock=time.monotonic):
    validate(plan)
    old.endpoint_url(endpoint)
    output = safe(output)
    require(output.is_dir(), "Caller must reserve a fresh output directory")
    require(not any(output.glob("call-*")) and not any(output.glob("assessment-*.json")) and
            not (output / "summary.json").exists(), "Never resume a prior attempt")
    transport = old.request_once if transport is None else transport
    records, reason, start = [], None, clock()
    for call in plan["calls"]:
        remaining = LIMIT - (clock() - start)
        if remaining <= 0:
            reason = "CLIENT_DEADLINE"
            break
        index = call["index"]
        record = {k: call[k] for k in ("index", "history_id", "interface_arm", "paired_seed", "request_sha256")}
        record.update(status="transport_failed", semantic_status="UNREVIEWED")
        records.append(record)
        try:
            raw = transport(output / f"call-{index:03d}", endpoint, "POST", min(180, remaining),
                            request=deepcopy(call["request"]))
            record["reported_usage"] = old.valid_usage(raw)
            require(type(raw) is dict and raw.get("model") == MODEL, "Response model mismatch")
            try:
                record.update(contract=old.parse_contract(raw), status="format_valid")
            except (ValueError, TypeError, KeyError):
                record["status"] = "format_invalid"
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            reason = "TRANSPORT_OR_MODEL_ID_FAILURE"
        old.save_new(output / f"assessment-{index:03d}.json", record)
        print(f"Attempt {index + 1}/12: {record['status']}", flush=True)
        if reason:
            break
    usages = [r["reported_usage"] for r in records if r.get("reported_usage") is not None]
    summary = {"status": "COMPLETE_UNREVIEWED" if len(records) == 12 and reason is None else "INCOMPLETE_UNREVIEWED",
               "planned_calls": 12, "attempted_calls": len(records), "unattempted_calls": 12 - len(records),
               "stop_reason": reason, "human_approvals": 0, "semantic_scoring_performed": False,
               "calls_with_reported_usage": len(usages), "usage_complete_for_attempts": len(usages) == len(records),
               "reported_prompt_tokens": sum(u["prompt_tokens"] for u in usages),
               "reported_completion_tokens": sum(u["completion_tokens"] for u in usages),
               "client_loop_seconds": clock() - start, "warnings": plan["warnings"]}
    summary.update({status: sum(r["status"] == status for r in records)
                    for status in ("format_valid", "format_invalid", "transport_failed")})
    old.save_new(output / "summary.json", summary)
    return summary


def execute(plan_path, output, native_path, startup_path, endpoint):
    from runtime import native
    plan = load_plan(plan_path)
    config = read_native(native_path)
    old.endpoint_url(endpoint)
    startup = old.read_small(startup_path, 8 * 1024 * 1024)
    require(bool(startup.strip()), "Nonempty actual startup log required")
    output = safe(output)
    output.mkdir(parents=True, exist_ok=False)
    try:
        old.save_new(output / "plan.json", plan)
        old.save_new(output / "native_model.json", config)
        with (output / "server-startup.log").open("xb") as stream:
            stream.write(startup)
        names = set(plan["preparation_source_sha256"]) | {
            "runtime/contract_interface_diagnostic.py", "scripts/run_contract_interface_server.py",
            "scripts/run_contract_pilot_server.py", "runtime/native.py", "runtime/e1.py",
            "runtime/__init__.py", "scripts/__init__.py"}
        sources = {}
        for name in sorted(names):
            raw = old.read_small(member(ROOT, name))
            path = member(output / "source", name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(raw)
            sources[name] = old.sha(raw)
        old.save_new(output / "started.json", {"started_unix": time.time(), "python": sys.version,
                     "source_sha256": sources, "plan_sha256": PLAN_SHA,
                     "native_manifest_sha256": old.sha(old.encoded(config))})
        native.verify(config)
        metadata = old.request_once(output / "model-preflight", endpoint, "GET", 15)
        matches = [m for m in metadata["data"] if m.get("id") == MODEL]
        require(len(matches) == 1 and Path(matches[0].get("root", "")).resolve() ==
                Path(config["model_path"]).resolve(), "Served model ID/root mismatch")
        return run_calls(plan, output, endpoint)
    except BaseException as exc:
        old.save_new(output / "execution_failure.json", {"error_type": type(exc).__name__,
                     "state": "UNRESOLVED_DO_NOT_RESUME", "finished_unix": time.time()})
        raise
    finally:
        old.save_new(output / "artifact_hashes.json", {
            p.relative_to(output).as_posix(): old.sha(old.read_small(p, 8 * 1024 * 1024))
            for p in sorted(output.rglob("*")) if p.is_file()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--native-config", type=Path)
    parser.add_argument("--startup-log", type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--ack-unreviewed-interface-diagnostic", action="store_true")
    parser.add_argument("--verify-model", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.verify_model:
        from runtime.native import verify
        verify(read_native(args.verify_model))
        return
    plan = load_plan(args.plan)
    if not args.execute:
        print(json.dumps({"status": "PLAN_ONLY_NO_MODEL_CALLS", "plan_sha256": PLAN_SHA,
                          "planned_calls": len(plan["calls"]), "executed_calls": 0}))
        return
    if not (args.ack_unreviewed_interface_diagnostic and args.output and args.native_config and args.startup_log):
        parser.error("Requires batch approval, explicit acknowledgement, new output, native config and startup log")
    result = execute(args.plan, args.output, args.native_config, args.startup_log, args.endpoint)
    raise SystemExit(0 if result["status"] == "COMPLETE_UNREVIEWED" else 2)


if __name__ == "__main__":
    main()
