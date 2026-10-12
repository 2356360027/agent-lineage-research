"""BIRD public-label execution admission audit; no inference or semantic grading.

Official SQL is untrusted input. Local SQLite defenses are not an OS sandbox.
No historical views are manufactured from target answers in this audit.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import sqlite3
import subprocess
import sys
import time


VERSION = "bird-readonly-admission-v1"
SEED = "bird-admission-20261005-v1"
LIMITS = {"soft_seconds": 8, "hard_seconds": 12, "max_rows": 10000,
          "max_result_bytes": 5 * 1024 * 1024, "max_value_bytes": 1024 * 1024,
          "max_sql_bytes": 65536, "max_columns": 256,
          "sqlite_heap_bytes": 256 * 1024 * 1024}
FUNCTIONS = set("abs avg char coalesce concat concat_ws count date datetime format glob "
                "hex if iif ifnull instr julianday length like likelihood likely lower "
                "ltrim max min nullif printf quote replace round rtrim sign strftime "
                "substr substring sum time total trim trunc typeof unicode unixepoch "
                "unlikely upper row_number rank dense_rank percent_rank cume_dist "
                "ntile lag lead first_value last_value nth_value".split())


def encoded(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(x):
    return hashlib.sha256(encoded(x)).hexdigest()


def hash_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save_new(path, value):
    with Path(path).open("xb") as f:
        f.write(encoded(value))


def load_questions(path):
    raw = Path(path).read_text(encoding="utf-8-sig")
    rows = json.loads(raw) if raw.lstrip().startswith("[") else [json.loads(s) for s in raw.splitlines() if s.strip()]
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not {"question_id", "db_id", "question", "SQL", "difficulty", "evidence"} <= set(row):
            raise ValueError("Unrecognized BIRD row schema")
        if type(row["question_id"]) is not int or row["question_id"] in ids:
            raise ValueError("Invalid/duplicate question_id")
        ids.add(row["question_id"])
        if not re.fullmatch(r"[a-zA-Z0-9_]+", row["db_id"]):
            raise ValueError("Unsafe database identifier")
        if any(not isinstance(row[k], str) for k in ("question", "SQL", "difficulty", "evidence")):
            raise ValueError("Invalid text field")
    if len(rows) != 500:
        raise ValueError("Expected the explicitly selected 500-question SQLite artifact")
    return rows


def select(rows, seed=SEED, databases=4, per_database=3):
    """Database hash ranking; one per difficulty when present, then hash-ranked fill.

    Uses metadata/IDs only. Does not inspect SQL, labels, output, or model results.
    """
    groups = defaultdict(list)
    for row in rows:
        groups[row["db_id"]].append(row)
    rank = lambda kind, value: digest([seed, kind, value])
    chosen = sorted(groups, key=lambda d: (rank("database", d), d))[:databases]
    result = []
    for db in chosen:
        ordered = sorted(groups[db], key=lambda r: (rank("question", r["question_id"]), r["question_id"]))
        selected = []
        for difficulty in ("simple", "moderate", "challenging"):
            matches = [r for r in ordered if r["difficulty"] == difficulty]
            if matches and len(selected) < per_database:
                selected.append(matches[0])
        selected += [r for r in ordered if r not in selected][:per_database-len(selected)]
        result.extend(selected)
    return chosen, result


def prepare(data_dir):
    data_dir = Path(data_dir)
    rows = load_questions(data_dir / "questions.jsonl")
    databases, selected = select(rows)
    record = {"version": VERSION, "seed": SEED, "questions_sha256": hash_file(data_dir / "questions.jsonl"),
              "selection_rule": "hash-rank 4 databases; one question per simple/moderate/challenging when present; fill remaining slots by question hash rank; no outcome-based replacement",
              "database_ids": databases, "selected_ids": [r["question_id"] for r in selected],
              "selected_records": selected, "limits": LIMITS,
              "label_origin": "public_benchmark_annotations_not_new_human_approvals",
              "purpose": "Execution admission only; not reference semantic validation, model evaluation, reuse success, or timing benchmark",
              "frozen_before_execution": True, "code_sha256": hash_file(__file__)}
    save_new(data_dir / "selected_db_ids.json", databases)
    save_new(data_dir / "selection.json", record)
    return {k: v for k, v in record.items() if k != "selected_records"}


def typed(value):
    if value is None:
        return ["null", None]
    if type(value) is int:
        return ["integer", str(value)]
    if type(value) is float:
        return ["real", value.hex()]
    if type(value) is str:
        return ["text", value]
    if type(value) is bytes:
        return ["blob", value.hex()]
    raise TypeError("Unsupported SQLite value")


def fingerprints(typed_rows):
    rows = [encoded(r) for r in typed_rows]
    return {"sequence_sha256": hashlib.sha256(encoded(typed_rows)).hexdigest(),
            "bag_sha256": hashlib.sha256(b"[" + b",".join(sorted(rows)) + b"]").hexdigest(),
            "distinct_rows": len(set(rows)), "duplicate_rows": len(rows)-len(set(rows))}


def execute_readonly(db_path, sql, limits=None):
    limits = dict(LIMITS if limits is None else limits)
    db_path = Path(db_path).resolve()
    base = {"status": "NOT_STARTED", "complete": False, "rows": [],
            "order_policy": "UNREVIEWED: bag and sequence fingerprints are diagnostics, not an equivalence verdict",
            "read_columns": [], "functions": [], "denied": []}
    if not db_path.is_file():
        return base | {"status": "MISSING_DATABASE"}
    if any(Path(str(db_path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        return base | {"status": "SIDECAR_PRESENT"}
    if len(sql.encode("utf-8")) > limits["max_sql_bytes"]:
        return base | {"status": "SQL_LIMIT"}
    started = time.perf_counter()
    reads, funcs, denied, main_relations = set(), set(), [], set()
    conn = None

    def authorizer(action, arg1, arg2, database, trigger):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        # SQLite's COUNT(*) optimization emits a table read with no column/db name.
        count_only_main_read = database is None and arg2 == "" and arg1 in main_relations
        if action == sqlite3.SQLITE_READ and (database == "main" or count_only_main_read):
            reads.add((arg1 or "", arg2 or ""))
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            name = (arg2 or arg1 or "").lower()
            funcs.add(name)
            if name in FUNCTIONS:
                return sqlite3.SQLITE_OK
        denied.append({"action": action, "arg1": arg1, "arg2": arg2, "database": database})
        return sqlite3.SQLITE_DENY

    try:
        conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=1)
        conn.enable_load_extension(False)
        conn.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
        conn.setconfig(sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA, False)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-32768")
        # Applies to SQLite allocations in this process; parent process has a hard deadline.
        conn.execute(f"PRAGMA hard_heap_limit={int(limits['sqlite_heap_bytes'])}")
        main_relations.update(r[0] for r in conn.execute("SELECT name FROM main.sqlite_schema WHERE type IN ('table','view')"))
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits["max_value_bytes"])
        conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, limits["max_sql_bytes"])
        conn.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, limits["max_columns"])
        conn.set_authorizer(authorizer)
        conn.set_progress_handler(lambda: int(time.perf_counter()-started > limits["soft_seconds"]), 1000)
        cursor = conn.execute(sql)
        if cursor.description is None:
            raise ValueError("Statement did not return a result set")
        base["columns"] = [c[0] for c in cursor.description]
        size = 0
        types = Counter()
        for row in cursor:
            values = [typed(v) for v in row]
            row_size = len(encoded(values))
            if len(base["rows"]) >= limits["max_rows"] or size + row_size > limits["max_result_bytes"]:
                base["status"] = "OUTPUT_LIMIT"
                break
            base["rows"].append(values)
            size += row_size
            types.update(v[0] for v in values)
        else:
            base.update(status="EXECUTED", complete=True)
        base.update(result_bytes=size, retained_rows=len(base["rows"]), value_types=dict(types))
        if base["complete"]:
            base.update(fingerprints(base["rows"]))
    except MemoryError as exc:
        base.update(status="SQLITE_MEMORY_LIMIT", error=str(exc))
    except (sqlite3.Error, ValueError, TypeError) as exc:
        base.update(status="DENIED" if denied else "EXECUTION_ERROR", error=str(exc))
        if isinstance(exc, sqlite3.OperationalError) and "interrupted" in str(exc).lower():
            base["status"] = "SOFT_TIMEOUT"
    finally:
        if conn is not None:
            conn.close()
        base.update(elapsed_seconds=time.perf_counter()-started,
                    read_columns=[list(r) for r in sorted(reads)], functions=sorted(funcs), denied=denied)
    return base


def run_child(db_path, sql, limits=None):
    limits = dict(LIMITS if limits is None else limits)
    packet = {"database": str(Path(db_path).resolve()), "sql": sql, "limits": limits}
    started = time.perf_counter()
    try:
        proc = subprocess.run([sys.executable, "-m", "runtime.bird_readonly_audit", "worker"],
                              input=encoded(packet), capture_output=True,
                              cwd=Path(__file__).resolve().parents[1], timeout=limits["hard_seconds"])
    except subprocess.TimeoutExpired:
        return {"status": "HARD_TIMEOUT", "complete": False, "elapsed_seconds": time.perf_counter()-started}
    if proc.returncode:
        return {"status": "WORKER_ERROR", "complete": False, "returncode": proc.returncode,
                "stderr": proc.stderr.decode("utf-8", errors="replace")[:10000]}
    result = json.loads(proc.stdout)
    result["process_wall_seconds"] = time.perf_counter()-started
    return result


def verify_download(data_dir, db_ids, actual_hashes):
    """Bind actual files to completed acquisition receipts, not just before/after equality."""
    data_dir = Path(data_dir)
    source_path = data_dir / "source_manifest.json"
    manifest = json.loads((data_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
    if manifest["stage"] != "databases_complete" or manifest["selected_db_ids"] != db_ids:
        raise ValueError("Download manifest incomplete or selection differs")
    if hash_file(source_path) != manifest["source_manifest"]["sha256"]:
        raise ValueError("Acquisition provenance manifest changed")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if hash_file(data_dir / "questions.jsonl") != source["files"]["questions.jsonl"]["sha256"]:
        raise ValueError("Questions differ from acquisition receipt")
    if hash_file(data_dir / "selected_db_ids.json") != manifest["selection_file"]["sha256"]:
        raise ValueError("Download selection receipt changed")
    receipts = manifest["databases"]
    if len(receipts) != len(db_ids) or [r["db_id"] for r in receipts] != db_ids:
        raise ValueError("Missing/duplicate database receipts")
    for r in receipts:
        expected_path = f"databases/{r['db_id']}/{r['db_id']}.sqlite"
        if r["path"] != expected_path or r["sha256"] != actual_hashes[r["db_id"]]:
            raise ValueError("Database differs from downloaded artifact")
        if (data_dir / expected_path).stat().st_size != r["bytes"]:
            raise ValueError("Database size differs from receipt")
    return manifest


def run(data_dir, destination):
    data_dir, destination = Path(data_dir), Path(destination)
    selection = json.loads((data_dir / "selection.json").read_text(encoding="utf-8"))
    if selection["questions_sha256"] != hash_file(data_dir / "questions.jsonl"):
        raise ValueError("Question bytes changed after selection")
    if selection["code_sha256"] != hash_file(__file__) or selection["limits"] != LIMITS:
        raise ValueError("Runner/limits differ from frozen selection; freeze a new version")
    rows = load_questions(data_dir / "questions.jsonl")
    dbs, sampled = select(rows)
    if sampled != selection["selected_records"] or dbs != selection["database_ids"]:
        raise ValueError("Selection drift")
    paths = {d: data_dir / "databases" / d / f"{d}.sqlite" for d in dbs}
    before = {d: hash_file(p) for d, p in paths.items()}
    acquisition = verify_download(data_dir, dbs, before)
    destination.mkdir(parents=True, exist_ok=False)
    save_new(destination / "design.json", selection)
    save_new(destination / "acquisition_manifest.json", acquisition)
    save_new(destination / "source_manifest.json", json.loads((data_dir / "source_manifest.json").read_text(encoding="utf-8")))
    (destination / "runner_source.py").write_bytes(Path(__file__).read_bytes())
    save_new(destination / "database_hashes_before.json", before)
    results = []
    for row in sampled:
        result = run_child(paths[row["db_id"]], row["SQL"])
        record = {"question_id": row["question_id"], "db_id": row["db_id"],
                  "difficulty": row["difficulty"], "sql_sha256": digest(row["SQL"]), **result}
        save_new(destination / f"q{row['question_id']}.json", record)
        results.append(record)
        print(f"question={row['question_id']} status={result['status']}", flush=True)
    after = {d: hash_file(p) for d, p in paths.items()}
    save_new(destination / "database_hashes_after.json", after)
    status_counts = dict(Counter(r["status"] for r in results))
    schema_overlap = []
    for i, left in enumerate(results):
        for right in results[i+1:]:
            overlap = set(map(tuple, left.get("read_columns", []))) & set(map(tuple, right.get("read_columns", [])))
            if left["db_id"] == right["db_id"] and overlap:
                schema_overlap.append({"question_ids": [left["question_id"], right["question_id"]],
                                       "shared_read_columns": sorted(overlap)})
    summary = {"status": "ADMISSION_AUDIT_ONLY" if before == after else "DATABASE_CHANGED",
               "created_at": datetime.now(timezone.utc).isoformat(), "version": VERSION,
               "questions": len(results), "databases": len(paths), "status_counts": status_counts,
               "complete_results": sum(r["complete"] for r in results),
               "empty_complete_results": sum(r["complete"] and not r.get("rows") for r in results),
               "database_hashes_unchanged": before == after, "model_calls": 0,
               "semantic_approvals": 0, "reuse_methods_tested": 0,
               "read_overlap_candidates_not_reuse_proof": schema_overlap,
               "python": sys.version, "sqlite": sqlite3.sqlite_version, "platform": platform.platform(),
               "source_sha256": hash_file(__file__), "selection_sha256": hash_file(data_dir / "selection.json"),
               "limits": LIMITS,
               "warnings": ["Only official reference SQL executed; no claim that labels express true intent.",
                            "Output fingerprints preserve types/duplicates but do not define semantic equality.",
                            "No order/tie policy inferred using regex; manual review required.",
                            "Column overlap does not demonstrate materialized-view applicability.",
                            "No model, no new method, no strong-baseline performance comparison.",
                            "Execution times are resource diagnostics, not an efficiency benchmark.",
                            "SQLite authorizer is defense in depth, not an OS-level sandbox."]}
    save_new(destination / "summary.json", summary)
    save_new(destination / "artifact_hashes.json", {p.name: hash_file(p) for p in sorted(destination.iterdir()) if p.is_file()})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("worker")
    prep = sub.add_parser("prepare")
    prep.add_argument("--data-dir", type=Path, required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--data-dir", type=Path, required=True)
    runner.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        packet = json.loads(sys.stdin.buffer.read())
        result = execute_readonly(packet["database"], packet["sql"], packet["limits"])
        sys.stdout.buffer.write(encoded(result))
    else:
        result = prepare(args.data_dir) if args.command == "prepare" else run(args.data_dir, args.output)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        if result.get("status") == "DATABASE_CHANGED":
            raise SystemExit(2)


if __name__ == "__main__":
    main()
