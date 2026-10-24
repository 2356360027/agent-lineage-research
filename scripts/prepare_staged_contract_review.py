"""Offline primary-endpoint review export; no inference or semantic labeling.

Reads a sealed batch archive. Later execution status never gates its earlier
contract. Reviewer files hide condition metadata, NOT condition clues inside
verbatim model text. Organizer mapping and all outputs are private artifacts.
"""
import argparse
from copy import deepcopy
import hashlib
from pathlib import Path, PurePosixPath
import re

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_batch import HISTORIES
from runtime import cache_intent_recovery_preview as preview
from runtime.cache_intent_entry import pinned_bytes
from runtime.cache_intent_journal import Journal
from scripts.fetch_bird_fixed import absent,no_link_ancestors,strict_json


def load_archive(root,manifest_sha,*,include_trace=False):
    root=Path(root).absolute(); no_link_ancestors(root)
    manifest=strict_json(pinned_bytes(root/"artifact_hashes.json",manifest_sha).decode("utf-8"))
    if type(manifest) is not dict or not manifest:
        raise ValueError("Nonempty sealed archive required")
    for name,sha in manifest.items():
        path=PurePosixPath(name)
        if (type(name) is not str or path.is_absolute() or path.as_posix()!=name
                or any(p in ("..", ".") or ":" in p or "\\" in p for p in path.parts)
                or not re.fullmatch(r"[0-9a-f]{64}",sha)):
            raise ValueError("Unsafe manifest member")
    entries=list(root.rglob("*"))
    for path in entries: no_link_ancestors(path)
    actual={p.relative_to(root).as_posix() for p in entries if p.is_file()}
    if actual!=set(manifest)|{"artifact_hashes.json"}:
        raise ValueError("Archive has missing or unmanifested files")
    # Hash every file, but retain only the small JSON files needed for review.
    raw={}
    for name,sha in manifest.items():
        path=root/name
        if audit.hash_file(path)!=sha: raise ValueError("Archive content changed")
        trace_member=re.fullmatch(r"h(?:200|234)[abc]-p[18]/trace/[gt]\d{3}\.(?:pending|response|failure|exposure|exposure_failure)\.json",name)
        if (name.startswith("batch/") or name.endswith(("/g001.pending.json","/g001.response.json"))
                or (include_trace and trace_member)):
            raw[name]=pinned_bytes(path,sha)
    return raw,manifest


