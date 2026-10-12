"""Bounded local differential execution of pinned upstream SQLGlot.

Not an LLM study, an intent-label repair, an MV planner, or a proof of equivalence.
The 12 BIRD SQLs are programs under test, not semantic ground truth for NL tasks.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import zipfile

from . import bird_readonly_audit as ro

WHEEL_SHA = "90aa461490fcd95d14ec3842a97506ae20f6d3e9313307ad31be793d479cca65"
SOURCE_SELECTION_SHA = "13c118f14d95116a56ad97615eb5efb703aaedcec5480a8ec6f78f456acd2636"
VERSION = "sqlglot-differential-admission-v1"
MODES = ["original", "parse_render", "optimize"]
SCHEMA = {"t":{"id":"INTEGER", "x":"INTEGER", "g":"TEXT"}}


def controls():
    # Assistant-authored, explicit counterexamples. Not random samples or BIRD tasks.
    return [
        {"id":"distinct", "kind":"negative", "a":"SELECT x FROM t", "b":"SELECT DISTINCT x FROM t",
         "base":[[1,1,"A"],[2,2,"B"]], "counter":[[1,1,"A"],[2,1,"B"]]},
        {"id":"null_denominator", "kind":"negative", "a":"SELECT AVG(x) FROM t", "b":"SELECT SUM(x)*1.0/COUNT(*) FROM t",
         "base":[[1,2,"A"],[2,4,"B"]], "counter":[[1,2,"A"],[2,None,"B"]]},
        {"id":"range_endpoint", "kind":"negative", "a":"SELECT x FROM t WHERE x<2", "b":"SELECT x FROM t WHERE x<=2",
         "base":[[1,1,"A"],[2,3,"B"]], "counter":[[1,1,"A"],[2,2,"B"]]},
        {"id":"boolean_scope", "kind":"negative", "a":"SELECT id FROM t WHERE g='A' AND x=1 OR x=2", "b":"SELECT id FROM t WHERE g='A' AND (x=1 OR x=2)",
         "base":[[1,1,"A"],[2,2,"A"],[3,3,"B"]], "counter":[[1,1,"A"],[2,2,"A"],[3,2,"B"]]},
        {"id":"unweighted_group_average", "kind":"negative", "a":"SELECT AVG(x) FROM t", "b":"SELECT AVG(m) FROM (SELECT AVG(x) AS m FROM t GROUP BY g) AS v",
         "base":[[1,1,"A"],[2,9,"B"]], "counter":[[1,1,"A"],[2,9,"B"],[3,3,"A"]]},
        {"id":"null_order_vs_min", "kind":"negative", "a":"SELECT x FROM t ORDER BY x LIMIT 1", "b":"SELECT MIN(x) FROM t",
         "base":[[1,1,"A"],[2,3,"B"]], "counter":[[1,None,"A"],[2,3,"B"]]},
        {"id":"alias_rename", "kind":"positive_finite_control", "a":"SELECT a.x FROM t AS a WHERE a.x>1", "b":"SELECT b.x FROM t AS b WHERE b.x>1",
         "base":[[1,1,"A"],[2,3,"B"]], "counter":[[1,None,"A"],[2,3,"B"],[3,3,"A"]]},
        {"id":"predicate_commutation", "kind":"positive_finite_control", "a":"SELECT id FROM t WHERE g='A' AND x>1", "b":"SELECT id FROM t WHERE x>1 AND g='A'",
         "base":[[1,1,"A"],[2,3,"A"]], "counter":[[1,None,"A"],[2,3,"B"],[3,3,"A"]]},
    ]


def load_upstream(wheel):
    wheel=Path(wheel).resolve()
    if ro.hash_file(wheel) != WHEEL_SHA:
        raise ValueError("Unverified upstream wheel")
    sys.path.insert(0,str(wheel))
    import sqlglot
    if sqlglot.__version__ != "30.22.0" or str(wheel) not in sqlglot.__file__:
        raise ValueError("Unexpected loaded module/version")
    return sqlglot


def transform(sql, schema, wheel):
    sg=load_upstream(wheel)
    from sqlglot.optimizer import optimize
    from sqlglot.optimizer.optimizer import RULES
    parsed=sg.parse(sql,read="sqlite",error_level=sg.ErrorLevel.RAISE)
    if len(parsed)!=1 or not isinstance(parsed[0],sg.exp.Query):
        raise ValueError("Single query required")
    tree=parsed[0]
    output={"original":{"sql":sql,"status":"AVAILABLE"}, "flags":{
        "top_level_order":bool(tree.args.get("order")), "top_level_limit":bool(tree.args.get("limit")),
        "nondeterministic_or_ties_review":"NOT_AUTOMATICALLY_VALIDATED"},
        "upstream_rules":[rule.__name__ for rule in RULES]}
    for mode in MODES[1:]:
        start=time.perf_counter()
        try:
            obj=tree.copy() if mode=="parse_render" else optimize(tree.copy(),schema=schema,dialect="sqlite")
            query=obj.sql(dialect="sqlite",unsupported_level=sg.ErrorLevel.RAISE)
            output[mode]={"status":"AVAILABLE","sql":query,"sha256":ro.digest(query),
                          "transform_seconds":time.perf_counter()-start}
        except Exception as exc:
            output[mode]={"status":"TRANSFORM_ERROR","type":type(exc).__name__,"error":str(exc),
                          "transform_seconds":time.perf_counter()-start}
    return output


def transform_child(sql, schema, wheel):
    try:
        p=subprocess.run([sys.executable,"-m","runtime.sqlglot_repro_audit","transform"],
            input=ro.encoded({"sql":sql,"schema":schema,"wheel":str(Path(wheel).resolve())}),
            capture_output=True,timeout=15,cwd=Path(__file__).resolve().parents[1])
        if p.returncode:
            raise ValueError(p.stderr.decode("utf-8",errors="replace")[:4000])
        return json.loads(p.stdout)
    except (subprocess.TimeoutExpired,ValueError,json.JSONDecodeError) as exc:
        return {"original":{"sql":sql,"status":"AVAILABLE"},
                **{mode:{"status":"TRANSFORM_FAILED","error":str(exc)} for mode in MODES[1:]}}


def schema_readonly(path):
    """Metadata inspection only, separate from query authorizer. No data mutation."""
    path=Path(path).resolve()
    if any(Path(str(path)+s).exists() for s in ("-wal","-shm","-journal")):
        raise ValueError("Unexpected database sidecar")
    c=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=1)
    try:
        c.execute("PRAGMA query_only=ON")
        c.setconfig(sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA,False)
        c.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE,True)
        c.enable_load_extension(False)
        ddl=c.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        result={}
        for name,_ in ddl:
            # Names are metadata but quote them as identifiers, never as raw SQL.
            cols=c.execute('PRAGMA table_info("'+name.replace('"','""')+'")').fetchall()
            result[name]={r[1]:r[2] or "BLOB" for r in cols}
        return {"schema":result,"ddl":ddl,"typing_caution":"Declared SQLite types are not guarantees about every stored value."}
    finally:
        c.close()


def compare(a,b):
    if not a.get("complete") or not b.get("complete"):
        return {"status":"UNRESOLVED_INCOMPLETE"}
    return {"status":"COMPARED_ON_ONE_INSTANCE_ONLY", "typed_bag_equal":a["bag_sha256"]==b["bag_sha256"],
            "typed_sequence_equal":a["sequence_sha256"]==b["sequence_sha256"],
            "column_names_equal":a.get("columns")==b.get("columns"),
            "counts_equal":a["retained_rows"]==b["retained_rows"]}


def evaluate(sql,schema,db,wheel):
    transformed=transform_child(sql,schema,wheel)
    result={"transform":transformed,"executions":{},"comparisons":{}}
    for mode in MODES:
        spec=transformed[mode]
        result["executions"][mode]=ro.run_child(db,spec["sql"]) if spec["status"]=="AVAILABLE" else {"status":"NOT_EXECUTED_TRANSFORM_FAILED","complete":False}
    for mode in MODES[1:]:
        result["comparisons"][mode]=compare(result["executions"]["original"],result["executions"][mode])
    return result


def prepare(data,wheel,output):
    data,wheel,output=Path(data),Path(wheel),Path(output)
    if ro.hash_file(data/"selection.json")!=SOURCE_SELECTION_SHA:
        raise ValueError("BIRD selection changed")
    selection=json.loads((data/"selection.json").read_text(encoding="utf-8"))
    hashes={d:ro.hash_file(data/"databases"/d/f"{d}.sqlite") for d in selection["database_ids"]}
    ro.verify_download(data,selection["database_ids"],hashes)
    if ro.hash_file(wheel)!=WHEEL_SHA:
        raise ValueError("Wheel changed")
    output.mkdir(parents=True,exist_ok=False)
    sources=[Path(__file__),Path(ro.__file__)]
    src={p.name:ro.hash_file(p) for p in sources}
    design={"version":VERSION,"status":"FROZEN_BEFORE_EXECUTION","client_date":"2026-10-12",
            "data_path":str(data.resolve()),"wheel_path":str(wheel.resolve()),"wheel_sha256":WHEEL_SHA,
            "source_hashes":src,"selection_sha256":SOURCE_SELECTION_SHA,"database_hashes":hashes,
            "selected_ids":selection["selected_ids"],"controls":controls(),"modes":MODES,
            "maximum_sql_executions":132,"model_calls":0,"query_limits":ro.LIMITS,
            "transform_timeout_seconds":15,
            "purpose":"Upstream component differential test, not NL benchmark or method efficacy",
            "positive_or_negative_controls":"Explicit authored analytic fixtures; no random-sample statistics",
            "comparison":"Typed bag/sequence and column labels separately; neither single-instance equality nor key equality proves semantics",
            "retry":"none; preserve all failures and do not replace samples"}
    ro.save_new(output/"design.json",design)
    for p in sources:
        with (output/("frozen_"+p.name)).open("xb") as f:
            f.write(p.read_bytes())
    # Byte-for-byte acquisition receipts, not re-serialized substitutes.
    for name in ("selection.json","source_manifest.json","dataset_manifest.json"):
        with (output/name).open("xb") as f:
            f.write((data/name).read_bytes())
    return {"output":str(output),"questions":12,"controls":8,"maximum_sql_executions":132}


def run(output):
    output=Path(output)
    design=json.loads((output/"design.json").read_text(encoding="utf-8"))
    if (output/"execution_started.json").exists():
        raise ValueError("Refuse repeat or overwrite of started batch")
    for p in (Path(__file__),Path(ro.__file__)):
        if ro.hash_file(p)!=design["source_hashes"][p.name]:
            raise ValueError("Frozen source drift")
    if controls()!=design["controls"] or MODES!=design["modes"] or ro.LIMITS!=design["query_limits"]:
        raise ValueError("Design drift")
    data,wheel=Path(design["data_path"]),Path(design["wheel_path"])
    if ro.hash_file(wheel)!=WHEEL_SHA or ro.hash_file(data/"selection.json")!=SOURCE_SELECTION_SHA:
        raise ValueError("Inputs drifted")
    selection=json.loads((data/"selection.json").read_text(encoding="utf-8"))
    before={d:ro.hash_file(data/"databases"/d/f"{d}.sqlite") for d in selection["database_ids"]}
    if before!=design["database_hashes"]:
        raise ValueError("Database drift")
    ro.verify_download(data,selection["database_ids"],before)
    started=time.time()
    ro.save_new(output/"execution_started.json",{"unix":started,"python":sys.version,"sqlite":sqlite3.sqlite_version})
    schemas={d:schema_readonly(data/"databases"/d/f"{d}.sqlite") for d in before}
    ro.save_new(output/"schemas.json",schemas)
    with zipfile.ZipFile(wheel) as z:
        upstream={n:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('sqlglot/optimizer/') and n.endswith('.py')}
    ro.save_new(output/"upstream_optimizer_source_hashes.json",upstream)
    bird=[]
    for row in selection["selected_records"]:
        db=data/"databases"/row["db_id"]/f"{row['db_id']}.sqlite"
        result={"question_id":row["question_id"],"db_id":row["db_id"],**evaluate(row["SQL"],schemas[row["db_id"]]["schema"],db,wheel)}
        ro.save_new(output/f"bird_{row['question_id']}.json",result)
        bird.append(result)
        print(f"BIRD {row['question_id']} "+str(result["comparisons"]),flush=True)
    fixture_results=[]
    fixture_hashes={}
    for case in design["controls"]:
        rec={"id":case["id"],"kind":case["kind"],"states":{}}
        for state in ("base","counter"):
            db=output/f"fixture_{case['id']}_{state}.sqlite"
            if db.exists():
                raise ValueError("Fixture exists")
            c=sqlite3.connect(db)
            c.execute("CREATE TABLE t(id INTEGER, x INTEGER, g TEXT)")
            c.executemany("INSERT INTO t VALUES(?,?,?)",case[state])
            c.commit(); c.close()
            start_hash=ro.hash_file(db)
            arms={arm:evaluate(case[arm],SCHEMA,db,wheel) for arm in ("a","b")}
            pairs={mode:compare(arms["a"]["executions"][mode],arms["b"]["executions"][mode]) for mode in MODES}
            keys={}
            for mode in MODES:
                left,right=(arms[a]["transform"][mode] for a in ("a","b"))
                keys[mode] = (left.get("sql")==right.get("sql")) if left["status"]==right["status"]=="AVAILABLE" else None
            rec["states"][state]={"arms":arms,"pair_comparison":pairs,"query_text_key_equal":keys}
            fixture_hashes[db.name]={"before":start_hash,"after":ro.hash_file(db)}
        ro.save_new(output/f"control_{case['id']}.json",rec)
        fixture_results.append(rec)
        print(f"CONTROL {case['id']} base="+str(rec['states']['base']['pair_comparison']['original'])+" counter="+str(rec['states']['counter']['pair_comparison']['original']),flush=True)
    after={d:ro.hash_file(data/"databases"/d/f"{d}.sqlite") for d in before}
    ro.save_new(output/"database_hashes_after.json",after)
    ro.save_new(output/"fixture_hashes.json",fixture_hashes)
    all_evals=bird+[arm for r in fixture_results for s in r['states'].values() for arm in s['arms'].values()]
    statuses=Counter(e['status'] for r in all_evals for e in r['executions'].values())
    negative=[r for r in fixture_results if r['kind']=='negative']
    summary={"status":"LOCAL_UPSTREAM_COMPONENT_AUDIT_ONLY","version":VERSION,"bird_queries":len(bird),
        "synthetic_control_pairs":len(fixture_results),"query_execution_statuses":dict(statuses),"model_calls":0,
        "elapsed_seconds":time.time()-started,
        "database_bytes_unchanged":before==after,"fixture_bytes_unchanged":all(v['before']==v['after'] for v in fixture_hashes.values()),
        "bird_by_mode":{mode:{"compared":sum(r['comparisons'][mode]['status'].startswith('COMPARED') for r in bird),
            "typed_bag_equal":sum(r['comparisons'][mode].get('typed_bag_equal',False) for r in bird),
            "typed_sequence_equal":sum(r['comparisons'][mode].get('typed_sequence_equal',False) for r in bird),
            "column_names_equal":sum(r['comparisons'][mode].get('column_names_equal',False) for r in bird)} for mode in MODES[1:]},
        "all_programs_by_mode":{mode:{"compared":sum(r['comparisons'][mode]['status'].startswith('COMPARED') for r in all_evals),
            "typed_bag_equal":sum(r['comparisons'][mode].get('typed_bag_equal',False) for r in all_evals),
            "typed_sequence_equal":sum(r['comparisons'][mode].get('typed_sequence_equal',False) for r in all_evals),
            "column_names_equal":sum(r['comparisons'][mode].get('column_names_equal',False) for r in all_evals)} for mode in MODES[1:]},
        "constructed_negative_pairs_equal_on_base":sum(r['states']['base']['pair_comparison']['original'].get('typed_bag_equal',False) for r in negative),
        "constructed_negative_pairs_different_on_counter":sum(r['states']['counter']['pair_comparison']['original'].get('typed_bag_equal') is False for r in negative),
        "optimizer_query_text_keys_equal_by_control":{r['id']:r['states']['base']['query_text_key_equal']['optimize'] for r in fixture_results},
        "warnings":["No accuracy rate or intent repair claimed for BIRD.","SQLGlot optimizer is not an MV rewrite planner or a universal semantic verifier.",
            "Counterexamples are authored fixtures, not observed population frequencies.","Same-instance result equality is not universal equivalence.",
            "Column aliases, duplicate multiplicity, types, ordering and ties are reported separately.",
            "No causal speedup estimate; one pass with uncontrolled OS caches.","SQLite declared types need not match dynamic value types.",
            "Query text keys alone do not constitute cache keys: snapshot/schema/parameters/session/policy must also be bound."]}
    if not summary["database_bytes_unchanged"] or not summary["fixture_bytes_unchanged"]:
        summary["status"]="INTEGRITY_FAILURE"
    ro.save_new(output/"summary.json",summary)
    ro.save_new(output/"artifact_hashes.json",{p.name:ro.hash_file(p) for p in output.iterdir() if p.is_file()})
    return summary


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("transform")
    prep=sub.add_parser("prepare"); prep.add_argument("--data",type=Path,required=True); prep.add_argument("--wheel",type=Path,required=True); prep.add_argument("--output",type=Path,required=True)
    execute=sub.add_parser("run"); execute.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    if args.command=="transform":
        record=json.loads(sys.stdin.buffer.read()); sys.stdout.buffer.write(ro.encoded(transform(**record)))
    else:
        result=prepare(args.data,args.wheel,args.output) if args.command=="prepare" else run(args.output)
        print(json.dumps(result,ensure_ascii=True,indent=2))
        if result.get("status")=="INTEGRITY_FAILURE":
            raise SystemExit(2)
