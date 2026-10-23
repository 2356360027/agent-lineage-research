"""Private engineering audit: scripted generation, real read-only SQL, no model.

Candidate contracts and SQL are audit fixtures, NEVER model input for an
experimental method. Counting continues beyond hypothetical context limits to
diagnose feasibility; it does not certify an actual inference run.
"""
import argparse
import json
from pathlib import Path
import time
from unittest.mock import patch

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_journal as journal
from runtime import cache_intent_staged_loop as loop
from runtime import cache_intent_staged_preview as staged
from runtime import cache_intent_tools as tools
from scripts import measure_staged_context_offline as measurement
from scripts.fetch_bird_fixed import absent, no_link_ancestors, read_json

SCOPED_SQL_234 = ("SELECT COUNT(DISTINCT c.bond_id) FROM connected AS c "
    "JOIN bond AS b ON b.bond_id=c.bond_id WHERE b.molecule_id='TR009' "
    "AND (c.atom_id='TR009_12' OR c.atom_id2='TR009_12')")
DELAYS = (0, 19, 20)


def scripted_action(delay, attempts, sql, last_observation):
    if type(delay) is not int or delay not in DELAYS:
        raise ValueError("Only the fixed engineering routes are allowed")
    if attempts < delay:
        return {"kind":"read_cache", "cache_id":"v000"}
    if attempts == delay:
        return {"kind":"run_sql", "sql":sql}
    if last_observation.get("complete") is not True:
        return {"kind":"abstain", "text":"Scripted SQL did not return a complete result."}
    return {"kind":"answer", "text":json.dumps(last_observation["rows"], ensure_ascii=False)}


def audit_routes(args):
    output = Path(args.output); absent(output)
    receipt, previews = measurement.load_inputs(args.package, args.manifest_sha256,
                                                args.scope_input, args.scope_manifest_sha256)
    database = Path(args.database); no_link_ancestors(database)
    before = audit.hash_file(database)
    if before != receipt["database_sha256_from_receipt"] or any(
            Path(str(database)+s).exists() for s in ("-wal","-shm","-journal")):
        raise ValueError("Not the frozen database snapshot")
    count, tokenizer_receipt = measurement.archived_counter(args.archive_root, args.dependency_dir)
    cases = {c["history"]["id"]:c for c in read_json(Path(args.package)/"PRIVATE_cases.json")}
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    audit.save_new(output/"started.json", {"status":"LOCAL_SCRIPTED_AUDIT_NOT_MODEL",
        "planned_routes":36, "delays":list(DELAYS), "model_calls":0,
        "input_manifest_sha256":args.manifest_sha256,
        "scope_manifest_sha256":args.scope_manifest_sha256, "database_sha256":before,
        "scripted_correct_contract_is_a_fixture_not_a_method":True,
        "hypothetical_window":4096, "hypothetical_completion_reservation":512,
        "context_overflow_is_measured_not_enforced_in_this_script":True,
        "source_sha256":audit.hash_file(__file__), **tokenizer_receipt})
    source_files = {Path(__file__),Path(measurement.__file__),Path(audit.__file__),
                    Path(loop.__file__),Path(tools.__file__),Path(journal.__file__),Path(staged.__file__)}
    source_root = output/"sources"; source_root.mkdir()
    for source in source_files:
        with (source_root/source.name).open("xb") as stream:
            stream.write(source.read_bytes())
    rows = []
    with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No network in local audit")):
        for pair in previews:
            history_id = pair["history_id"]; case = cases[history_id]
            contract = dict(case["candidate_contract"])
            sql = case["target_record"]["SQL"]
            if case["target_id"] == 234:
                sql = SCOPED_SQL_234
                contract["filter"] += "; referenced bond belongs to molecule TR009"
            cache = read_json(Path(args.package)/(history_id+".result.json"))
            for envelope in pair["arms"]:
                price = staged.validate_preview(envelope)["context"]["cache"]["read_cost"]
                for delay in DELAYS:
                    route_id = f"{history_id}-p{price}-delay{delay:02d}"
                    root = output/route_id
                    session = tools.ToolSession(database_path=database, database_sha256=before,
                        history_sql=case["history"]["sql"], cached_result=cache,
                        input_receipt={"scripted_route_id":route_id,"model_calls":0},
                        output=root/"tools", materialized_available=True, cache_read_cost=price,
                        budget_profile=tools.NONBINDING_PROFILE)
                    log = journal.Journal(root/"trace")
                    lengths = []

                    def generate(request):
                        lengths.append({"generation_id":request["generation_id"],
                            "stage":request["stage"],"prompt_tokens":count(request["messages"])})
                        if request["stage"] == "contract":
                            content = contract
                        else:
                            attempts = session.describe()["attempts_used"]
                            last = (read_json(session.output/f"attempt-{attempts:03d}.completed.json")["response"]
                                    if attempts else {})
                            content = scripted_action(delay, attempts, sql, last)
                        return {"content":json.dumps(content,ensure_ascii=False),
                                "finish_reason":"stop", "backend":"SCRIPTED_NOT_MODEL", "usage":None}

                    try:
                        result = loop.run_trajectory(envelope, session, generate, log)
                    finally:
                        tool_summary = session.close()
                        journal.verify(session.output)
                        log.seal()
                    last_rows = None
                    if tool_summary["sql_calls"]:
                        last = read_json(session.output/f"attempt-{delay+1:03d}.completed.json")["response"]
                        if last.get("complete"):
                            last_rows = audit.fingerprints(last["rows"])["bag_sha256"]
                    rows.append({"route_id":route_id,"target_id":case["target_id"],
                        "history_id":history_id,"read_price":price,"cache_reads_before_sql":delay,
                        "scripted_loop_status":result["status"], "tool_summary":tool_summary,
                        "prompt_lengths":lengths, "sql_result_bag_sha256":last_rows,
                        "max_prompt_tokens":max(r["prompt_tokens"] for r in lengths),
                        "would_exceed_4096_with_512":[r["generation_id"] for r in lengths
                                                     if r["prompt_tokens"]+512>4096]})
                    audit.save_new(root/"route_audit.json",rows[-1])
    after = audit.hash_file(database)
    report = {"status":"LOCAL_SCRIPTED_ROUTE_AUDIT_NOT_MODEL_EXPERIMENT", "routes":rows,
        "planned_routes":36,"recorded_routes":len(rows),"model_calls":0,
        "actual_readonly_sql_calls":sum(r["tool_summary"]["sql_calls"] for r in rows),
        "database_sha256_before":before,"database_sha256_after":after,
        "database_unchanged":before==after,"wall_seconds":time.perf_counter()-started,
        "always_feasible_claim":"NOT_ESTABLISHED", "inference_authorized":False}
    audit.save_new(output/"report.json",report)
    audit.save_new(output/"artifact_hashes.json", {p.relative_to(output).as_posix():audit.hash_file(p)
        for p in sorted(output.rglob("*")) if p.is_file()})
    print(audit.encoded({k:v for k,v in report.items() if k!="routes"}).decode())
    if before != after:
        raise RuntimeError("Database changed; all routes invalid")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("package","manifest-sha256","scope-input","scope-manifest-sha256",
                 "database","archive-root","dependency-dir","output"):
        parser.add_argument("--"+name,required=True)
    audit_routes(parser.parse_args())
