"""Offline ledger accounting, not invoices or evidence of successful inference.

Caller verifies immutable archives first. Preserve failed attempts; do not
sum a cached result's old private backend timing as current execution cost.
"""
import math
import re
from runtime.cache_intent_contract_pilot import valid_usage


def _seconds(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:
        raise ValueError("Invalid measured duration")
    return value


def summarize(records):
    """records maps job-relative JSON filenames to parsed objects; no I/O."""
    hp=re.compile(r"trace/http-g(\d{3})\.pending\.json")
    hr=re.compile(r"trace/http-g(\d{3})\.provider\.json")
    tp=re.compile(r"tools/attempt-(\d{3})\.pending\.json")
    tc=re.compile(r"tools/attempt-(\d{3})\.completed\.json")
    def index(pattern): return {int(m[1]):v for k,v in records.items() if (m:=pattern.fullmatch(k))}
    pending,providers=index(hp),index(hr)
    if not set(providers)<=set(pending): raise ValueError("Provider without adapter pending record")
    token_rows=[]; pt=ct=0
    for n in sorted(pending):
        raw=providers[n].get("raw") if n in providers else None
        usage=valid_usage(raw)
        if usage is not None and "total_tokens" in raw["usage"]:
            total=raw["usage"]["total_tokens"]
            if type(total) is not int or total!=sum(usage.values()): usage=None
        if usage is not None: pt+=usage["prompt_tokens"]; ct+=usage["completion_tokens"]
        token_rows.append(dict(generation=n,provider_saved=n in providers,reported_usage=usage,
            report_status="REPORTED" if usage is not None else "UNKNOWN_NOT_ZERO"))
    attempts,completed=index(tp),index(tc)
    if not set(completed)<=set(attempts): raise ValueError("Tool completion without pending ledger")
    if attempts and sorted(attempts)!=list(range(1,len(attempts)+1)):
        raise ValueError("Gapped tool ledger")
    started=records.get("tools/started.json")
    if attempts and started is None: raise ValueError("Missing initial tool budget")
    initial=started["configuration"]["initial_budget"] if started is not None else None
    if initial is not None and (type(initial) is not int or initial<0): raise ValueError("Invalid initial budget")
    balance=initial; spent=0; wall=0; tool_rows=[]
    for n in sorted(attempts):
        record=attempts[n]
        if (type(record.get("attempt")) is not int or record["attempt"]!=n or record.get("automatic_retry") is not False
                or any(type(record.get(k)) is not int or record[k]<0 for k in ("charge","balance_before","balance_after"))
                or record["balance_before"]!=balance
                or record["balance_after"]!=balance-record["charge"]):
            raise ValueError("Inconsistent recorded tool debit")
        balance=record["balance_after"]; spent+=record["charge"]
        outcome=completed.get(n); elapsed=None; status=None
        if outcome is not None:
            if any(outcome.get(k)!=v for k,v in record.items()): raise ValueError("Tool completion changed ledger")
            reply=outcome["response"]
            if (reply.get("attempt")!=n or reply.get("charged_units")!=record["charge"]
                    or reply.get("remaining_budget")!=balance):
                raise ValueError("Tool completion charge mismatch")
            elapsed=_seconds(outcome["measured_action_wall_seconds"])
            wall+=elapsed; status=reply.get("status")
        tool_rows.append(dict(attempt=n,requested_tool=record["request"]["tool"],
            recorded_declared_charge=record["charge"],completion_saved=outcome is not None,
            response_status=status,measured_action_wall_seconds=elapsed))
    closed=records.get("tools/summary.json")
    if closed is not None and (closed.get("attempts")!=len(attempts)
            or closed.get("spent_declared_units")!=spent or closed.get("remaining_budget")!=balance):
        raise ValueError("Tool summary disagrees with per-attempt ledger")
    durations=[]
    for key,record in records.items():
        if re.fullmatch(r"trace/g\d{3}\.response\.json",key):
            durations.append(_seconds(record["wall_seconds"]))
    return dict(status="RECORDED_RESOURCE_ACCOUNTING_NOT_TOTAL_COST",
        adapter_pending_records=len(pending),provider_records=len(providers),
        provider_usage_records=sum(r["reported_usage"] is not None for r in token_rows),
        unknown_usage_records=sum(r["reported_usage"] is None for r in token_rows),
        reported_prompt_tokens=pt,reported_completion_tokens=ct,token_records=token_rows,
        token_totals_are_partial_when_unknown=True,verified_model_calls=None,
        tool_pending_records=len(attempts),unresolved_tool_records=len(attempts)-len(completed),
        recorded_declared_units=spent,tool_summary_present=closed is not None,
        completed_tool_action_wall_seconds=wall,tool_records=tool_rows,
        completed_generation_callback_wall_seconds=sum(durations),
        generation_duration_records=len(durations),
        timing_scopes_are_separate_not_summed_into_total=True,
        cache_private_historical_backend_timing_counted=False,
        total_financial_cost=None,total_resource_cost_complete=False,
        unmeasured_categories=["server startup and model loading","preflight and model hashing",
            "cache creation and maintenance","shared scope-certificate preparation",
            "human review and adjudication","archival/backup","idle instance billing",
            "unreported or in-flight work after interruption"])
