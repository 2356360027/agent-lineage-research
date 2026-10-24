"""Local deployment gates and owned-worker supervision; no executable CLI.

Nothing here grants permission to start compute. Native file verification reads
weights but does not load a model. Metadata must be collected by the eventual
authorized launcher. Local unit tests use fixtures/mocked processes only.
"""
from pathlib import Path
import os
import subprocess
import sys
import time
from runtime import native
from runtime.bird_readonly_audit import digest
from runtime.cache_intent_journal import Journal
from scripts.run_contract_pilot_server import child_environment, serve_command, stop_owned_group

WINDOW=32768
MODEL_ID="Qwen/Qwen2.5-7B-Instruct"
TOKENIZER_ASSETS={"tokenizer.json","tokenizer_config.json","vocab.json","merges.txt","config.json"}


def recovery_server_command(model_path):
    """Build only; never start a service. Preserve old runner's4096 setting."""
    path=Path(model_path)
    if not path.is_absolute(): raise ValueError("Explicit local model directory required")
    command=serve_command(str(path.resolve()))
    command[command.index("--max-model-len")+1]=str(WINDOW)
    return command


def validate_metadata(config,metadata,command,tokenizer_assets):
    """Consistency only, not proof of loaded bytes, readiness or GPU capacity."""
    if config.get("deployment")!="native" or config.get("model")!=MODEL_ID:
        raise ValueError("Expected the fixed native Qwen model")
    path=Path(config["model_path"])
    if not path.is_absolute() or command!=recovery_server_command(path):
        raise ValueError("Unapproved serving command/path/settings")
    if digest(config["model_files"])!=config["model_content_sha256"]:
        raise ValueError("Reference model manifest digest mismatch")
    if set(tokenizer_assets)!=TOKENIZER_ASSETS:
        raise ValueError("All five pinned tokenizer/config assets required")
    for name,sha in tokenizer_assets.items():
        if config["model_files"].get(name,{}).get("sha256")!=sha:
            raise ValueError("Model and offline tokenizer/config identities differ")
    models=metadata.get("data") if type(metadata) is dict else None
    if (type(metadata) is not dict or metadata.get("object")!="list" or type(models) is not list or len(models)!=1
            or type(models[0]) is not dict):
        raise ValueError("Expected one served base model, no adapter ambiguity")
    model=models[0]
    root=Path(model.get("root",""))
    if (model.get("id")!=MODEL_ID or not root.is_absolute() or root.resolve()!=path.resolve()
            or type(model.get("max_model_len")) is not int or model["max_model_len"]!=WINDOW):
        raise ValueError("Served identity/path/window mismatch")
    return dict(status="METADATA_CONSISTENT_ONLY",model=MODEL_ID,max_model_len=WINDOW,
        model_content_sha256=config["model_content_sha256"],metadata_sha256=digest(metadata),
        command_sha256=digest(command),tokenizer_assets_sha256=digest(tokenizer_assets),
        loaded_weight_attestation=False,provider_token_parity=False,inference_authorized=False)


def verify_current_files(config,metadata,command,tokenizer_assets):
    """Read-only gate intended to run in a supervised child on the execution host."""
    receipt=validate_metadata(config,metadata,command,tokenizer_assets)
    native.verify(config)
    return receipt|{"status":"FILES_AND_METADATA_CONSISTENT",
                    "local_files_rehashed":True,"package_versions_checked":True}


def wait_unreaped(child,timeout):
    """Observe exit without reaping the leader before owned-group cleanup.

    Single child owner: no other thread/handler may reap this process. WNOWAIT
    preserves its waitable state until stop_owned_group finally calls wait().
    API semantics: https://docs.python.org/3/library/os.html#os.WNOWAIT
    """
    deadline=time.monotonic()+timeout
    while True:
        if time.monotonic()>=deadline:
            raise subprocess.TimeoutExpired("owned worker",timeout)
        info=os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
        if info is not None:
            if info.si_pid!=child.pid: raise RuntimeError("Unexpected wait result")
            if info.si_code==os.CLD_EXITED: return info.si_status
            if info.si_code in (os.CLD_KILLED,os.CLD_DUMPED): return -info.si_status
            raise RuntimeError("Unexpected non-exit child state")
        time.sleep(min(0.1,max(0,deadline-time.monotonic())))


def supervise_owned_worker(command,*,cwd,output,limit_seconds):
    """Linux-only worker group; caller separately authorizes supplied command.

    Stops only the process group created here. Parent remains alive to preserve
    timeout/cleanup receipts. Cleanup can add up to the helper's grace/wait time;
    this is not an instance shutdown or a proof that remote requests are cancelled.
    """
    if sys.platform!="linux": raise RuntimeError("Owned-group execution requires Linux")
    if (type(command) is not list or not command or any(type(x) is not str or not x for x in command)
            or type(limit_seconds) is not int or not 1<=limit_seconds<=1800):
        raise ValueError("Explicit command and limit1..1800 required")
    output=Path(output)
    log=Journal(output/"supervisor")
    log("started",dict(command=command,cwd=str(Path(cwd).resolve()),limit_seconds=limit_seconds,
        cloud_instance_shutdown=False,automatic_retry=False))
    child=None
    ownership_lost=False
    state={"status":"NOT_STARTED","exit_code":None,"cleanup":"NOT_NEEDED"}
    start=time.monotonic()
    with (output/"worker.log").open("xb") as stream:
        try:
            child=subprocess.Popen(command,cwd=cwd,env=child_environment(),stdin=subprocess.DEVNULL,
                stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            log("owned_process",dict(pid=child.pid,own_process_group=True))
            state["exit_code"]=wait_unreaped(child,max(0,limit_seconds-(time.monotonic()-start)))
            state["status"]="PROCESS_EXITED" if state["exit_code"]==0 else "PROCESS_FAILED"
        except subprocess.TimeoutExpired:
            state["status"]="PROCESS_TIMEOUT"
            state["inflight_requests_must_not_be_retried"]=True
        except ChildProcessError:
            ownership_lost=True
            state.update(status="OWNERSHIP_LOST",cleanup="SKIPPED_UNSAFE_TO_SIGNAL")
            raise
        except BaseException as exc:
            state.update(status="SUPERVISOR_INTERRUPTED_OR_FAILED",error_type=type(exc).__name__)
            raise
        finally:
            if child is not None and not ownership_lost:
                try:
                    # Also clean up descendants if the group leader already exited.
                    stop_owned_group(child)
                    state["cleanup"]="OWNED_GROUP_STOP_REQUESTED"
                except Exception as exc:
                    state.update(cleanup="FAILED",cleanup_error=type(exc).__name__)
            state.update(wall_seconds=time.monotonic()-start,semantic_success=False,
                         cloud_instance_shutdown=False,automatic_retry=False)
            state["process_checks_passed"]=(state["status"]=="PROCESS_EXITED" and
                state["cleanup"]=="OWNED_GROUP_STOP_REQUESTED")
            log("outcome",state); log.seal()
    return state