def prepare(run,*,manifest_sha,plan_sha,output):
    run=Path(run).absolute(); output=Path(output).absolute()
    if output==run or output.is_relative_to(run): raise ValueError("Keep review outside raw archive")
    absent(output)
    raw,manifest=load_archive(run,manifest_sha)
    def read(name): return strict_json(raw[name].decode("utf-8"))
    plan=read("batch/plan.json")
    if audit.digest(plan)!=plan_sha: raise ValueError("Wrong frozen plan")
    jobs=plan["jobs"]
    expected={f"{h}-p{p}" for h in HISTORIES for p in (1,8)}
    if (len(jobs)!=12 or {j["id"] for j in jobs}!=expected
            or plan["independent_targets"]!=2):
        raise ValueError("Expected the twelve fixed development slots")
    backend=read("batch/backend.json")
    if backend.get("kind") not in ("scripted","native"):
        raise ValueError("Explicit source backend required; never infer model provenance")
    summary=read("batch/summary.json")
    if (len(summary["rows"])!=12 or {r["id"] for r in summary["rows"]}!=expected):
        raise ValueError("Batch omitted planned outcomes")
    outcomes={r["id"]:r["status"] for r in summary["rows"]}
    items=[]; mapping=[]
    for job in jobs:
        ident=job["id"]
        if ident!=f'{job["history_id"]}-p{job["read_price"]}':
            raise ValueError("Mismatched pair identity")
        packet=preview.validate_preview(job["envelope"])
        alias="R-"+hashlib.sha256((manifest_sha+ident).encode()).hexdigest()[:16]
        pending=f"{ident}/trace/g001.pending.json"
        response=f"{ident}/trace/g001.response.json"
        content=None; state="NO_SAVED_RESPONSE"; finish=None
        if response in raw:
            if pending not in raw: raise ValueError("Contract response lacks its request")
            request=read(pending)
            expected_request=dict(generation_id="g001",stage="contract",contract_event=None,
                                  messages=job["envelope"]["messages"])
            if request!=expected_request: raise ValueError("Not the frozen pre-result generation")
            record=read(response)
            if record["request_sha256"]!=audit.digest(request): raise ValueError("Request link mismatch")
            reply=record["response"]
            if type(reply) is not dict or type(reply.get("content")) is not str:
                state="UNUSABLE_RESPONSE"
            else:
                content=reply["content"]; finish=reply.get("finish_reason")
                state="NONSTOP_RESPONSE" if finish!="stop" else "FORMAT_VALID_UNREVIEWED"
                if finish=="stop":
                    try: preview.next_generation_messages(job["envelope"],content)
                    except ValueError: state="FORMAT_INVALID_UNREVIEWED"
        items.append(dict(item_id=alias,current_request=packet["current_request"],
            evidence=packet["evidence"],schema=packet["schema"],raw_contract=content,
            response_availability=state,finish_reason=finish,
            review=dict(whole_contract_vs_request=None,material_omission=None,
                field_misplacement_only=None,evidence_quote=None,reason=None)))
        mapping.append(dict(item_id=alias,job_id=ident,history_id=job["history_id"],
            read_price=job["read_price"],later_outcome=outcomes[ident],
            response_path=response if response in raw else None,
            response_sha256=manifest.get(response),pending_sha256=manifest.get(pending)))
    # Opaque ordering, not arm alternation; no deduplication or outcome filtering.
    items.sort(key=lambda x:x["item_id"])
    journal=Journal(output)
    common=dict(status="BLANK_PRIMARY_ENDPOINT_REVIEW_NOT_RESULTS",
        source_backend=backend["kind"],source_is_scripted=backend["kind"]=="scripted",
        instructions=[
            "Complete an independent current-request interpretation before rating outputs.",
            "Judge the whole contract. Field placement alone is not a missing requirement.",
            "Do not infer intention from later SQL, final correctness or experimental condition.",
            "Use match, contradiction, or unresolved; leave unavailable text unresolved.",
            "Record material omission separately; omission alone need not be contradiction.",
            "Original text is unedited and may reveal condition clues; this is label masking only.",
            "Two previously inspected tasks: development review, not independent confirmation."],
        reviewer=dict(code=None,date=None,independently_completed=None,
            prior_exposure=None,assistance_used=None,current_request_review_reference=None),
        items=items)
    for reviewer in (1,2): journal(f"reviewer-{reviewer}",deepcopy(common))
    journal("organizer-only-mapping",dict(source_manifest_sha256=manifest_sha,
        plan_sha256=plan_sha,records=mapping))
    source=Path(__file__).read_bytes()
    journal("exporter-source",dict(sha256=hashlib.sha256(source).hexdigest(),
        source_utf8=source.decode("utf-8")))
    receipt=dict(status="BLANK_PRIVATE_REVIEW_EXPORTED",planned_slots=12,
        independent_targets=2,available_texts=sum(i["raw_contract"] is not None for i in items),
        missing_responses=sum(i["response_availability"]=="NO_SAVED_RESPONSE" for i in items),
        source_backend=backend["kind"],semantic_labels_generated=False,
        primary_endpoint_gated_on_later_success=False,complete_blinding_claimed=False,
        model_calls=0,sql_queries=0,network_requests=0)
    journal("receipt",receipt); journal.seal()
    return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("run","manifest-sha","plan-sha","output"): p.add_argument("--"+name,required=True)
    a=p.parse_args()
    print(audit.encoded(prepare(a.run,manifest_sha=a.manifest_sha,plan_sha=a.plan_sha,output=a.output)).decode())


if __name__=="__main__": main()
