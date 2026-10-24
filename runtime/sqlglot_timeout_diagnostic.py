"""One predeclared post-hoc diagnostic; never replaces the source batch.

Three plans and one execution of each: not a causal speed benchmark.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from . import bird_readonly_audit as ro
from . import sqlglot_repro_audit as audit


def build(wheel, sql, schema):
    sg = audit.load_upstream(wheel)
    from sqlglot.optimizer import optimize
    from sqlglot.optimizer.optimizer import RULES
    rules = [r for r in RULES if r.__name__ != "unnest_subqueries"]
    assert len(rules) == len(RULES) - 1
    tree = sg.parse_one(sql, read="sqlite", error_level=sg.ErrorLevel.RAISE)
    result = optimize(tree, schema=schema, dialect="sqlite", rules=rules)
    return {"sql": result.sql(dialect="sqlite", unsupported_level=sg.ErrorLevel.RAISE),
            "rules": [r.__name__ for r in rules]}


def run(source, output):
    source, output = Path(source), Path(output)
    receipt = json.loads((source / "artifact_hashes.json").read_text(encoding="utf-8"))
    if any(ro.hash_file(source / n) != h for n, h in receipt.items()):
        raise ValueError("Source batch artifact drift")
    source_design = json.loads((source / "design.json").read_text(encoding="utf-8"))
    for module in (audit, ro):
        if ro.hash_file(module.__file__) != source_design["source_hashes"][Path(module.__file__).name]:
            raise ValueError("Shared source changed")
    question = json.loads((source / "bird_1481.json").read_text(encoding="utf-8"))
    schema = json.loads((source / "schemas.json").read_text(encoding="utf-8"))[question["db_id"]]["schema"]
    wheel = source_design["wheel_path"]
    database = Path(source_design["data_path"]) / "databases" / question["db_id"] / (question["db_id"] + ".sqlite")
    before = ro.hash_file(database)
    if before != source_design["database_hashes"][question["db_id"]]:
        raise ValueError("Database changed")
    output.mkdir(parents=True, exist_ok=False)
    ro.save_new(output / "design.json", {
        "status": "POST_HOC_DIAGNOSTIC_FROZEN_BEFORE_EXECUTION", "question_id": 1481,
        "trigger": "default optimizer timed out in fixed source batch",
        "source": str(source.resolve()), "source_summary_sha256": ro.hash_file(source / "summary.json"),
        "source_artifact_manifest_sha256": ro.hash_file(source / "artifact_hashes.json"),
        "source_sha256": ro.hash_file(__file__), "database_sha256": before,
        "wheel_sha256": audit.WHEEL_SHA,
        "candidate_order": ["original", "default_optimize", "omit_unnest_subqueries"],
        "limits": ro.LIMITS, "max_query_executions": 6, "repetitions": 1,
        "retry": "none", "inference": "diagnostic only; no causal timing estimate or broad rule recommendation"})
    with (output / "frozen_runner.py").open("xb") as f:
        f.write(Path(__file__).read_bytes())
    started = time.time()
    original = question["transform"]["original"]["sql"]
    candidates = {"original": {"sql": original},
                  "default_optimize": {"sql": question["transform"]["optimize"]["sql"]}}
    packet = {"wheel": wheel, "sql": original, "schema": schema}
    try:
        proc = subprocess.run([sys.executable, "-m", "runtime.sqlglot_timeout_diagnostic", "build"],
                              input=ro.encoded(packet), capture_output=True, timeout=15,
                              cwd=Path(__file__).resolve().parents[1])
        if proc.returncode:
            raise ValueError(proc.stderr.decode(errors="replace")[:4000])
        candidates["omit_unnest_subqueries"] = json.loads(proc.stdout)
    except (ValueError, subprocess.TimeoutExpired) as exc:
        candidates["omit_unnest_subqueries"] = {"status": "TRANSFORM_FAILED", "error": str(exc)}
    ro.save_new(output / "candidates.json", candidates)
    results = {}
    for name, candidate in candidates.items():
        if "sql" in candidate:
            plan = ro.run_child(database, "EXPLAIN QUERY PLAN " + candidate["sql"])
            execution = ro.run_child(database, candidate["sql"])
            results[name] = {"plan": plan, "execution": execution}
        else:
            results[name] = {"execution": {"status": "NOT_EXECUTED_TRANSFORM_FAILED", "complete": False}}
        ro.save_new(output / (name + ".json"), results[name])
        print(name + ": " + results[name]["execution"]["status"], flush=True)
    after = ro.hash_file(database)
    summary = {"status": "POST_HOC_DIAGNOSTIC_ONLY" if before == after else "INTEGRITY_FAILURE",
               "elapsed_seconds": time.time() - started, "database_unchanged": before == after,
               "model_calls": 0, "source_batch_unmodified": all(ro.hash_file(source / n) == h for n, h in receipt.items()),
               "results": {name: {"status": r["execution"]["status"],
                   "elapsed_seconds": r["execution"].get("elapsed_seconds"),
                   "comparison": audit.compare(results["original"]["execution"], r["execution"])} for name, r in results.items()},
               "warning": "Fixed order, one pass, observed-case selection: do not report a speedup benchmark, semantic proof, or new method."}
    if not summary["source_batch_unmodified"]:
        summary["status"] = "INTEGRITY_FAILURE"
    ro.save_new(output / "summary.json", summary)
    ro.save_new(output / "artifact_hashes.json", {p.name: ro.hash_file(p) for p in output.iterdir() if p.is_file()})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    execute = sub.add_parser("run")
    execute.add_argument("--source", required=True, type=Path)
    execute.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "build":
        sys.stdout.buffer.write(ro.encoded(build(**json.loads(sys.stdin.buffer.read()))))
    else:
        summary = run(args.source, args.output)
        print(json.dumps(summary, indent=2))
        if summary["status"] == "INTEGRITY_FAILURE":
            raise SystemExit(2)
