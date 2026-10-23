"""Fixed development allocation and SCRIPTED-only local batch wiring.

No default network transport or live CLI. This validates orchestration before a
separately authorized live launcher exists. Scripted replies are never evidence
about the model. A cooperative deadline is not a process/instance shutdown.
"""
from copy import deepcopy
from pathlib import Path
import time
from runtime import bird_readonly_audit as audit
from runtime import cache_intent_recovery_preview as preview
from runtime import cache_intent_staged_preview as old
from runtime import cache_intent_tools as tools
from runtime.cache_intent_capacity import RecoveryCapacity
from runtime.cache_intent_staged_adapter import Adapter
from runtime.cache_intent_staged_loop import run_recovery_trajectory
from runtime.cache_intent_observation_bound import ObservationBound
from runtime.cache_intent_journal import Journal, verify
from scripts.fetch_bird_fixed import absent

HISTORIES = ("h200a","h200b","h200c","h234a","h234b","h234c")


def make_plan(pairs,caches):
    if (len(pairs)!=6 or {p["history_id"] for p in pairs}!=set(HISTORIES)
            or set(caches)!=set(HISTORIES)):
        raise ValueError("Exactly the six fixed development histories required")
    pairs = {p["history_id"]:p for p in pairs}
    jobs=[]
    for index,history in enumerate(HISTORIES):
        pair=pairs[history]["arms"]
        packet=old.validate_preview(pair[0])
        expected=old.make_pair(packet["current_request"],packet["evidence"],packet["schema"],
            packet["history"],packet["context"]["database_sha256"])
        if audit.encoded(pair)!=audit.encoded(expected): raise ValueError("Noncanonical paired input")
        revised=preview.make_pair(packet["current_request"],packet["evidence"],packet["schema"],
            packet["history"],packet["context"]["database_sha256"])
        tools.valid_result(caches[history])
        if caches[history]["sql_sha256"]!=audit.digest(packet["history"]["sql"]):
            raise ValueError("History/cache mismatch")
        # Fixed alternating arm order, not randomized or retrospectively balanced.
        for arm in ((0,1) if index%2==0 else (1,0)):
            price=(1,8)[arm]
            jobs.append(dict(id=f"{history}-p{price}",history_id=history,
                read_price=price,seed=1700+index,envelope=revised[arm],
                cached_result_sha256=audit.digest(caches[history])))
    return dict(version="recovery-development-plan-v1",jobs=jobs,
        trajectories=12,independent_targets=2,max_generations=276,max_tools=252,
        max_seconds=1800,completion_cap=512,proposed_window=32768,
        inference_authorized=False,confirmation_study=False)


def validate_plan(plan,caches):
    # Reconstruct the entire allocation, not just its checksum or successful rows.
    pairs=[]
    for history in HISTORIES:
        jobs=[j for j in plan["jobs"] if j["history_id"]==history]
        if len(jobs)!=2: raise ValueError("Missing or duplicate paired job")
        packet=preview.validate_preview(jobs[0]["envelope"])
        pairs.append({"history_id":history,"arms":old.make_pair(packet["current_request"],
            packet["evidence"],packet["schema"],packet["history"],packet["context"]["database_sha256"])})
    if plan!=make_plan(pairs,caches): raise ValueError("Frozen plan/allocation changed")


class DeadlineSession:
    def __init__(self,session,check): self.session,self.check=session,check
    def describe(self): return self.session.describe()
    def call(self,name,args):
        self.check()
        result=self.session.call(name,args)
        # ToolSession already retained the raw completion if it arrived late.
        self.check()
        return result


