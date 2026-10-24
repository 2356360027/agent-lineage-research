"""Single-deadline Linux deployment entry; defaults to a non-executing plan.

Future execution requires fresh human approval. An audit digest is not permission.
The parent owns the worker group; the server inherits it rather than escaping into
a new session. No downloads, retries, existing-service reuse or instance shutdown.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from runtime import cache_intent_deployment as deployment
from runtime import cache_intent_entry as entry
from runtime import cache_intent_native_batch as bridge
from runtime import cache_intent_tokenizer as tokenizer
from runtime.bird_readonly_audit import encoded, hash_file
from runtime.cache_intent_journal import Journal
from scripts.fetch_bird_fixed import absent, strict_json
from scripts.run_contract_pilot_server import child_environment, require_idle, read_models

PATHS={"inputs","source_bundle","database","native_config","output"}
DIGESTS={"source_sha256","native_config_sha256","approval_record_sha256"}


def validate_spec(spec):
    if type(spec) is not dict or set(spec)!=PATHS|DIGESTS|{"limit_seconds"}:
        raise ValueError("Exact deployment specification required")
    for key in PATHS:
        if type(spec[key]) is not str or not Path(spec[key]).is_absolute():
            raise ValueError("Explicit absolute paths required")
    for key in DIGESTS:
        if type(spec[key]) is not str or re.fullmatch(r"[0-9a-f]{64}",spec[key]) is None:
            raise ValueError("Pinned hashes and approval audit reference required")
    if type(spec["limit_seconds"]) is not int or not 1<=spec["limit_seconds"]<=1800:
        raise ValueError("Whole-worker limit must be within 1..1800 seconds")
    return spec


def await_ready(server,timeout=300):
    """GET readiness only; no generation or restart. Outer deadline still applies."""
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if server.poll() is not None:
            raise RuntimeError("Owned server exited before readiness")
        try:
            return read_models()
        except (OSError,ValueError):
            time.sleep(min(1,max(0,deadline-time.monotonic())))
    raise TimeoutError("Owned server readiness deadline reached")


def worker(spec):
    """Run only inside the parent's new process group. Parent performs cleanup."""
    validate_spec(spec)
    if sys.platform!="linux" or os.getpgrp()!=os.getpid():
        raise RuntimeError("Worker must be its supervised Linux process-group leader")
    output=Path(spec["output"])/"execution"
    log=Journal(output/"deployment")
    try:
        log("spec",spec)
        local=entry.inspect_inputs(inputs=spec["inputs"],input_sha=entry.FIXED_INPUT_SHA,
            plan_sha=entry.FIXED_PLAN_SHA,source_bundle=spec["source_bundle"],
            source_sha=spec["source_sha256"],database=spec["database"])
        log("local_admission",local)
        _,_,receipt=entry.load_private_inputs(spec["inputs"],entry.FIXED_INPUT_SHA,entry.FIXED_PLAN_SHA)
        config=strict_json(entry.pinned_bytes(spec["native_config"],spec["native_config_sha256"]).decode())
        log("native_config",config)
        if not {"torch","vllm","transformers"}<=set(config.get("packages",{})):
            raise ValueError("Core serving package versions must be pinned")
        pinned=receipt["tokenizer"]
        assets=pinned["tokenizer_files_sha256"]
        command=deployment.recovery_server_command(config["model_path"])
        # Pre-start consistency uses expected metadata, explicitly NOT observed readiness.
        expected=dict(object="list",data=[dict(id=deployment.MODEL_ID,
            root=config["model_path"],max_model_len=deployment.WINDOW)])
        deployment.validate_metadata(config,expected,command,assets)
        deployment.native.verify(config)  # All weights/packages before creating the server.
        chat,text,counters=tokenizer.local_counters(config["model_path"],expected_assets=assets,
            expected_template_sha256=pinned["template_sha256_utf8"],expected_versions=pinned["versions"])
        log("tokenizer",counters)
        require_idle()  # Existing services/GPU jobs cause refusal, never termination.
        log("server_command",command)
        with (output/"server.log").open("xb") as stream:
            server=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stream,
                stderr=subprocess.STDOUT,env=child_environment(),start_new_session=False)
            log("server_process",dict(pid=server.pid,worker_pgid=os.getpgrp(),
                inherits_worker_group=True,existing_service_reused=False))
            metadata=await_ready(server)
            log("served_metadata",metadata)
            deployment.validate_metadata(config,metadata,command,assets)
            result=bridge.run_native_batch(inputs=spec["inputs"],input_sha=entry.FIXED_INPUT_SHA,
                plan_sha=entry.FIXED_PLAN_SHA,source_bundle=spec["source_bundle"],
                source_sha=spec["source_sha256"],database=spec["database"],native_config=config,
                served_metadata=metadata,server_command=command,tokenizer_assets=assets,
                count_chat=chat,count_text=text,template_sha256=pinned["template_sha256_utf8"],
                output=output/"batch_run",approval_record_sha256=spec["approval_record_sha256"])
        log("batch_result",result)
        # Process exit success must not turn an incomplete batch into a success.
        complete=result.get("status")=="NATIVE_UNREVIEWED_BATCH_FINISHED"
        log("outcome",dict(batch_finished=complete,semantic_success=False,
            cleanup_owner="PARENT_SUPERVISOR",automatic_retry=False))
        return complete
    except BaseException as exc:
        log("failure",dict(error_type=type(exc).__name__,automatic_retry=False,
            cleanup_owner="PARENT_SUPERVISOR"))
        raise
    finally:
        log.seal()


def launch(spec):
    """Explicitly authorized caller only. Never invoked during local preparation."""
    validate_spec(spec)
    if sys.platform!="linux": raise RuntimeError("Execution requires Linux")
    output=Path(spec["output"]); absent(output)
    request=Journal(output/"request")
    request("spec",spec); request.seal()
    path=output/"request/spec.json"
    root=Path(__file__).resolve().parents[1]
    command=[sys.executable,"-B","-m","runtime.cache_intent_launcher",
             "--worker-spec",str(path),"--worker-sha256",hash_file(path)]
    # Includes input hashing, tokenizer construction, model verification, startup
    # and all model/tool calls. Cleanup grace is additional; billing continues.
    return deployment.supervise_owned_worker(command,cwd=root,output=output/"execution",
                                             limit_seconds=spec["limit_seconds"])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec")
    parser.add_argument("--spec-sha256")
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--worker-spec",help=argparse.SUPPRESS)
    parser.add_argument("--worker-sha256",help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.worker_spec or args.worker_sha256:
        if not (args.worker_spec and args.worker_sha256) or args.spec or args.spec_sha256 or args.execute:
            parser.error("Internal worker arguments cannot be combined with public options")
        spec=strict_json(entry.pinned_bytes(args.worker_spec,args.worker_sha256).decode())
        return 0 if worker(spec) else 1
    if not args.execute:
        print(encoded(dict(status="PLAN_ONLY",model_calls=0,server_started=False,
            inference_authorized=False,maximum_worker_seconds=1800,
            cleanup_grace_additional=True,cloud_instance_shutdown=False)).decode())
        return 0
    if not args.spec or not args.spec_sha256:
        parser.error("Execution requires a pinned specification and fresh human approval")
    spec=strict_json(entry.pinned_bytes(args.spec,args.spec_sha256).decode())
    result=launch(spec)
    print(encoded(result).decode())
    return 0 if result["process_checks_passed"] else 1


if __name__=="__main__": sys.exit(main())
