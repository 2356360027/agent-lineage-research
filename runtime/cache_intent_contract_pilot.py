"""Bounded, UNREVIEWED contract-only inference; never training or semantic grading.

Plan mode is offline. Execution requires a separately authorized native backend,
the explicit acknowledgement, its local content manifest and a startup log.
The frozen protective prompts do NOT exercise tools or enforce runtime costs.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from runtime import cache_intent_prompts as prompts
from scripts.fetch_bird_fixed import no_link_ancestors, strict_json

VERSION = "unreviewed-contract-pilot-v1"
PACKAGE = Path("runtime/runs/cache-intent-dev-package-20261012-001")
PREVIEWS_SHA = "bc930e0b167045a09f78e8e6cf4925d642b6dd4a28712ba39236b3a170d12f45"
PROMPTS_SHA = "e92cd5098eaebd62f6f8a9424d77b3e970aef202d04869582057d2f275bd5656"
SEED = 20261013
MAX_CALLS = 24
MAX_TOKENS = 768
MAX_SECONDS = 1800
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HISTORIES = {f"h{qid}{suffix}" for qid in (200, 234) for suffix in "abc"}
WARNINGS = [
    "Unreviewed development material: two targets, six constructed histories; not 24 independent tasks.",
    "Explicit contract elicitation is protective; tools, SQL and actual cache access are disabled.",
    "Budget 100, SQL cost 10 and cache cost 1/8 are frozen prompt text, not charged resources or money.",
    "No semantic approval, task accuracy, intent-drift/C labels, effect estimates or efficacy claims.",
    "Local weight hashes and server metadata do not attest which weights a running process loaded.",
    "The client deadline does not stop instance billing or prove cancellation of server-side generation.",
]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save_new(path, value):
    with Path(path).open("xb") as stream:
        stream.write(encoded(value))


def read_small(path, limit=4 * 1024 * 1024):
    path = Path(path).absolute()
    no_link_ancestors(path)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Input exceeds byte limit")
    return raw


def validate_previews(records):
    if type(records) is not list or len(records) != 12:
        raise ValueError("Expected all twelve frozen pairs")
    seen = set()
    for record in records:
        if type(record) is not dict or set(record) != {
            "private_history_id", "factor", "envelopes", "envelope_hashes"
        }:
            raise ValueError("Unexpected private wrapper fields")
        ident, factor = record["private_history_id"], record["factor"]
        if ident not in HISTORIES or factor not in {"availability", "read_cost"}:
            raise ValueError("Unknown history or contrast")
        if (ident, factor) in seen:
            raise ValueError("Duplicate pair")
        seen.add((ident, factor))
        pair = record["envelopes"]
        if type(pair) is not list or len(pair) != 2:
            raise ValueError("Expected two envelopes")
        prompts.assert_pair(*pair, factor=factor)
        if record["envelope_hashes"] != [sha(encoded(e)) for e in pair]:
            raise ValueError("Envelope hash mismatch")
    if seen != {(ident, factor) for ident in HISTORIES for factor in ("availability", "read_cost")}:
        raise ValueError("Incomplete fixed pair coverage")


def load_plan(package=PACKAGE):
    raw = read_small(Path(package) / "PRIVATE_prompt_previews.json")
    if sha(raw) != PREVIEWS_SHA or sha(read_small(prompts.__file__)) != PROMPTS_SHA:
        raise ValueError("Frozen input or prompt source changed")
    records = strict_json(raw.decode("utf-8"))
    validate_previews(records)
    calls = []
    for record in records:
        ident, factor = record["private_history_id"], record["factor"]
        paired_seed = int(sha(encoded([SEED, ident, factor]))[:7], 16)
        for arm, envelope in enumerate(record["envelopes"]):
            calls.append({"history_id": ident, "factor": factor, "arm": arm,
                          "seed": paired_seed, "envelope": envelope})
    # A deterministic hash order, fixed before any model outcomes; no seed search.
    calls.sort(key=lambda c: sha(encoded([SEED, c["history_id"], c["factor"], c["arm"]])))
    return {"version": VERSION, "purpose": "UNREVIEWED_CONTRACT_ONLY_EXPLORATION",
            "human_approvals": 0, "formal_inference_eligible": False,
            "independent_targets": 2, "history_count": 6, "maximum_model_calls": MAX_CALLS,
            "max_completion_tokens_per_call": MAX_TOKENS, "maximum_client_seconds": MAX_SECONDS,
            "previews_sha256": PREVIEWS_SHA, "prompt_source_sha256": PROMPTS_SHA,
            "seed": SEED, "warnings": WARNINGS, "calls": calls}


def payload(call, model):
    # Never send wrapper IDs, labels, candidate answers, history relations or questionnaire data.
    return deepcopy(call["envelope"]) | {
        "model": model, "seed": call["seed"], "temperature": 0,
        "top_p": 1, "max_tokens": MAX_TOKENS, "stream": False,
    }


def endpoint_url(value):
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})/v1", value)
    if not match or not 1 <= int(match[1]) <= 65535:
        raise ValueError("Use an explicit 127.0.0.1 HTTP port and /v1; no credentials or proxy")
    return value


def parse_contract(raw):
    if type(raw) is not dict or type(raw.get("choices")) is not list or len(raw["choices"]) != 1:
        raise ValueError("Expected one choice")
    choice = raw["choices"][0]
    if type(choice) is not dict or choice.get("finish_reason") != "stop":
        raise ValueError("Truncated or non-stop generation")
    message = choice.get("message")
    if (type(message) is not dict or message.get("tool_calls") or message.get("function_call")
            or message.get("refusal") or not isinstance(message.get("content"), str)):
        raise ValueError("Missing content or unexpected tool call")
    contract = strict_json(message["content"])
    if (type(contract) is not dict or set(contract) != set(prompts.CONTRACT_FIELDS)
            or any(not isinstance(v, str) or not v.strip() for v in contract.values())):
        raise ValueError("Invalid eight-field contract; no repair or imputation")
    return contract


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirect forbidden")


def http_worker(directory, endpoint, method, timeout):
    """Separate process so a slow/trickling HTTP response cannot defeat client timeout."""
    directory = Path(directory)
    base = endpoint_url(endpoint)
    if method not in {"GET", "POST"}:
        raise ValueError("Invalid method")
    body = read_small(directory / "request.json") if method == "POST" else None
    target = base + ("/models" if method == "GET" else "/chat/completions")
    started = time.monotonic()
    result = {"status": "failed", "http_status": None, "response_truncated": False}
    count, written = 0, 0
    response_hash = hashlib.sha256()
    raw_stream = (directory / "response.raw").open("xb")
    try:
        request = urllib.request.Request(target, body, {"Content-Type": "application/json"}, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc  # Preserve HTTP error bodies too, with no retry/fallback.
        with response:
            result["http_status"] = response.code
            while count <= MAX_RESPONSE_BYTES:
                reader = getattr(response, "read1", response.read)
                chunk = reader(min(65536, MAX_RESPONSE_BYTES + 1 - count))
                if not chunk:
                    break
                retained = chunk[:MAX_RESPONSE_BYTES - written]
                raw_stream.write(retained)
                raw_stream.flush()  # A killed worker must not lose already received bytes.
                response_hash.update(retained)
                written += len(retained)
                count += len(chunk)
        result["response_truncated"] = count > MAX_RESPONSE_BYTES
        if result["http_status"] == 200 and not result["response_truncated"]:
            result["status"] = "ok"
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    finally:
        raw_stream.close()
        result.update(response_bytes=written, response_sha256=response_hash.hexdigest(),
                      elapsed_seconds=time.monotonic() - started)
        save_new(directory / "transport.json", result)


def request_once(directory, endpoint, method, timeout, request=None):
    """Exclusive attempt directory; never resume, overwrite, silently retry or read proxy env."""
    endpoint_url(endpoint)
    directory = Path(directory).absolute()
    no_link_ancestors(directory)
    worker_source = directory.parent / "source"
    if not (worker_source / "runtime" / "cache_intent_contract_pilot.py").is_file():
        raise ValueError("HTTP worker requires the archived source tree")
    directory.mkdir()
    save_new(directory / "attempt.json", {"state": "DISPATCH_PENDING", "method": method,
                                          "started_unix": time.time()})
    if request is not None:
        save_new(directory / "request.json", request)
    command = [sys.executable, "-B", "-m", "runtime.cache_intent_contract_pilot", "_http",
               "--directory", str(directory), "--endpoint", endpoint, "--method", method,
               "--timeout", str(max(0.01, timeout))]
    try:
        process = subprocess.run(command, capture_output=True, timeout=timeout, cwd=worker_source)
    except subprocess.TimeoutExpired:
        save_new(directory / "client_stop.json", {"reason": "HTTP_WORKER_DEADLINE",
                 "server_request_may_still_be_running": True})
        raise RuntimeError("HTTP worker deadline; do not retry") from None
    if process.returncode != 0:
        save_new(directory / "worker_error.json", {"returncode": process.returncode,
                 "stderr_sha256": sha(process.stderr), "stdout_sha256": sha(process.stdout)})
        raise RuntimeError("HTTP worker failed; preserve this attempt")
    receipt = strict_json(read_small(directory / "transport.json").decode("utf-8"))
    body = read_small(directory / "response.raw", MAX_RESPONSE_BYTES)
    if receipt.get("response_sha256") != sha(body) or receipt.get("status") != "ok":
        raise RuntimeError("HTTP request failed or exceeded limits; no retry")
    return strict_json(body.decode("utf-8"))


def valid_usage(raw):
    usage = raw.get("usage") if type(raw) is dict else None
    keys = ("prompt_tokens", "completion_tokens")
    if type(usage) is dict and all(type(usage.get(k)) is int and usage[k] >= 0 for k in keys):
        return {k: usage[k] for k in keys}
    return None


def run_calls(plan, model, output, endpoint, *, transport=request_once, clock=time.monotonic):
    """Caller reserves output and records native-model preflight before using this loop."""
    output = Path(output)
    started, records = clock(), []
    stop_reason = None
    if len(plan["calls"]) != MAX_CALLS:
        raise ValueError("Expected the entire fixed call schedule")
    for index, call in enumerate(plan["calls"]):
        remaining = MAX_SECONDS - (clock() - started)
        if remaining <= 0:
            stop_reason = "CLIENT_BATCH_DEADLINE"
            break
        record = {k: call[k] for k in ("history_id", "factor", "arm", "seed")}
        record.update(index=index, status="transport_failed", semantic_status="UNREVIEWED")
        records.append(record)
        try:
            raw = transport(output / f"call-{index:03d}", endpoint, "POST", min(180, remaining),
                            request=payload(call, model))
            record["reported_usage"] = valid_usage(raw)
            if raw.get("model") != model:
                raise ValueError("Response model ID differs from configured model")
            try:
                record["contract"] = parse_contract(raw)
                record["status"] = "format_valid"
            except (ValueError, TypeError, KeyError) as exc:
                record.update(status="format_invalid", error_type=type(exc).__name__)
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            stop_reason = "TRANSPORT_OR_MODEL_ID_FAILURE"
        save_new(output / f"assessment-{index:03d}.json", record)
        print(f"Attempt {index + 1}/{MAX_CALLS}: {record['status']}", flush=True)
        if stop_reason:
            break
    usages = [r["reported_usage"] for r in records if r.get("reported_usage") is not None]
    summary = {
        "status": "COMPLETE_UNREVIEWED" if len(records) == MAX_CALLS and stop_reason is None else "INCOMPLETE_UNREVIEWED",
        "attempted_calls": len(records), "planned_calls": MAX_CALLS,
        "unattempted_calls": MAX_CALLS - len(records), "stop_reason": stop_reason,
        "format_valid": sum(r["status"] == "format_valid" for r in records),
        "format_invalid": sum(r["status"] == "format_invalid" for r in records),
        "transport_failed": sum(r["status"] == "transport_failed" for r in records),
        "calls_with_reported_usage": len(usages), "usage_complete_for_attempts": len(usages) == len(records),
        "reported_prompt_tokens": sum(u["prompt_tokens"] for u in usages),
        "reported_completion_tokens": sum(u["completion_tokens"] for u in usages),
        "client_loop_seconds": clock() - started, "human_approvals": 0, "warnings": WARNINGS,
    }
    save_new(output / "summary.json", summary)
    return summary


def execute(plan, output, native_config, startup_log, endpoint):
    from runtime import native
    endpoint_url(endpoint)
    config = strict_json(read_small(native_config).decode("utf-8"))
    keys = ("deployment", "model", "model_path", "model_files", "model_content_sha256", "packages")
    config = {k: config[k] for k in keys}
    if config["deployment"] != "native" or not isinstance(config["model"], str) or not config["model"].strip():
        raise ValueError("Need a native-model content manifest from the execution host")
    startup = read_small(startup_log, 8 * 1024 * 1024)
    if not startup.strip():
        raise ValueError("Retain a nonempty server startup log; it is evidence, not attestation")
    output = Path(output).absolute()
    no_link_ancestors(output)
    output.mkdir(parents=True, exist_ok=False)
    save_new(output / "plan.json", plan)
    save_new(output / "native_model.json", config)
    (output / "server-startup.log").write_bytes(startup)
    source_paths = [Path(__file__), Path(prompts.__file__), Path(native.__file__),
                    Path("runtime/e1.py"), Path("scripts/fetch_bird_fixed.py"),
                    Path("runtime/__init__.py"), Path("scripts/__init__.py")]
    sources = {}
    for path in source_paths:
        relative = f"{path.parent.name}/{path.name}"
        raw = read_small(path)
        dest = output / "source" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("xb") as stream:
            stream.write(raw)
        sources[relative] = sha(raw)
    save_new(output / "started.json", {"started_unix": time.time(), "python": sys.version,
             "plan_sha256": sha(encoded(plan)), "source_hashes": sources,
             "native_manifest_sha256": sha(encoded(config)), "startup_log_sha256": sha(startup)})
    try:
        native.verify(config)
        metadata = request_once(output / "model-preflight", endpoint, "GET", 15)
        matches = [m for m in metadata["data"] if m.get("id") == config["model"]]
        if (len(matches) != 1 or
                Path(matches[0].get("root", "")).resolve() != Path(config["model_path"]).resolve()):
            raise ValueError("Served model name/root does not match native manifest")
        summary = run_calls(plan, config["model"], output, endpoint)
    except BaseException as exc:
        save_new(output / "execution_failure.json", {"error_type": type(exc).__name__,
                 "state": "UNRESOLVED_DO_NOT_RESUME", "finished_unix": time.time()})
        raise
    finally:
        manifest = {p.relative_to(output).as_posix(): sha(p.read_bytes())
                    for p in sorted(output.rglob("*")) if p.is_file()}
        save_new(output / "artifact_hashes.json", manifest)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    if len(sys.argv) > 1 and sys.argv[1] == "_http":
        parser.add_argument("_http")
        parser.add_argument("--directory", required=True)
        parser.add_argument("--endpoint", required=True)
        parser.add_argument("--method", choices=("GET", "POST"), required=True)
        parser.add_argument("--timeout", type=float, required=True)
        args = parser.parse_args()
        http_worker(args.directory, args.endpoint, args.method, args.timeout)
        return
    parser.add_argument("--package", type=Path, default=PACKAGE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--native-config", type=Path)
    parser.add_argument("--startup-log", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--ack-unreviewed-contract-only", action="store_true")
    args = parser.parse_args()
    plan = load_plan(args.package)
    if not args.execute:
        print(json.dumps({k: v for k, v in plan.items() if k != "calls"} |
                         {"mode": "PLAN_ONLY_NO_MODEL_CALLS", "plan_sha256": sha(encoded(plan))}, indent=2))
        return
    if not (args.ack_unreviewed_contract_only and args.native_config and args.startup_log and args.output):
        parser.error("Execute requires explicit unreviewed acknowledgement, native config, startup log and new output")
    result = execute(plan, args.output, args.native_config, args.startup_log, args.endpoint)
    print(json.dumps(result, indent=2))
    if result["status"] != "COMPLETE_UNREVIEWED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
