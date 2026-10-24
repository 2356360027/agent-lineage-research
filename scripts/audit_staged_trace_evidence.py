"""Private, offline secondary trace audit. Not blinded review or semantic scoring."""
import argparse
import hashlib
from pathlib import Path
from runtime import bird_readonly_audit as audit
from runtime import cache_intent_trace_evidence as evidence
from runtime.cache_intent_batch import HISTORIES
from runtime.cache_intent_journal import Journal
from scripts.prepare_staged_contract_review import load_archive,declared_backend
from scripts.fetch_bird_fixed import absent,strict_json


def prepare(run,*,manifest_sha,plan_sha,output):
    run=Path(run).absolute(); output=Path(output).absolute()
    if output==run or output.is_relative_to(run): raise ValueError("Preserve raw archive")
    absent(output)
    raw,manifest=load_archive(run,manifest_sha,include_trace=True)
    read=lambda name:strict_json(raw[name].decode("utf-8"))
    plan=read("batch/plan.json"); backend=declared_backend(raw)
    expected={f"{h}-p{p}" for h in HISTORIES for p in (1,8)}
    if (audit.digest(plan)!=plan_sha or len(plan["jobs"])!=12
            or {j["id"] for j in plan["jobs"]}!=expected
            or plan["independent_targets"]!=2):
        raise ValueError("Wrong fixed plan/backend")
    outcome=read("batch/summary.json")
    if len(outcome["rows"])!=12 or {r["id"] for r in outcome["rows"]}!=expected:
        raise ValueError("Missing planned outcomes")
    rows=[]
    for job in plan["jobs"]:
        prefix=job["id"]+"/trace/"
        records={name[len(prefix):-5]:read(name) for name in raw if name.startswith(prefix)}
        rows.append(dict(job_id=job["id"],envelope=job["envelope"],
            evidence=evidence.inspect(job["envelope"],records),
            trace_record_hashes={name:manifest[name] for name in raw if name.startswith(prefix)}))
    journal=Journal(output)
    journal("private-evidence",dict(status="SECONDARY_TRACE_EVIDENCE_NOT_SEMANTIC_RESULTS",
        source_backend=backend["kind"],conditions_visible=True,
        warning="Freeze separate primary contract review first; do not use outcomes to revise those labels",
        source_manifest_sha256=manifest_sha,plan_sha256=plan_sha,rows=rows))
    for label,path in (("audit",Path(__file__)),("reconstruction",Path(evidence.__file__))):
        source=path.read_bytes()
        journal(label+"-source",dict(sha256=hashlib.sha256(source).hexdigest(),
                                   source_utf8=source.decode("utf-8")))
    receipt=dict(status="OFFLINE_TRACE_LINKS_AUDITED",planned_slots=12,independent_targets=2,
        saved_contracts=sum(r["evidence"]["contract"] is not None for r in rows),
        recorded_generations=sum(len(r["evidence"]["events"]) for r in rows),
        semantic_labels_generated=False,model_calls=0,sql_queries=0,network_requests=0,
        server_dispatch_or_execution_attested=False,causal_effect_established=False)
    journal("receipt",receipt); journal.seal()
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("run","manifest-sha","plan-sha","output"):
        parser.add_argument("--"+name,required=True)
    a=parser.parse_args()
    print(audit.encoded(prepare(a.run,manifest_sha=a.manifest_sha,plan_sha=a.plan_sha,output=a.output)).decode())


if __name__=="__main__": main()
