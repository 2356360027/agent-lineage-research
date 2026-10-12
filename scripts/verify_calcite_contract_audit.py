"""Read-only independent verifier of Calcite audit v1/v2/v3 artifacts.

Reads files and prints JSON only. Does not import the runtime evaluator, execute
SQL/Java, repair records, or write files. Current source drift is reported
separately and does not invalidate a matching historical source snapshot.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[1]


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      object_pairs_hook=reject_duplicates, parse_float=Decimal,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_decimal_text(value):
    """Keep exact decimal evidence in CLI JSON without converting it to float."""
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)
    raise TypeError("Unsupported JSON output type: " + type(value).__name__)


def within(directory, relative):
    part = Path(relative)
    if part.is_absolute() or part.drive or ".." in part.parts:
        raise ValueError("Unsafe artifact path: " + str(relative))
    path = (directory / part).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("Artifact escapes requested directory")
    return path


def file_check(path, expected_sha, expected_bytes=None):
    path = Path(path)
    answer = {"path": str(path), "expected_sha256": expected_sha, "exists": path.is_file()}
    if not answer["exists"]:
        answer["matches"] = False
        return answer
    answer.update(actual_sha256=sha(path), actual_bytes=path.stat().st_size)
    answer["matches"] = answer["actual_sha256"] == expected_sha
    if expected_bytes is not None:
        answer["expected_bytes"] = expected_bytes
        answer["matches"] = answer["matches"] and answer["actual_bytes"] == expected_bytes
    return answer


def canonical(value):
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, Decimal, Fraction)):
        rational = Fraction(value)
        return ("number", rational.numerator, rational.denominator)
    if isinstance(value, str):
        return ("text", value)
    raise ValueError("Unexpected cell value type")


def bag(rows):
    if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
        raise ValueError("Expected list of result rows")
    return Counter(tuple(canonical(value) for value in row) for row in rows)


def decode(rows):
    if not isinstance(rows, list):
        raise ValueError("Missing/malformed encoded rows")
    result, typed = [], []
    for row in rows:
        if not isinstance(row, list):
            raise ValueError("Malformed encoded row")
        values, tagged = [], []
        for cell in row:
            if not isinstance(cell, dict) or "value" not in cell or "javaType" not in cell:
                raise ValueError("Incomplete typed cell")
            values.append(cell["value"])
            tagged.append((cell["javaType"], canonical(cell["value"])))
        result.append(values)
        typed.append(tuple(tagged))
    return result, Counter(typed)


def groups(rows, indices):
    result = defaultdict(list)
    for row in rows:
        result[tuple(row[i] for i in indices)].append(row)
    return result


def nonnull_sum(values):
    retained = [value for value in values if value is not None]
    return sum(retained) if retained else None


def sufficient(rows):
    values = [row[2] for row in rows if row[2] is not None]
    return [len(rows), len(values), sum(values) if values else None]


def row_oracle(case_id, rows):
    """Independent explicit finite-contract logic, not an SQL interpreter."""
    department = groups(rows, [1])
    sums = [[key[0], nonnull_sum([row[2] for row in group])]
            for key, group in department.items()]
    statistics = [[key[0], *sufficient(group)] for key, group in department.items()]
    if case_id == "C1":
        return [[r[2]] for r in rows if r[1] == 20], [[r[1], r[2]] for r in rows]
    if case_id == "C2":
        return ([[key[0], len(group)] for key, group in department.items()],
                [[key[0], sum(r[2] is not None for r in group)] for key, group in department.items()])
    if case_id == "C3":
        return [[key[0], nonnull_sum([r[3] for r in group])] for key, group in department.items()], sums
    if case_id == "C4":
        return [[*key, nonnull_sum([r[2] for r in group])]
                for key, group in groups(rows, [1, 0]).items()], sums
    if case_id == "C5":
        return [sufficient(rows)], statistics
    if case_id == "C6":
        values = [r[2] for r in rows if r[1] in (10, 20) and r[2] is not None]
        return [[Fraction(sum(values), len(values)) if values else None]], statistics
    if case_id == "C7":
        return ([[len({r[2] for r in rows if r[2] is not None})]],
                [[key[0], len({r[2] for r in group if r[2] is not None})]
                 for key, group in department.items()])
    if case_id == "C8":
        return [r[:3] for r in rows if r[1] in (10, 20)], [r[:3] for r in rows if r[1] == 10]
    raise ValueError("Unsupported authored contract ID")


def schema_names(report, field):
    schema = report.get(field)
    if not isinstance(schema, list):
        return None
    if any(not isinstance(column, dict) or "name" not in column for column in schema):
        return None
    return [column["name"] for column in schema]


def assess_raw(report, case, state, fixture, process, mode=None):
    result = {"case": case["id"], "state": state,
              "reported_status": report.get("status"),
              "process_success": process.get("status") == "EXITED" and process.get("returncode") == 0,
              "identity_matches": report.get("caseId") == case["id"] and report.get("state") == state}
    result["mode"] = mode or "hep"
    result["planner_mode_matches"] = mode is None or report.get("plannerMode") == mode
    result["planner_evidence_valid"] = cost_evidence_valid(report, mode)
    base, _ = decode(report["baseRows"])
    result["fixture_matches"] = bag(base) == bag(fixture["rows"])
    data, strict = {}, {}
    for field, expected in [("originalRows", "expected_query_rows"),
                            ("rewrittenRows", "expected_query_rows"),
                            ("materializedViewRows", "expected_view_rows")]:
        data[field], strict[field] = decode(report[field])
        result[field + "_oracle_equal"] = bag(data[field]) == bag(case[expected][state])
    result["all_values_equal_contract"] = all(result[field + "_oracle_equal"] for field in data)
    query_names = [column["name"] for column in case["query_columns"]]
    names = [("querySchema", query_names), ("rewrittenSchema", query_names),
             ("viewSchema", case["view_columns"])]
    result["column_contracts"] = {field: {"actual": schema_names(report, field), "expected": expected,
                                         "matches": schema_names(report, field) == expected}
                                  for field, expected in names}
    result["all_named_columns_equal_contract"] = all(item["matches"] for item in result["column_contracts"].values())
    result["original_rewritten_numeric_bag_equal"] = bag(data["originalRows"]) == bag(data["rewrittenRows"])
    result["original_rewritten_typed_bag_equal"] = strict["originalRows"] == strict["rewrittenRows"]
    type_keys = ("sqlType", "nullable", "fullType")
    valid_types = all(isinstance(report.get(key), list) and
                      all(all(item in column for item in type_keys) for column in report[key])
                      for key in ("querySchema", "rewrittenSchema"))
    result["sql_types_equal_ignoring_names"] = valid_types and (
        [tuple(c[k] for k in type_keys) for c in report["querySchema"]] ==
        [tuple(c[k] for k in type_keys) for c in report["rewrittenSchema"]])
    result["strict_named_contract_passed"] = all(result[k] for k in (
        "process_success", "identity_matches", "planner_mode_matches", "planner_evidence_valid", "fixture_matches", "all_values_equal_contract",
        "all_named_columns_equal_contract", "original_rewritten_typed_bag_equal",
        "sql_types_equal_ignoring_names"))
    comparisons = report.get("comparison", {})
    result["reported_comparison_matches_recalculation"] = all([
        comparisons.get("numericNormalizedBagEqual") == result["original_rewritten_numeric_bag_equal"],
        comparisons.get("typedBagEqual") == result["original_rewritten_typed_bag_equal"],
        comparisons.get("sqlRowTypesEqualIgnoringNames") == result["sql_types_equal_ignoring_names"],
        comparisons.get("originalRowCount") == len(data["originalRows"]),
        comparisons.get("rewrittenRowCount") == len(data["rewrittenRows"])])
    counters = report.get("rewrittenExecutionCounters", {})
    calls = {}
    for table in ("emps", "MV"):
        value = counters.get(table, {}).get("scanCalls")
        if type(value) is not int or value < 0:
            raise ValueError("Missing/invalid actual scan-call counter")
        calls[table] = value
    result["actual_scan_calls"] = calls
    result["actual_reuse_structure"] = (
        "MV_PLUS_BASE" if calls["MV"] and calls["emps"] else
        "MV_ONLY" if calls["MV"] else "BASE_ONLY" if calls["emps"] else "NO_SCAN")
    classification = report.get("classification", {})
    result["reported_scan_classification_matches_counters"] = (
        classification.get("usesMaterializedViewAtExecution") is bool(calls["MV"]) and
        classification.get("readsBaseAtExecution") is bool(calls["emps"]))
    result["planned_scan_tables"] = report.get("rewrittenScanTables")
    result["cost_planning"] = report.get("costPlanning")
    result["scan_interpretation"] = "Recorded table scan calls, not storage I/O, latency or global cost optimality."
    return result


def cost_evidence_valid(report, mode):
    if mode is None:
        return True  # Historical HEP reports predate explicit mode identity.
    if report.get("plannerMode") != mode:
        return False
    if mode == "hep":
        return True
    c = report.get("costPlanning", {})
    if not isinstance(c.get("selectedPlan"), str) or not c["selectedPlan"]:
        return False
    if c["selectedPlan"] != report.get("selectedPlanExecutionRel"):
        return False
    fields = {"status": "PLANNED", "planner": "org.apache.calcite.plan.volcano.VolcanoPlanner",
              "program": "Programs.standard()", "selectedConvention": "ENUMERABLE"}
    if any(c.get(key) != value for key, value in fields.items()):
        return False
    if type(c.get("registeredMaterializationCount")) is not int or c["registeredMaterializationCount"] != int(mode == "volcano_mv"):
        return False
    if any(c.get(key) is not True for key in ("materializationsEnabled", "freshClusterAndUnregisteredTreeVerified", "metadataResetBeforeStandardProgram")):
        return False
    for field in ("selectedCost", "finalProgramCost"):
        value = c.get(field, {})
        if value.get("status") != "AVAILABLE" or value.get("infinite") is not False:
            return False
        for component in ("rows", "cpu", "io"):
            number = value.get(component)
            if type(number) not in (int, Decimal) or not Decimal(number).is_finite() or number < 0:
                return False
    return True


def required_manifest_entries(design, schedule):
    names = {"design.json", "summary.json", "build.json", "compiled_classes.json",
             "oracle_preflight.json", "java_version.json"}
    names.update(str(Path("source") / name) for name in design["source_hashes"])
    names.update(row["label"] + suffix for row in schedule for suffix in (".json", ".process.json"))
    return {str(Path(name)) for name in names}


def recorded_schedule(design, config):
    modes = design.get("planner_modes", ["hep"])
    if not modes or len(modes) != len(set(modes)) or any(m not in ("hep", "volcano_base", "volcano_mv") for m in modes):
        raise ValueError("Unexpected planner modes")
    expected = {(c["id"], s, m) for c in config["cases"] for s in config["fixtures"] for m in modes}
    schedule = design.get("schedule")
    if schedule is None:
        if design["version"] == "calcite-contract-audit-v3-cost-controls":
            raise ValueError("New audit version must save its execution schedule")
        schedule = [{"case": c["id"], "state": s, "mode": "hep", "label": c["id"] + "-" + s}
                    for c in config["cases"] for s in config["fixtures"]]
    actual = [(r["case"], r["state"], r["mode"]) for r in schedule]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("Missing/duplicate/extra case-state-mode combinations")
    for row in schedule:
        wanted = row["case"] + "-" + row["state"] + ("" if row["mode"] == "hep" else "-" + row["mode"])
        if row["label"] != wanted:
            raise ValueError("Unexpected output label")
    return schedule


def verify(run):
    run = Path(run).resolve()
    manifest = load(run / "manifest.json")
    design = load(run / "design.json")
    summary = load(run / "summary.json")
    config = load(run / "source/configs/calcite_contracts_v1.json")
    if design.get("version") not in ("calcite-hep-contract-audit-v1", "calcite-hep-contract-audit-v2", "calcite-contract-audit-v3-cost-controls"):
        raise ValueError("Unsupported audit version")
    if [case["id"] for case in config["cases"]] != [f"C{i}" for i in range(1, 9)]:
        raise ValueError("Expected C1-C8 exactly")
    if list(config["fixtures"]) != ["base", "empty", "all_salary_null"]:
        raise ValueError("Expected the fixed three states")
    if design["cases"] != [case["id"] for case in config["cases"]] or design["states"] != list(config["fixtures"]):
        raise ValueError("Design/config schedule mismatch")
    schedule = recorded_schedule(design, config)
    missing_manifest_entries = sorted(required_manifest_entries(design, schedule) -
                                      {str(Path(name)) for name in manifest})
    manifest_checks = [file_check(within(run, name), item["sha256"], item["bytes"])
                       for name, item in manifest.items()]
    frozen, current = {}, {}
    for relative, h in design["source_hashes"].items():
        frozen[relative] = file_check(within(run / "source", relative), h)
        current[relative] = file_check(within(REPOSITORY, relative), h)
    dependencies = [file_check(Path(item["path"]), item["sha256"], item["bytes"])
                    for item in design["dependencies"]]
    binaries = [file_check(Path(path), h) for path, h in design["java_binaries"].items()]
    classpath = file_check(Path(design["classpath_file"]), design["classpath_sha256"])
    artifact_design_hash = sha(run / "design.json") == summary["design_sha256"]
    integrity = all(item["matches"] for item in manifest_checks + list(frozen.values()) +
                    dependencies + binaries + [classpath]) and artifact_design_hash and not missing_manifest_entries
    oracle_checks = []
    records, errors = [], []
    for case in config["cases"]:
        for state, fixture in config["fixtures"].items():
            q, v = row_oracle(case["id"], fixture["rows"])
            oracle_checks.extend([
                {"case": case["id"], "state": state, "kind": "query",
                 "matches": bag(q) == bag(case["expected_query_rows"][state])},
                {"case": case["id"], "state": state, "kind": "view",
                 "matches": bag(v) == bag(case["expected_view_rows"][state])}])
    cases = {case["id"]: case for case in config["cases"]}
    for task in schedule:
        case, state, mode = cases[task["case"]], task["state"], task["mode"]
        fixture = config["fixtures"][state]
        try:
            report = load(run / (task["label"] + ".json"))
            process = load(run / (task["label"] + ".process.json"))
            records.append(assess_raw(report, case, state, fixture, process,
                                     mode if "planner_modes" in design else None))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({"case": case["id"], "state": state, "mode": mode,
                           "error_type": type(exc).__name__, "error": str(exc)})
    expected_ok = all(item["matches"] for item in oracle_checks)
    independent = {
        "raw_reports_verified": len(records), "raw_report_errors": len(errors),
        "process_success": sum(r["process_success"] for r in records),
        "identity_matches": sum(r["identity_matches"] for r in records),
        "planner_evidence_valid": sum(r["planner_evidence_valid"] for r in records),
        "fixture_matches": sum(r["fixture_matches"] for r in records),
        "original_query_value_passes": sum(r["originalRows_oracle_equal"] for r in records),
        "rewritten_query_value_passes": sum(r["rewrittenRows_oracle_equal"] for r in records),
        "materialized_view_value_passes": sum(r["materializedViewRows_oracle_equal"] for r in records),
        "all_values_passes": sum(r["all_values_equal_contract"] for r in records),
        "named_column_passes": sum(r["all_named_columns_equal_contract"] for r in records),
        "strict_named_contract_passes": sum(r["strict_named_contract_passed"] for r in records),
        "raw_status_counts": dict(Counter(r["reported_status"] for r in records)),
        "actual_reuse_counts": dict(Counter(r["actual_reuse_structure"] for r in records)),
        "per_case_actual_reuse_counts": {
            case["id"]: dict(Counter(r["actual_reuse_structure"] for r in records if r["case"] == case["id"]))
            for case in config["cases"]},
        "per_mode_actual_reuse_counts": {
            mode: dict(Counter(r["actual_reuse_structure"] for r in records if r["mode"] == mode))
            for mode in design.get("planner_modes", ["hep"])},
        "comparisons_match_records": all(r["reported_comparison_matches_recalculation"] for r in records),
        "scan_classifications_match_counters": all(r["reported_scan_classification_matches_counters"] for r in records)}
    test_path = run / "source/tests/test_calcite_contract_audit.py"
    tests = [node.name for node in ast.walk(ast.parse(test_path.read_text(encoding="utf-8-sig")))
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")]
    historical_contract_count = sum(r.get("contract_passed") is True for r in summary.get("records", []))
    all_files = {str(p.relative_to(run)) for p in run.rglob("*") if p.is_file()}
    listed_files = {str(Path(name)) for name in manifest} | {"manifest.json"}
    status = ("INTEGRITY_CHECK_FAILED" if not integrity else
              "INDEPENDENT_ORACLE_DISAGREES_WITH_CONFIG" if not expected_ok else
              "RAW_AUDIT_INCOMPLETE" if errors or len(records) != len(schedule) else
              "VERIFIED_WITH_CONTRACT_FAILURES" if independent["strict_named_contract_passes"] != len(schedule) else
              "VERIFIED_ALL_FINITE_CONTRACTS")
    return {
        "verifier": "independent-calcite-contract-verifier-v3-plan-and-manifest", "run": str(run),
        "expected_combinations": len(schedule),
        "verifier_sha256": sha(Path(__file__)), "status": status,
        "json_decimal_encoding": "Exact Decimal values in CLI output are strings; original evidence is not rewritten.",
        "read_only": True, "sql_java_model_executions": 0,
        "manifest_sha256": sha(run / "manifest.json"), "manifest_files_checked": len(manifest_checks),
        "design_sha256": sha(run / "design.json"), "summary_sha256": sha(run / "summary.json"),
        "integrity_passed": integrity, "manifest_checks": manifest_checks,
        "missing_required_manifest_entries": missing_manifest_entries,
        "frozen_source_checks": frozen, "dependency_checks": dependencies,
        "java_binary_checks": binaries, "classpath_check": classpath,
        "summary_design_hash_matches": artifact_design_hash,
        "current_working_source_checks": current,
        "current_source_scope": "Differences from a historical frozen snapshot are not evidence of mutation during that historical run; not included in integrity_passed.",
        "unmanifested_files": sorted(all_files - listed_files),
        "unmanifested_scope": "Later additions are listed, not treated as original manifest-authenticated artifacts.",
        "frozen_test_count": len(tests), "frozen_test_sha256": sha(test_path),
        "missing_fixture_regression_present": "test_absent_fixture_is_not_proof_of_empty_fixture" in tests,
        "independent_row_oracle_checks": oracle_checks,
        "counts": independent, "records": records, "errors": errors,
        "historical_summary_contract_passes": historical_contract_count,
        "summary_comparison_scope": "The current verifier applies named-column checks to v1 and v2. Historical summaries are never rewritten; v1's contract count did not include these column checks.",
        "limitations": ["Finite authored fixtures; no general equivalence or method-novelty proof.",
                        "No re-execution; raw reported scan counters are cross-checked, not independently measured.",
                        "No cost-planner completeness or performance inference.",
                        "INTEGER-valued fixtures do not test nonintegral AVG, DECIMAL precision or overflow."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(args.run)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
        result = {"status": "VERIFICATION_ERROR", "read_only": True,
                  "sql_java_model_executions": 0, "error_type": type(exc).__name__, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False, default=json_decimal_text))
    # Observed, preserved contract failures are a valid verification outcome.
    if result["status"] not in ("VERIFIED_WITH_CONTRACT_FAILURES", "VERIFIED_ALL_FINITE_CONTRACTS"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
