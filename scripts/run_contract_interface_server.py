"""One separately authorized 12-call batch; offline validation by default.

Reuses pure process/serving/archive helpers, never modifies the frozen 24-call
runner or its globals. The whole preflight/startup/client deadline is 600 s;
cleanup/archive/transfer are additional. Cloud-instance billing is not stopped.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import signal
import subprocess
import sys
import time

from runtime import contract_interface_diagnostic as diagnostic
from runtime.cache_intent_contract_pilot import save_new, encoded, read_small, sha, strict_json
from scripts import run_contract_pilot_server as helpers
from scripts.audit_contract_pilot_results import safe, require, member

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 600


def verify_source_manifest(root=ROOT):
    manifest = strict_json(read_small(root / "SOURCE_MANIFEST.json").decode("utf-8"))
    require(manifest["plan_sha256"] == diagnostic.PLAN_SHA, "Bundle plan identity differs")
    required = {"runtime/contract_interface_diagnostic.py", "scripts/run_contract_interface_server.py",
                "scripts/run_contract_pilot_server.py", "runtime/cache_intent_contract_pilot.py",
                "runtime/cache_intent_prompts.py", "runtime/cache_intent_prompts_v2.py",
                "runtime/native.py", "runtime/e1.py", "runtime/__init__.py", "scripts/__init__.py",
                "scripts/fetch_bird_fixed.py", "scripts/audit_contract_pilot_results.py",
                "scripts/prepare_contract_interface_diagnostic.py",
                diagnostic.DEFAULT_PLAN.as_posix(), "EXPECTED_MODEL.json"}
    require(set(manifest["files"]) == required, "Wrong allowlisted bundle members")
    for name, expected in manifest["files"].items():
        raw = read_small(member(root, name))
        require(expected == {"sha256": sha(raw), "bytes": len(raw)}, "Bundle file changed: " + name)
    return manifest


def execute(plan_path, native_path, output):
    require(sys.platform == "linux", "Execute only on the authorized Linux GPU instance")
    plan_path, native_path, output = safe(plan_path), safe(native_path), safe(output)
    manifest = verify_source_manifest()
    plan = diagnostic.load_plan(plan_path)
    config = diagnostic.read_native(native_path)
    require(not any(p.exists() for p in (output, output.with_suffix(".zip"), output.with_suffix(".archive.json"))),
            "Preserve prior attempt and archives")
    require((Path(config["model_path"]) / "model.safetensors.index.json").is_file(), "Local model missing")
    output.mkdir(parents=True, exist_ok=False)
    start, children, streams = time.monotonic(), [], []
    state = {"status": "PREPARING", "started_unix": time.time(), "limit_seconds": LIMIT,
             "maximum_inference_calls": 12, "plan_sha256": diagnostic.PLAN_SHA,
             "training": False, "cloud_instance_still_billed": True}

    def remaining():
        value = LIMIT - (time.monotonic() - start)
        if value <= 0:
            raise TimeoutError("Ten-minute supervised window expired")
        return value

    def launch(command, label):
        stream = (output / f"{label}.log").open("xb")
        streams.append(stream)
        save_new(output / f"{label}.command.json", command)
        child = subprocess.Popen(command, cwd=ROOT, env=helpers.child_environment(), stdin=subprocess.DEVNULL,
                                 stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        children.append(child)
        save_new(output / f"{label}.pid.json", {"pid": child.pid, "own_process_group": True})
        return child

    try:
        save_new(output / "started.json", state)
        save_new(output / "plan.json", plan)
        save_new(output / "native-current.json", config)
        save_new(output / "SOURCE_MANIFEST.json", manifest)
        with (output / "supervisor.py").open("xb") as stream:
            stream.write(Path(__file__).read_bytes())
        helpers.require_idle()
        with (output / "gpu-before.txt").open("xb") as stream:
            stream.write(subprocess.check_output(["nvidia-smi"], timeout=min(10, remaining())))
        verifier = launch([sys.executable, "-B", "-m", "runtime.contract_interface_diagnostic",
                           "--verify-model", str(output / "native-current.json")], "verify-model")
        code = verifier.wait(timeout=remaining())
        helpers.stop_owned_group(verifier)
        children.remove(verifier)
        require(code == 0, "Model/package verification failed")
        save_new(output / "model-verification.json", {"verified_unix": time.time(),
                     "model_content_sha256": config["model_content_sha256"], "packages": config["packages"],
                     "loaded_weight_attestation": False})
        helpers.require_idle()
        server = launch(helpers.serve_command(config["model_path"]), "server-startup")
        ready_deadline = min(start + LIMIT, time.monotonic() + 300)
        while True:
            require(server.poll() is None, "Model server exited")
            remaining()
            if time.monotonic() >= ready_deadline:
                raise TimeoutError("Server startup exceeded limit")
            try:
                metadata = helpers.read_models()
            except (OSError, ValueError):
                time.sleep(2)  # Readiness GET only; never a generation retry.
                continue
            models = [m for m in metadata.get("data", []) if m.get("id") == diagnostic.MODEL]
            require(len(models) == 1 and Path(models[0].get("root", "")).resolve() ==
                    Path(config["model_path"]).resolve(), "Served model identity mismatch")
            save_new(output / "served-models.json", metadata)
            break
        remaining()
        runner = launch([sys.executable, "-B", "-m", "runtime.contract_interface_diagnostic",
                         "--plan", str(output / "plan.json"), "--native-config", str(output / "native-current.json"),
                         "--startup-log", str(output / "server-startup.log"), "--output", str(output / "experiment"),
                         "--execute", "--ack-unreviewed-interface-diagnostic"], "experiment")
        code = runner.wait(timeout=remaining())
        state.update(status="COMPLETE_UNREVIEWED" if code == 0 else "FAILED_PRESERVED", runner_exit_code=code)
        return code
    except BaseException as exc:
        state.update(status="FAILED_OR_INTERRUPTED_PRESERVED", error_type=type(exc).__name__,
                     uncertain_inflight_calls_must_not_be_retried=True)
        raise
    finally:
        errors = []
        for child in reversed(children):
            try:
                helpers.stop_owned_group(child)
            except Exception as exc:
                errors.append({"pid": child.pid, "error_type": type(exc).__name__})
        for stream in streams:
            stream.close()
        state.update(finished_unix=time.time(), supervised_seconds=time.monotonic() - start, cleanup_errors=errors)
        save_new(output / "finished.json", state)
        helpers.archive(output)
        print("Batch stopped; cloud instance may still be billed. Inspect cleanup_errors before stopping it.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=diagnostic.DEFAULT_PLAN)
    parser.add_argument("--native-config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--ack-unreviewed-interface-diagnostic", action="store_true")
    args = parser.parse_args()
    diagnostic.load_plan(args.plan)
    if not args.execute:
        print("PLAN_ONLY: twelve calls, no model execution; deadline 600 s if separately authorized.")
        return
    if not (args.native_config and args.output and args.ack_unreviewed_interface_diagnostic):
        parser.error("Explicit new-batch acknowledgement, native config and new output required")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(execute(args.plan, args.native_config, args.output))


if __name__ == "__main__":
    main()
