"""Freeze and execute a bounded, local upstream Calcite contract audit.

This audits configured baseline rule/cost planning on authored fixtures, not
new-method efficacy, optimizer completeness, or an external reproduction.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
JAVA_SOURCE = "runtime/calcite_repro/src/main/java/research/CalciteContractAudit.java"
SOURCES = [JAVA_SOURCE, "runtime/calcite_contract_audit.py",
           "runtime/calcite_repro/pom.xml", "runtime/calcite_repro/isolated-settings.xml",
           "scripts/fetch_maven_repro.py", "configs/calcite_contracts_v1.json",
           "tests/test_calcite_contract_audit.py", "scripts/verify_calcite_contract_audit.py"]
PLANNER_MODES = ("hep", "volcano_base", "volcano_mv")


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def save(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def canonical(value):
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float, Decimal)):
        return ("number", Decimal(str(value)))
    return (type(value).__name__, str(value))


def bag(rows):
    return Counter(tuple(canonical(v) for v in row) for row in rows)


def decoded(rows):
    return [[cell["value"] for cell in row] for row in rows]


def invoke(command, cwd, timeout, record):
    start = time.perf_counter()
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, timeout=timeout,
                              shell=False)
        result = {"status": "EXITED", "returncode": proc.returncode,
                  "stdout": proc.stdout.decode("utf-8", errors="replace"),
                  "stderr": proc.stderr.decode("utf-8", errors="replace")}
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills and waits for this dedicated Java child, not other JVMs.
        result = {"status": "TIMEOUT", "returncode": None,
                  "stdout": (exc.stdout or b"").decode("utf-8", errors="replace"),
                  "stderr": (exc.stderr or b"").decode("utf-8", errors="replace")}
    result.update(command=command, timeout_seconds=timeout,
                  wall_seconds=time.perf_counter() - start)
    save(record, result)
    return result


def check_oracle(config):
    """Independently execute explanatory contracts on in-memory SQLite fixtures.

    Only this reviewed, local configuration is accepted. Not a generic SQL sandbox.
    It deliberately does not establish cross-dialect type equivalence.
    """
    if [c["id"] for c in config["cases"]] != [f"C{i}" for i in range(1, 9)]:
        raise ValueError("Expected fixed C1-C8 contract list")
    if list(config["fixtures"]) != ["base", "empty", "all_salary_null"]:
        raise ValueError("Expected fixed three fixture states")
    checks = []
    for state, fixture in config["fixtures"].items():
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE emps(empid INTEGER NOT NULL, deptno INTEGER, salary INTEGER, commission INTEGER)")
            db.executemany("INSERT INTO emps VALUES(?,?,?,?)", fixture["rows"])
            for case in config["cases"]:
                for field, sql_field in [("query_rows", "query_sql"), ("view_rows", "view_sql")]:
                    actual = db.execute(case[sql_field]).fetchall()
                    wanted = case["expected_" + field][state]
                    equal = bag(actual) == bag(wanted)
                    checks.append({"case": case["id"], "state": state, "kind": field,
                                   "equal": equal, "rows": actual})
                    if not equal:
                        raise ValueError(f"Oracle inconsistency: {case['id']} {state} {field}")
    return checks


def assess(report, case, state, fixture):
    result = {"case": case["id"], "state": state, "reported_status": report.get("status")}
    result["identity_matches"] = report.get("caseId") == case["id"] and report.get("state") == state
    result["fixture_matches"] = "baseRows" in report and bag(decoded(report["baseRows"])) == bag(fixture["rows"])
    for key, expected_key in [("originalRows", "query_rows"),
                              ("rewrittenRows", "query_rows"),
                              ("materializedViewRows", "view_rows")]:
        result[key + "_oracle_equal"] = key in report and bag(decoded(report[key])) == bag(case["expected_" + expected_key][state])
    result["all_oracles_equal"] = all(result[k] for k in result if k.endswith("_oracle_equal"))
    result["comparison"] = report.get("comparison")
    result["classification"] = report.get("classification")
    result["scan_tables"] = report.get("rewrittenScanTables")
    result["produced_mv_candidate"] = any("MV" in e.get("scanTables", []) for e in report.get("ruleProductions", []))
    result["error"] = report.get("error")
    expected_names = [column["name"] for column in case["query_columns"]]
    result["query_columns_match_contract"] = [c["name"] for c in report.get("querySchema", [])] == expected_names
    result["rewritten_columns_match_contract"] = [c["name"] for c in report.get("rewrittenSchema", [])] == expected_names
    result["view_columns_match_contract"] = [c["name"] for c in report.get("viewSchema", [])] == case["view_columns"]
    result["all_column_contracts_equal"] = all(result[k] for k in ["query_columns_match_contract", "rewritten_columns_match_contract", "view_columns_match_contract"])
    result["contract_passed"] = (result["identity_matches"] and result["fixture_matches"]
                                 and result["all_oracles_equal"]
                                 and result["all_column_contracts_equal"]
                                 and report.get("comparison", {}).get("typedBagEqual") is True
                                 and report.get("comparison", {}).get("sqlRowTypesEqualIgnoringNames") is True)
    return result


def tasks(config, modes):
    if not modes or len(set(modes)) != len(modes) or any(m not in PLANNER_MODES for m in modes):
        raise ValueError("Choose unique supported planner modes")
    result = []
    for case in config["cases"]:
        for state in config["fixtures"]:
            # Balanced fixed ordering; timings remain diagnostic, not a benchmark.
            ordered = list(modes) if (len(result) // len(modes)) % 2 == 0 else list(reversed(modes))
            for mode in ordered:
                label = case["id"] + "-" + state + ("" if mode == "hep" else "-" + mode)
                result.append({"case": case["id"], "state": state, "mode": mode, "label": label})
    return result


def planner_evidence(report, mode):
    if report.get("plannerMode") != mode:
        return False
    if mode == "hep":
        return True
    cost = report.get("costPlanning", {})
    valid = (cost.get("status") == "PLANNED"
             and cost.get("planner") == "org.apache.calcite.plan.volcano.VolcanoPlanner"
             and cost.get("program") == "Programs.standard()"
             and type(cost.get("registeredMaterializationCount")) is int
             and cost["registeredMaterializationCount"] == (1 if mode == "volcano_mv" else 0)
             and cost.get("materializationsEnabled") is True
             and cost.get("freshClusterAndUnregisteredTreeVerified") is True
             and cost.get("metadataResetBeforeStandardProgram") is True
             and cost.get("selectedConvention") == "ENUMERABLE")
    for field in ("selectedCost", "finalProgramCost"):
        value = cost.get(field, {})
        valid = valid and value.get("status") == "AVAILABLE" and value.get("infinite") is False
        valid = valid and all(type(value.get(k)) in (int, float)
                              and math.isfinite(value[k]) and value[k] >= 0
                              for k in ("rows", "cpu", "io"))
    return bool(valid)


def run(output, java_home, classpath_file, modes=("hep",)):
    output, java_home, classpath_file = map(lambda p: Path(p).resolve(), (output, java_home, classpath_file))
    if not output.is_relative_to(ROOT / "runtime" / "runs"):
        raise ValueError("Output must be a fresh directory inside runtime/runs")
    output.mkdir(parents=True, exist_ok=False)
    source_hashes = {}
    for relative in SOURCES:
        source = ROOT / relative
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        source_hashes[relative] = digest(target)
    config = json.loads((output / "source/configs/calcite_contracts_v1.json").read_text(encoding="utf-8"))
    schedule = tasks(config, modes)
    timeout = 60 if any(mode != "hep" for mode in modes) else 40
    checks = check_oracle(config)
    save(output / "oracle_preflight.json", {"engine": sqlite3.sqlite_version, "checks": checks,
                                           "scope": "Numeric-valued bag check only, not SQL type equivalence"})
    classpath = classpath_file.read_text(encoding="utf-8").strip()
    jars = [Path(p).resolve() for p in classpath.split(os.pathsep)]
    if not jars or any(not p.is_file() or p.suffix != ".jar" for p in jars):
        raise ValueError("Invalid pinned dependency classpath")
    deps = [{"path": str(p), "bytes": p.stat().st_size, "sha256": digest(p)} for p in jars]
    java = java_home / "bin" / ("java.exe" if os.name == "nt" else "java")
    javac = java_home / "bin" / ("javac.exe" if os.name == "nt" else "javac")
    binaries = {str(p): digest(p) for p in [java, javac]}
    version = invoke([str(java), "-version"], ROOT, 15, output / "java_version.json")
    design = {"version": "calcite-contract-audit-v3-cost-controls", "started_at": datetime.now(timezone.utc).isoformat(),
              "source_hashes": source_hashes, "dependencies": deps, "java_binaries": binaries,
              "classpath_file": str(classpath_file), "classpath_sha256": digest(classpath_file),
              "java_version": version["stderr"], "cases": [c["id"] for c in config["cases"]],
              "states": list(config["fixtures"]), "per_case_timeout_seconds": timeout,
              "planner_modes": list(modes), "schedule": schedule,
              "heap_limit": "768m", "model_calls": 0, "paid_calls": 0,
              "baseline_scope": "Configured HEP and/or standard Volcano with/without registered MV; not optimizer completeness or novel method efficacy",
              "no_adaptation": "Fixed case/state/mode schedule, no silent retries, no result-based exclusions",
              "jdk_caution": "Local JBR25 adaptation; not claiming upstream-supported JDK25 validation"}
    save(output / "design.json", design)
    classes = output / "classes"
    classes.mkdir()
    build = invoke([str(javac), "--release", "17", "-encoding", "UTF-8", "-cp", classpath,
                    "-d", str(classes), str(output / "source" / JAVA_SOURCE)],
                   ROOT, 60, output / "build.json")
    if build.get("returncode") != 0:
        raise RuntimeError("Compilation failed; preserved design/build record, no cases started")
    class_hashes = {str(p.relative_to(classes)): digest(p) for p in classes.rglob("*.class")}
    save(output / "compiled_classes.json", class_hashes)
    records = []
    cases = {case["id"]: case for case in config["cases"]}
    for task in schedule:
        case, state, mode, label = cases[task["case"]], task["state"], task["mode"], task["label"]
        fixture = config["fixtures"][state]
        result_path = output / (label + ".json")
        child = invoke([str(java), "-Xmx768m", "-cp", str(classes) + os.pathsep + classpath,
                        "research.CalciteContractAudit", "--case", case["id"],
                        "--state", state, "--planner-mode", mode, "--output", str(result_path)],
                       ROOT, timeout, output / (label + ".process.json"))
        entry = {"case": case["id"], "state": state, "mode": mode, "process_status": child["status"],
                 "returncode": child["returncode"]}
        if result_path.exists():
            report = json.loads(result_path.read_text(encoding="utf-8"))
            entry.update(assess(report, case, state, fixture))
            entry["planner_mode_matches"] = report.get("plannerMode") == mode
            entry["planner_evidence_valid"] = planner_evidence(report, mode)
            entry["contract_passed"] = entry["contract_passed"] and entry["planner_evidence_valid"]
            entry["cost_planning"] = report.get("costPlanning")
        else:
            entry["reported_status"] = "NO_REPORT"
        records.append(entry)
        # Coverage/progress only; complete readout is generated after all combinations.
        print(f"Completed {len(records)}/{len(schedule)}: {label}", flush=True)
    unchanged = all(digest(ROOT / p) == h and digest(output / "source" / p) == h for p, h in source_hashes.items())
    unchanged = unchanged and all(digest(d["path"]) == d["sha256"] for d in deps)
    unchanged = unchanged and all(digest(p) == h for p, h in binaries.items())
    summary = {"status": "LOCAL_BASELINE_COVERAGE_ONLY", "design_sha256": digest(output / "design.json"),
               "input_hashes_unchanged": unchanged, "expected_combinations": len(schedule),
               "observed_combinations": len(records), "model_calls": 0,
               "records": records, "status_counts": dict(Counter(r["reported_status"] for r in records)),
               "independent_contract_pass_count": sum(r.get("contract_passed", False) and r["returncode"] == 0 for r in records),
               "planner_modes": list(modes),
               "mode_counts": {m: {"observed": sum(r["mode"] == m for r in records),
                                    "passed": sum(r["mode"] == m and r.get("contract_passed", False) and r["returncode"] == 0 for r in records)} for m in modes},
               "warning": "Authored small fixtures, not natural tasks. Cost estimates are not measured end-to-end savings. No novel-method effectiveness claim."}
    save(output / "summary.json", summary)
    manifest = {str(p.relative_to(output)): {"sha256": digest(p), "bytes": p.stat().st_size}
                for p in sorted(output.rglob("*")) if p.is_file()}
    save(output / "manifest.json", manifest)
    print(json.dumps({k:v for k,v in summary.items() if k != "records"}, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--java-home", required=True)
    parser.add_argument("--classpath", required=True)
    parser.add_argument("--planner-modes", nargs="+", choices=PLANNER_MODES, default=["hep"])
    args = parser.parse_args()
    run(args.output, args.java_home, args.classpath, args.planner_modes)
