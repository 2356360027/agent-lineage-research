"""One bounded Linux deployment; no install, download, resume, or model retry.

Only --execute starts compute. The supervisor owns the child process groups it
starts and stops only those groups. Stopping them does NOT stop cloud billing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

from runtime.cache_intent_contract_pilot import (
    NoRedirect, encoded, load_plan, read_small, save_new, sha,
)
from scripts.fetch_bird_fixed import no_link_ancestors, strict_json

ROOT = Path(__file__).resolve().parents[1]
MODEL = "/gemini/code/model-cache/Qwen/Qwen2___5-7B-Instruct"
PLAN_SHA = "73fc13d1256a76281e42c3f0aca15a92ff875322e59b1bcc9ff03348cdeea5e3"
LIMIT = 1800  # Includes this supervisor's preflight, model startup and inference.


def verify_bundle(root):
    manifest = strict_json(read_small(root / "DEPLOY_MANIFEST.json").decode("utf-8"))
    for name, expected in manifest["files"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe bundle member")
        raw = read_small(root / name)
        if len(raw) != expected["bytes"] or sha(raw) != expected["sha256"]:
            raise ValueError(f"Bundle content changed: {name}")
    if sha(encoded(load_plan())) != PLAN_SHA:
        raise ValueError("Fixed experiment plan changed")
    return manifest


def child_environment():
    # Do not archive the ambient environment (it may contain credentials).
    env = os.environ.copy()
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               HF_HUB_DISABLE_TELEMETRY="1", VLLM_NO_USAGE_STATS="1",
               DO_NOT_TRACK="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    for name in ("OPENAI_API_KEY", "VLLM_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
                 "PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    return env


def serve_command(model):
    return [sys.executable, "-m", "vllm.entrypoints.openai.api_server",
            "--model", model, "--served-model-name", "Qwen/Qwen2.5-7B-Instruct",
            "--host", "127.0.0.1", "--port", "8000", "--dtype", "bfloat16",
            "--max-model-len", "4096", "--max-num-seqs", "4",
            "--gpu-memory-utilization", "0.85", "--enforce-eager",
            "--generation-config", "vllm", "--seed", "20261001"]


def stop_owned_group(process):
    # Every process passed here MUST have been started below with start_new_session=True.
    # Kill the owned group even if its leader exited but left descendants alive.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            time.sleep(1)
    process.wait(timeout=10)


def require_idle():
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex(("127.0.0.1", 8000)) == 0:
            raise RuntimeError("Port 8000 is occupied; existing service will not be touched")
    pids = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
        timeout=10, text=True)
    if pids.strip():
        raise RuntimeError("GPU compute process exists; inspect it before running")


def read_models():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open("http://127.0.0.1:8000/v1/models", timeout=2) as response:
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("Oversized model metadata")
        return strict_json(raw.decode("utf-8"))


def archive(work):
    files = sorted(p for p in work.rglob("*") if p.is_file())
    hashes = {}
    for path in files:
        no_link_ancestors(path)
        hashes[path.relative_to(work).as_posix()] = sha(path.read_bytes())
    save_new(work / "SUPERVISOR_HASHES.json", hashes)
    dest = work.with_suffix(".zip")
    with zipfile.ZipFile(dest, "x", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(p for p in work.rglob("*") if p.is_file()):
            no_link_ancestors(path)
            output.write(path, work.name + "/" + path.relative_to(work).as_posix())
    with dest.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    save_new(work.with_suffix(".archive.json"), {"archive": dest.name, "sha256": digest,
                                              "bytes": dest.stat().st_size})
    print(f"ARCHIVE: {dest}\nARCHIVE_SHA256: {digest}", flush=True)


def execute(model, work):
    if sys.platform != "linux":
        raise RuntimeError("Execute only on the authorized Linux GPU instance")
    no_link_ancestors(work)
    if work.exists() or work.with_suffix(".zip").exists() or work.with_suffix(".archive.json").exists():
        raise FileExistsError("Preserve prior attempt; do not rerun or overwrite")
    reference = strict_json(read_small(ROOT / "EXPECTED_MODEL.json").decode("utf-8"))
    reference["model_path"] = str(Path(model).resolve(strict=True))
    # Ensure this is a local model directory, never a Hugging Face repository ID.
    if not (Path(reference["model_path"]) / "model.safetensors.index.json").is_file():
        raise ValueError("Local indexed model is missing; no automatic download")
    work.mkdir(parents=True, exist_ok=False)
    deadline = time.monotonic() + LIMIT
    children, logs = [], []
    state = {"status": "PREPARING", "started_unix": time.time(), "limit_seconds": LIMIT,
             "maximum_inference_calls": 24, "training": False, "work": str(work)}
    save_new(work / "started.json", state)
    save_new(work / "native-current.json", reference)
    for name in ("DEPLOY_MANIFEST.json", "EXPECTED_MODEL.json"):
        (work / name).write_bytes(read_small(ROOT / name))
    if (ROOT / "UPLOAD_RECEIPT.json").is_file():
        (work / "UPLOAD_RECEIPT.json").write_bytes(read_small(ROOT / "UPLOAD_RECEIPT.json"))
    (work / "supervisor.py").write_bytes(Path(__file__).read_bytes())

    def remaining():
        result = deadline - time.monotonic()
        if result <= 0:
            raise TimeoutError("Whole deployment reached 30-minute limit")
        return result

    def launch(command, label):
        stream = (work / f"{label}.log").open("xb")
        logs.append(stream)
        save_new(work / f"{label}.command.json", command)
        child = subprocess.Popen(command, cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL,
                                 stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        children.append(child)
        save_new(work / f"{label}.pid.json", {"pid": child.pid, "own_process_group": True})
        print(f"{label}: PID {child.pid}; log={work / (label + '.log')}", flush=True)
        return child

    try:
        require_idle()
        gpu = subprocess.check_output(["nvidia-smi"], timeout=min(10, remaining()))
        (work / "gpu-before.txt").write_bytes(gpu)
        verify = launch([sys.executable, "-B", "-m", "scripts.run_contract_pilot_server",
                         "--verify-model", str(work / "native-current.json")], "verify-model")
        verify_exit = verify.wait(timeout=min(600, remaining()))
        stop_owned_group(verify)
        children.remove(verify)
        if verify_exit != 0:
            raise RuntimeError("Model content/package verification failed; see verify-model.log")
        save_new(work / "model-verification.json", {"verified_unix": time.time(),
                 "model_content_sha256": reference["model_content_sha256"],
                 "packages": reference["packages"], "loaded_weight_attestation": False})
        require_idle()
        server = launch(serve_command(reference["model_path"]), "server-startup")
        startup_deadline = min(deadline, time.monotonic() + 300)
        while True:
            if server.poll() is not None:
                raise RuntimeError("Model server exited; see server-startup.log")
            remaining()
            if time.monotonic() >= startup_deadline:
                raise TimeoutError("Model startup exceeded five minutes")
            try:
                metadata = read_models()
            except (OSError, ValueError):
                time.sleep(2)  # Readiness GET only; never retry a generation request.
                continue
            matches = [m for m in metadata.get("data", []) if m.get("id") == reference["model"]]
            if len(matches) != 1 or Path(matches[0].get("root", "")).resolve() != Path(reference["model_path"]):
                raise RuntimeError("Served model identity/path differs from verified reference")
            save_new(work / "served-models.json", metadata)
            break
        print("MODEL_READY. Starting the fixed 24 calls; no retries.", flush=True)
        runner = launch([sys.executable, "-B", "-m", "runtime.cache_intent_contract_pilot",
                         "--native-config", str(work / "native-current.json"),
                         "--startup-log", str(work / "server-startup.log"),
                         "--output", str(work / "experiment"),
                         "--execute", "--ack-unreviewed-contract-only"], "experiment")
        result = runner.wait(timeout=remaining())
        state.update(status="COMPLETE_UNREVIEWED" if result == 0 else "FAILED_PRESERVED",
                     runner_exit_code=result)
        return result
    except BaseException as exc:
        state.update(status="FAILED_OR_INTERRUPTED_PRESERVED", error_type=type(exc).__name__,
                     error=str(exc), uncertain_inflight_calls_must_not_be_retried=True)
        raise
    finally:
        cleanup_errors = []
        for child in reversed(children):
            try:
                stop_owned_group(child)
            except Exception as exc:
                cleanup_errors.append({"pid": child.pid, "error_type": type(exc).__name__})
        for stream in logs:
            stream.close()
        state.update(finished_unix=time.time(), cleanup_errors=cleanup_errors,
                     cloud_instance_still_billed=True)
        save_new(work / "finished.json", state)
        archive(work)
        if cleanup_errors:
            print("WARNING: process cleanup failed. Inspect GPU immediately. Cloud instance still billed.", flush=True)
        else:
            print("Owned process groups stopped. Cloud instance still billed.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--model-path", default=MODEL)
    parser.add_argument("--verify-model", type=Path)
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.verify_model:
        from runtime.native import verify
        verify(strict_json(read_small(args.verify_model).decode("utf-8")))
        print("MODEL_CONTENT_AND_PACKAGES_MATCH", flush=True)
        return
    verify_bundle(ROOT)
    if not args.execute:
        print("BUNDLE_OK. PLAN_ONLY; 24 calls, two unreviewed targets, no training.")
        print(f"PLAN_SHA256: {PLAN_SHA}")
        return
    # SIGTERM performs the same archival/owned-process cleanup as Ctrl+C.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(execute(args.model_path, ROOT / "runtime/runs/server-pilot-20261013-001"))


if __name__ == "__main__":
    main()
