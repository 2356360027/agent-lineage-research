"""Offline, read-only structural audit of one archived contract pilot.

The caller separately verifies the downloaded ZIP against its remote SHA-256.
This checks extracted contents; it cannot attest loaded model bytes or semantics.
Only a new explicit --output report is written. No network or model calls occur.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys

PLAN = "73fc13d1256a76281e42c3f0aca15a92ff875322e59b1bcc9ff03348cdeea5e3"
MODEL = "d6d12ae13c606e1d3fdc29eb937828be32b1b09a755f1632b4e59654f228ff1e"
SOURCES = {
    "runtime/__init__.py": "eb741a3891d5511fe02c0bf3fc1a8e4f3bbf29cf711f0881317d052b3d4f1e16",
    "scripts/__init__.py": "81533376ead083dfed55d1a862b8cd7929ad0e4a5083901abae6944423de4a5d",
    "runtime/cache_intent_contract_pilot.py": "20c3f39fba6e5f866ef79c4befbbd55e6df70c36a65c714f2cbe899204383ba0",
    "runtime/cache_intent_prompts.py": "e92cd5098eaebd62f6f8a9424d77b3e970aef202d04869582057d2f275bd5656",
    "runtime/native.py": "e2b7b077979cf022221ee139b816d60a425e6ad1860167bd4d62e11c420b9d1b",
    "runtime/e1.py": "aabbcb37b7a4f25c722d865a9e46e0685479d69bf4a2a96a09479b71d4f19041",
    "scripts/fetch_bird_fixed.py": "49257a9e18f1c2925c9945c7f4ebbed17e54b1187060b24d9b9e732f2596f491",
}


class AuditFailure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise AuditFailure(message)


def safe(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            mode = part.lstat()
            require(not stat.S_ISLNK(mode.st_mode) and
                    not (getattr(mode, "st_file_attributes", 0) & 0x400), "Linked path forbidden")
    return path


def member(root, name):
    p = PurePosixPath(name)
    require(bool(name) and not p.is_absolute() and ".." not in p.parts and
            "\\" not in name and ":" not in name and p.as_posix() == name, "Unsafe manifest member")
    return safe(root / name)


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("Nonfinite JSON constant")
    raw = safe(path).read_bytes()
    require(len(raw) <= 16 * 1024 * 1024, "Oversized JSON artifact")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)


def inventory(root):
    paths = {}
    for path in root.rglob("*"):
        safe(path)
        require(path.is_dir() or path.is_file(), "Nonregular artifact")
        if path.is_file():
            with path.open("rb") as stream:
                paths[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return paths


def verify_hashes(root, name):
    expected = read_json(root / name)
    require(type(expected) is dict, "Hash manifest must be an object")
    for relative in expected:
        member(root, relative)
    actual = inventory(root)
    actual.pop(name, None)
    require(actual == expected, "Hash mismatch or missing/extra files: " + name)
    return len(actual)


def audit(root):
    root = safe(root)
    checked = verify_hashes(root, "SUPERVISOR_HASHES.json")
    finished = read_json(root / "finished.json")
    deploy = read_json(root / "DEPLOY_MANIFEST.json")["files"]
    reference = read_json(root / "EXPECTED_MODEL.json")
    native = read_json(root / "native-current.json")
    require(sha(encoded(native["model_files"])) == MODEL == native["model_content_sha256"], "Model reference mismatch")
    require({k: v for k, v in native.items() if k != "model_path"} ==
            {k: v for k, v in reference.items() if k != "model_path"}, "Native reference fields differ")
    for relative in ("EXPECTED_MODEL.json", "scripts/run_contract_pilot_server.py"):
        path = root / ("supervisor.py" if relative.startswith("scripts/") else relative)
        raw = path.read_bytes()
        require(deploy[relative] == {"bytes": len(raw), "sha256": sha(raw)}, "Deployment reference mismatch")
    exp = root / "experiment"
    call_dirs = sorted(p for p in exp.glob("call-*") if p.is_dir()) if exp.exists() else []
    report = {"status": "VERIFIED_PRESERVED_FAILURE", "supervisor_files_checked": checked,
              "recorded_call_directories": len(call_dirs), "semantic_scoring_performed": False,
              "loaded_weight_attestation": False, "zip_transfer_hash_checked_by_this_tool": False,
              "timing_independently_reconstructed": False,
              "cloud_instance_still_billed": True, "cleanup_errors": finished.get("cleanup_errors")}
    if not call_dirs:
        require(finished.get("status") != "COMPLETE_UNREVIEWED", "Completion without calls")
        report.update(note="No recorded inference attempts; preserved failed pre-inference run. Not proof of backend inactivity.")
        return report
    if not (exp / "artifact_hashes.json").exists() or not (exp / "summary.json").exists():
        require(finished.get("status") != "COMPLETE_UNREVIEWED", "Completion missing experiment records")
        report.update(note="Interrupted attempt: supervisor hashes verified; full reconstruction unavailable. Do not retry uncertain calls.")
        return report
    report["experiment_files_checked"] = verify_hashes(exp, "artifact_hashes.json")
    started, plan = read_json(exp / "started.json"), read_json(exp / "plan.json")
    require(sha(encoded(plan)) == PLAN == started["plan_sha256"], "Fixed plan mismatch")
    require(started["source_hashes"] == SOURCES, "Unexpected frozen source identities")
    for name, digest in SOURCES.items():
        raw = member(exp / "source", name).read_bytes()
        require(sha(raw) == digest and deploy[name] == {"bytes": len(raw), "sha256": digest}, "Frozen source mismatch")
    require(inventory(exp / "source") == SOURCES, "Extra frozen source files")
    require(read_json(exp / "native_model.json") == native and sha(encoded(native)) ==
            started["native_manifest_sha256"], "Experiment native manifest mismatch")
    require(sha((exp / "server-startup.log").read_bytes()) == started["startup_log_sha256"], "Startup snapshot mismatch")
    # This function runs in a fresh -I/-B interpreter, with outbound effects blocked.
    sys.path.insert(0, str(exp / "source"))
    frozen = importlib.import_module("runtime.cache_intent_contract_pilot")
    require(Path(frozen.__file__).resolve() == (exp / "source/runtime/cache_intent_contract_pilot.py").resolve(), "Wrong imported source")
    preflight = exp / "model-preflight"
    if preflight.exists():
        receipt = read_json(preflight / "transport.json")
        raw = (preflight / "response.raw").read_bytes()
        require(receipt["response_sha256"] == sha(raw) and receipt["response_bytes"] == len(raw)
                and receipt["status"] == "ok" and receipt["http_status"] == 200
                and not receipt["response_truncated"], "Model preflight transport mismatch")
        metadata = frozen.strict_json(raw.decode("utf-8"))
        models = [m for m in metadata["data"] if m.get("id") == native["model"]]
        require(len(models) == 1 and models[0].get("root") == native["model_path"], "Model preflight identity mismatch")
    else:
        raise AuditFailure("Recorded calls without model preflight")
    records = []
    for index, folder in enumerate(call_dirs):
        require(folder.name == f"call-{index:03d}" and index < 24, "Call gap or extra attempt")
        call = plan["calls"][index]
        require((folder / "request.json").read_bytes() == frozen.encoded(frozen.payload(call, native["model"])), "Request differs from frozen schedule")
        attempt = read_json(folder / "attempt.json")
        require(attempt["method"] == "POST" and attempt["state"] == "DISPATCH_PENDING", "Unexpected attempt receipt")
        record = {k: call[k] for k in ("history_id", "factor", "arm", "seed")}
        record.update(index=index, status="transport_failed", semantic_status="UNREVIEWED")
        try:
            if (folder / "client_stop.json").exists() or (folder / "worker_error.json").exists():
                raise RuntimeError("Worker failed")
            receipt = read_json(folder / "transport.json")
            body = (folder / "response.raw").read_bytes()
            require(receipt["response_sha256"] == sha(body) and receipt["response_bytes"] == len(body), "Transport bytes/hash mismatch")
            if receipt["status"] != "ok":
                raise RuntimeError("Transport failed")
            require(receipt["http_status"] == 200 and not receipt["response_truncated"], "Invalid successful receipt")
            raw = frozen.strict_json(body.decode("utf-8"))
            record["reported_usage"] = frozen.valid_usage(raw)
            if raw.get("model") != native["model"]:
                raise ValueError("Model mismatch")
            try:
                record.update(contract=frozen.parse_contract(raw), status="format_valid")
            except (ValueError, TypeError, KeyError) as exc:
                record.update(status="format_invalid", error_type=type(exc).__name__)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
            record["error_type"] = type(exc).__name__
        require(read_json(exp / f"assessment-{index:03d}.json") == record, "Assessment differs from raw response")
        records.append(record)
        require(record["status"] != "transport_failed" or index == len(call_dirs) - 1, "Calls continued after transport failure")
    require(len(list(exp.glob("assessment-*.json"))) == len(records), "Extra assessment records")
    usage = [r["reported_usage"] for r in records if r.get("reported_usage") is not None]
    counts = {"attempted_calls": len(records), "planned_calls": 24, "unattempted_calls": 24 - len(records),
              "format_valid": sum(r["status"] == "format_valid" for r in records),
              "format_invalid": sum(r["status"] == "format_invalid" for r in records),
              "transport_failed": sum(r["status"] == "transport_failed" for r in records),
              "calls_with_reported_usage": len(usage), "usage_complete_for_attempts": len(usage) == len(records),
              "reported_prompt_tokens": sum(u["prompt_tokens"] for u in usage),
              "reported_completion_tokens": sum(u["completion_tokens"] for u in usage), "human_approvals": 0}
    summary = read_json(exp / "summary.json")
    require(all(summary[k] == v for k, v in counts.items()) and summary["warnings"] == frozen.WARNINGS, "Summary reconstruction mismatch")
    if counts["transport_failed"]:
        require(summary["stop_reason"] == "TRANSPORT_OR_MODEL_ID_FAILURE", "Incorrect failure stop reason")
    elif len(records) < 24:
        require(summary["stop_reason"] == "CLIENT_BATCH_DEADLINE", "Missing incomplete stop reason")
    complete = len(records) == 24 and counts["transport_failed"] == 0 and summary["stop_reason"] is None
    require(summary["status"] == ("COMPLETE_UNREVIEWED" if complete else "INCOMPLETE_UNREVIEWED"), "Incorrect completion status")
    require((finished.get("runner_exit_code") == 0 and finished["status"] == "COMPLETE_UNREVIEWED") == complete, "Supervisor/runner mismatch")
    report.update(structural_counts=counts, independent_targets=2, method_efficacy_claim=False,
                  status="VERIFIED_COMPLETE_UNREVIEWED" if complete else "VERIFIED_PRESERVED_FAILURE")
    return report


def block_effects():
    import socket
    import sqlite3
    import urllib.request
    def deny(*args, **kwargs):
        raise RuntimeError("Offline audit forbids network, SQL and child execution")
    socket.socket = deny
    socket.create_connection = deny
    urllib.request.urlopen = deny
    urllib.request.OpenerDirector.open = deny
    subprocess.Popen = deny
    sqlite3.connect = deny


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._worker:
        block_effects()
        try:
            report = audit(args.run)
        except Exception as exc:
            report = {"status": "AUDIT_FAILED", "error_type": type(exc).__name__, "error": str(exc), "semantic_scoring_performed": False}
        print(json.dumps(report, ensure_ascii=False))
        return
    if args.output is None:
        parser.error("Provide a NEW --output report outside the raw run directory")
    run, output = safe(args.run), safe(args.output)
    require(not output.exists() and not output.is_relative_to(run), "Output exists or would mutate raw run")
    child = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).absolute()),
                            "--_worker", "--run", str(run)], capture_output=True, timeout=120, check=True)
    report = json.loads(child.stdout.decode("utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(encoded(report))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["status"] == "AUDIT_FAILED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
