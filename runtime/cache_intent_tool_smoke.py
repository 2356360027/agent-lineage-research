"""One bounded software wiring check on an already-exposed historical query.

No model or target-answer evaluation. Three sessions each attempt one cache read
and one execution of the SAME historical SQL. No automatic retry or resumption.
"""
import argparse
from pathlib import Path
import shutil

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_tools as tools
from runtime import cache_intent_tool_inputs as inputs
from scripts.fetch_bird_fixed import absent


def run(package_dir, bird_dir, output):
    output = Path(output)
    absent(output)
    bound = inputs.load_inputs(package_dir, bird_dir, "h200a")
    output.mkdir(parents=True, exist_ok=False)
    audit.save_new(output / "plan.json", {
        "purpose": "SOFTWARE_WIRING_CHECK_NOT_MODEL_EXPERIMENT",
        "history_id": "h200a", "max_new_readonly_sql_calls": 3,
        "sessions": [[False, 1], [True, 1], [True, 8]],
        "model_calls": 0, "semantic_approvals": 0, "input_receipt": bound["input_receipt"]})
    for source, name in ((Path(__file__), "smoke_source.py"),
                         (Path(tools.__file__), "tools_source.py"),
                         (Path(inputs.__file__), "inputs_source.py")):
        shutil.copyfile(source, output / name)
    results = []
    try:
        for index, (available, cost) in enumerate(((False, 1), (True, 1), (True, 8))):
            session = tools.ToolSession(**bound, output=output / f"session-{index}",
                                        materialized_available=available, cache_read_cost=cost)
            try:
                cached = session.call("read_cache", {"cache_id": "v000"})
                recomputed = session.call("run_sql", {"sql": bound["history_sql"]})
            finally:
                session_summary = session.close()
            expected = bound["cached_result"]
            recomputed_match = (recomputed.get("complete") is True and
                               recomputed.get("columns") == expected["columns"] and
                               audit.fingerprints(recomputed["rows"])["bag_sha256"] == expected["bag_sha256"])
            cache_check = (cached.get("complete") is True and cached.get("columns") == expected["columns"] and
                           cached.get("rows") == expected["rows"]) if available else (
                               cached.get("status") == "CACHE_UNAVAILABLE" and "rows" not in cached)
            charge_check = session_summary["spent_declared_units"] == tools.SQL_COST + (cost if available else 0)
            result = {"session": index, "available": available, "read_cost": cost,
                      "cache_permission_and_output_match": cache_check,
                      "same_historical_sql_matches_frozen_cache": recomputed_match,
                      "declared_charge_matches": charge_check, "session_summary": session_summary}
            results.append(result)
            audit.save_new(output / f"check-{index}.json", result)
            if not (cache_check and recomputed_match and charge_check and
                    session_summary["status"] == "TOOL_SESSION_CLOSED"):
                raise ValueError("Software wiring check failed; no replacement or retry")
        summary = {"status": "SOFTWARE_WIRING_CHECK_ONLY", "sessions": len(results),
                   "new_readonly_sql_calls": sum(r["session_summary"]["sql_calls"] for r in results),
                   "attempts": sum(r["session_summary"]["attempts"] for r in results),
                   "checks_passed": all(r["cache_permission_and_output_match"] and
                       r["same_historical_sql_matches_frozen_cache"] and r["declared_charge_matches"] for r in results),
                   "declared_units_by_session": [r["session_summary"]["spent_declared_units"] for r in results],
                   "model_calls": 0, "semantic_approvals": 0, "independent_research_samples": 0,
                   "inference_eligible": False,
                   "finalization_requires": "Full verified artifact manifest and no failure.json",
                   "warning": "Same previously exposed historical SQL; not new task validation, model evidence, timing benchmark or API cost measurement."}
        audit.save_new(output / "summary.json", summary)
        files = [p for p in output.rglob("*") if p.is_file()]
        audit.save_new(output / "artifact_hashes.json", {
            p.relative_to(output).as_posix(): audit.hash_file(p) for p in sorted(files)})
        return summary
    except BaseException as exc:
        try:
            audit.save_new(output / "failure.json", {"error_type": type(exc).__name__,
                "completed_session_checks": len(results), "automatic_retry": False})
        except BaseException:
            pass  # Never mask the original failure if the failure record also cannot be saved.
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package-dir", type=Path, default=Path("runtime/runs/cache-intent-dev-package-20261012-001"))
    p.add_argument("--bird-dir", type=Path, default=Path("runtime/runs/datasets/bird-mini-fixed-20261005-002"))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()
    result = run(a.package_dir, a.bird_dir, a.output) if a.execute else {
        "status": "PLAN_NO_EXECUTION", "max_new_readonly_sql_calls": 3,
        "sessions": 3, "model_calls": 0, "semantic_approvals": 0}
    print(audit.encoded(result).decode("utf-8"))


if __name__ == "__main__":
    main()
