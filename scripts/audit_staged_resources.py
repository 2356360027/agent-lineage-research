"""Read-only batch resource accounting. Never filters by answer success."""
import argparse
import hashlib
from pathlib import Path
import re
from runtime import bird_readonly_audit as audit
from runtime import cache_intent_resource_accounting as resource
from runtime.cache_intent_entry import pinned_bytes
from runtime.cache_intent_batch import HISTORIES
from runtime.cache_intent_journal import Journal
from scripts.prepare_staged_contract_review import load_archive
from scripts.fetch_bird_fixed import absent,strict_json


def prepare(run,*,manifest_sha,plan_sha,output):
    run=Path(run).absolute(); output=Path(output).absolute()
    if output==run or output.is_relative_to(run): raise ValueError("Preserve raw archive")
    absent(output)
    raw,manifest=load_archive(run,manifest_sha)
    read=lambda name:strict_json(raw[name].decode("utf-8"))
    plan=read("batch/plan.json"); backend=read("batch/backend.json"); summary=read("batch/summary.json")
    expected={f"{h}-p{p}" for h in HISTORIES for p in (1,8)}
    if (audit.digest(plan)!=plan_sha or len(plan["jobs"])!=12 or {j["id"] for j in plan["jobs"]}!=expected
            or len(summary["rows"])!=12 or {r["id"] for r in summary["rows"]}!=expected
            or backend.get("kind") not in ("scripted","native")):
        raise ValueError("Wrong fixed plan or outcomes")
    outcomes={r["id"]:r["status"] for r in summary["rows"]}; rows=[]
    wanted=re.compile(r"(?:trace/http-g\d{3}\.(?:pending|provider)|trace/g\d{3}\.response|tools/attempt-\d{3}\.(?:pending|completed)|tools/(?:started|summary))\.json")
    for job in plan["jobs"]:
        prefix=job["id"]+"/"
        records={name[len(prefix):]:strict_json(pinned_bytes(run/name,sha).decode("utf-8"))
            for name,sha in manifest.items() if name.startswith(prefix) and wanted.fullmatch(name[len(prefix):])}
        rows.append(dict(job_id=job["id"],outcome=outcomes[job["id"]],accounting=resource.summarize(records)))
    journal=Journal(output)
    result=dict(status="ALL_PLANNED_SLOTS_RESOURCE_LEDGER_NOT_TOTAL_COST",rows=rows,
        source_backend=backend["kind"],source_manifest_sha256=manifest_sha,plan_sha256=plan_sha,
        planned_slots=12,conditioned_on_success=False,batch_reported_wall_seconds=summary.get("wall_seconds"),
        batch_wall_excludes_preflight_and_idle_billing=True,total_financial_cost=None,
        total_resource_cost_complete=False,new_model_calls=0,new_sql_queries=0,network_requests=0)
    journal("resource-accounting",result)
    for label,path in (("script",Path(__file__)),("ledger",Path(resource.__file__))):
        data=path.read_bytes()
        journal(label+"-source",dict(file_sha256=hashlib.sha256(data).hexdigest(),source_utf8=data.decode("utf-8")))
    journal.seal()
    return {k:v for k,v in result.items() if k!="rows"}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("run","manifest-sha","plan-sha","output"): p.add_argument("--"+name,required=True)
    a=p.parse_args()
    print(audit.encoded(prepare(a.run,manifest_sha=a.manifest_sha,plan_sha=a.plan_sha,output=a.output)).decode())


if __name__=="__main__": main()