def scripted_batch(plan,*,expected_plan_sha256,caches,database,output,count_chat,count_text,
                   tokenizer_sha256,template_sha256,transport,clock=time.monotonic):
    """Injected scripted transport only. Never pass a network client here.

    transport(job_id, payload) must return an explicitly SCRIPTED provider reply.
    Tests/replays supply it; model identity is deliberately not configurable.
    No resumption, retry, reference-answer loading or semantic labels.
    """
    validate_plan(plan,caches)
    if audit.digest(plan)!=expected_plan_sha256: raise ValueError("Plan hash changed")
    if not callable(transport): raise ValueError("Explicit scripted transport required")
    output=Path(output); absent(output)
    records=Journal(output/"batch")
    records("plan",plan)
    start=clock()
    rows=[dict(id=j["id"],status="NOT_STARTED",generation_calls=0) for j in plan["jobs"]]
    dispatched=0
    stop_reason=None
    def check():
        if clock()-start>=plan["max_seconds"]: raise TimeoutError("Batch deadline reached")
    for position,job in enumerate(plan["jobs"]):
        if stop_reason: break
        session=trace=adapter=None
        entry=rows[position]
        records(f"job-{position:02d}.pending",{"id":job["id"],"automatic_retry":False})
        try:
            check()
            packet=preview.validate_preview(job["envelope"])
            cap=RecoveryCapacity(job["envelope"],count_chat,count_text,
                max_model_len=plan["proposed_window"],tokenizer_sha256=tokenizer_sha256,
                template_sha256=template_sha256)
            root=output/job["id"]
            trace=Journal(root/"trace")
            session=tools.ToolSession(database_path=database,
                database_sha256=packet["context"]["database_sha256"],
                history_sql=packet["history"]["sql"],cached_result=caches[job["history_id"]],
                input_receipt={"plan_sha256":expected_plan_sha256,"job_id":job["id"],
                               "backend":"SCRIPTED_NOT_MODEL"},output=root/"tools",
                materialized_available=True,cache_read_cost=job["read_price"],
                budget_profile=tools.RECOVERY_PROFILE)
            def dispatch(directory,endpoint,method,timeout,request):
                nonlocal dispatched
                check()
                if dispatched>=plan["max_generations"]: raise RuntimeError("Batch call ceiling")
                dispatched+=1
                value=transport(job["id"],deepcopy(request))
                # Adapter saves the provider response before validating its ID.
                return value
            adapter=Adapter(endpoint="http://127.0.0.1:8000/v1",model="SCRIPTED_NOT_MODEL",
                seed=job["seed"],max_tokens=512,max_seconds=max(1,int(plan["max_seconds"]-(clock()-start))),
                directory=root/"unused-network",save=trace,context_check=cap,transport=dispatch,
                protocol="recovery-v2",text_count=count_text,clock=clock)
            result=run_recovery_trajectory(job["envelope"],DeadlineSession(session,check),adapter,
                                           trace,ObservationBound(count_chat))
            entry.update(status=result["status"],generation_calls=result["generation_calls"])
            if result["status"]=="ENVIRONMENT_INVALID": stop_reason="ENVIRONMENT_INVALID"
        except Exception as exc:
            entry.update(status="FAILED",error_type=type(exc).__name__)
            stop_reason=type(exc).__name__
        finally:
            entry["generation_calls"]=adapter.count if adapter else 0
            if session:
                try:
                    summary=session.close(); verify(session.output)
                    entry["tool_summary"]=summary
                    if summary["status"]!="TOOL_SESSION_CLOSED":
                        entry["status"]="ENVIRONMENT_INVALID"
                        stop_reason=summary["status"]
                except Exception as exc:
                    entry["status"]="FINALIZATION_FAILED"
                    entry["finalization_error"]=type(exc).__name__
                    stop_reason="FINALIZATION_FAILED"
            if trace:
                try: trace.seal()
                except Exception as exc:
                    entry["status"]="FINALIZATION_FAILED"
                    entry["trace_seal_error"]=type(exc).__name__
                    stop_reason="FINALIZATION_FAILED"
        records(f"job-{position:02d}.outcome",entry)
    summary=dict(status="SCRIPTED_BATCH_STOPPED" if stop_reason else "SCRIPTED_BATCH_FINISHED",
        stop_reason=stop_reason,rows=rows,planned_trajectories=12,scripted_dispatches=dispatched,
        model_calls=0,semantic_judgments=0,wall_seconds=clock()-start,
        deadline_kind="cooperative_checks_not_hard_process_timeout",inference_authorized=False)
    records("summary",summary); records.seal()
    audit.save_new(output/"artifact_hashes.json",{p.relative_to(output).as_posix():audit.hash_file(p)
        for p in sorted(output.rglob("*")) if p.is_file()})
    return summary
