"""Native backend bridge. No CLI; not permission to start a server or inference.

A separately authorized supervisor must supply current metadata, native config,
tokenizer counters and an approval-record digest. That digest is an audit link,
not an authentication or authorization mechanism. No retry or auto-download.
"""
from pathlib import Path
import re
from runtime import bird_readonly_audit as audit
from runtime import cache_intent_batch as batch
from runtime import cache_intent_entry as entry
from runtime import cache_intent_deployment as deployment
from runtime import cache_intent_contract_pilot as client
from runtime.cache_intent_journal import Journal
from scripts.fetch_bird_fixed import strict_json,absent,no_link_ancestors


class FrozenTransport:
    """Copy only verified code beside each bounded HTTP worker, never references."""
    def __init__(self,source_bundle,source_sha,output):
        entry.check_running_source(source_bundle,source_sha)
        source_bundle=Path(source_bundle)
        manifest=strict_json(entry.pinned_bytes(source_bundle/"source_manifest.json",source_sha).decode())
        self.sources={name:entry.pinned_bytes(source_bundle/"source"/name,sha)
                      for name,sha in manifest["files"].items()}
        self.output=Path(output).absolute()
        self.counts={}

    def __call__(self,job,directory,endpoint,timeout,payload):
        if job not in {f"{h}-p{p}" for h in batch.HISTORIES for p in (1,8)}:
            raise ValueError("Unknown fixed job")
        number=self.counts.get(job,0)+1
        network=self.output/job/"network"
        expected=network/f"http-g{number:03d}"
        if (Path(directory).absolute()!=expected or number>23
                or endpoint!="http://127.0.0.1:8000/v1" or not 0<timeout<=180):
            raise ValueError("Unexpected worker path/sequence/endpoint/timeout")
        self.counts[job]=number
        if number==1:
            absent(network); network.mkdir()
            for name,raw in self.sources.items():
                path=network/"source"/name; path.parent.mkdir(parents=True,exist_ok=True)
                with path.open("xb") as stream: stream.write(raw)
        # Ensure executed code still equals the frozen bytes, including on later calls.
        for name,raw in self.sources.items():
            path=network/"source"/name
            no_link_ancestors(path)
            if path.read_bytes()!=raw:
                raise ValueError("HTTP worker source changed")
        return client.request_once(expected,endpoint,"POST",timeout,request=payload)


def run_native_batch(*,inputs,input_sha,plan_sha,source_bundle,source_sha,database,
                     native_config,served_metadata,server_command,tokenizer_assets,
                     count_chat,count_text,template_sha256,output,approval_record_sha256):
    """Authorized launcher only; this function never creates user approval.

    No current live authorization exists in this project. Unit tests use an
    injected/mocked environment and do not establish server readiness.
    """
    if type(approval_record_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}",approval_record_sha256):
        raise ValueError("Explicit approval audit reference required before any work")
    local=entry.inspect_inputs(inputs=inputs,input_sha=input_sha,plan_sha=plan_sha,
        source_bundle=source_bundle,source_sha=source_sha,database=database)
    plan,caches,receipt=entry.load_private_inputs(inputs,input_sha,plan_sha)
    expected_assets=receipt["tokenizer"]["tokenizer_files_sha256"]
    if (tokenizer_assets!=expected_assets
            or template_sha256!=receipt["tokenizer"]["template_sha256_utf8"]):
        raise ValueError("Counters must use the same pinned tokenizer and template")
    output=Path(output).absolute(); absent(output)
    preflight=Journal(output/"preflight")
    preflight("inputs",local)
    preflight("approval_reference",{"sha256":approval_record_sha256,
        "operator_supplied_audit_reference_only":True,"permission_created_by_code":False})
    preflight("native_config",native_config)
    preflight("served_metadata",served_metadata)
    preflight("server_command",server_command)
    try:
        model=deployment.verify_current_files(native_config,served_metadata,server_command,tokenizer_assets)
        preflight("model_check",model)
        transport=FrozenTransport(source_bundle,source_sha,output/"experiment")
    except BaseException as exc:
        preflight("failure",{"error_type":type(exc).__name__,"automatic_retry":False})
        raise
    finally:
        preflight.seal()
    return batch._run_batch(plan,expected_plan_sha256=plan_sha,caches=caches,database=database,
        output=output/"experiment",count_chat=count_chat,count_text=count_text,
        tokenizer_sha256=tokenizer_assets["tokenizer.json"],template_sha256=template_sha256,
        transport=transport,backend="native",model=deployment.MODEL_ID,
        backend_receipt={"approval_record_sha256":approval_record_sha256,
                         "model_preflight":model,"local_inputs":local})
