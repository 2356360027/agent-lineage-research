"""Build six local, pending-review cache histories and offline prompt previews.

No inference, automatic semantic approvals or official benchmark reproduction.
Only an explicit --execute runs six historical queries plus one schema read.
Target outputs are reused from the hash-verified prior compatibility audit.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import sys

from runtime import arcwise_compatibility as arc
from runtime import bird_readonly_audit as sql
from runtime import cache_intent_prompts as prompts
from scripts.fetch_bird_fixed import read_json, no_link_ancestors
from scripts import fetch_bird_fixed

VERSION = "cache-intent-dev-package-v1"
SCHEMA_QUERY = "SELECT sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"


def transform_rows(rows, kind):
    """Candidate cache-only transformations, not universal SQL equivalence proofs."""
    if kind == "identity":
        return rows
    if kind == "filter_positive_label_project_id":
        if any(len(row) != 2 for row in rows):
            raise ValueError("Expected molecule_id,label rows")
        projected = {sql.encoded([row[0]]): [row[0]] for row in rows if row[1] == ["text", "+"]}
        return [projected[key] for key in sorted(projected)]
    if kind == "count_distinct_nonnull_ids":
        if any(len(row) != 1 for row in rows):
            raise ValueError("Expected bond_id rows")
        ids = {sql.encoded(row[0]) for row in rows if row[0][0] != "null"}
        return [[sql.typed(len(ids))]]
    if kind is None:
        return None
    raise ValueError("Unknown transform")


def verify_prior(directory):
    directory = Path(directory)
    no_link_ancestors(directory)
    hashes = read_json(directory / "artifact_hashes.json")
    required = {"summary.json", "database_hashes_after.json"}
    required.update(f"q{qid}.{suffix}.json" for qid in (200, 234) for suffix in ("metadata", "revised"))
    if not isinstance(hashes, dict) or not required <= hashes.keys():
        raise ValueError("Prior manifest omits required target artifacts")
    for name, expected in hashes.items():
        if Path(name).name != name or name in (".", ".."):
            raise ValueError("Invalid prior artifact name")
        path = directory / name
        no_link_ancestors(path)
        if sql.hash_file(path) != expected:
            raise ValueError(f"Prior audit artifact changed: {name}")
    summary = read_json(directory / "summary.json")
    if summary["status"] != "DEVELOPMENT_COMPATIBILITY_ONLY" or not summary["database_hashes_unchanged"]:
        raise ValueError("Prior compatibility audit not complete and unchanged")
    return summary


def validate_plan(plan):
    if plan.get("version") != "cache-intent-pairs-dev-v1" or plan.get("independent_target_count") != 2:
        raise ValueError("Unexpected plan version or target count")
    if plan.get("target_ids") != [200, 234] or plan.get("human_review_status") != "pending":
        raise ValueError("This development package is restricted to two pending targets")
    if [t["question_id"] for t in plan["targets"]] != plan["target_ids"]:
        raise ValueError("Target order mismatch")
    ids = []
    for target in plan["targets"]:
        if set(target["candidate_contract"]) != set(prompts.CONTRACT_FIELDS):
            raise ValueError("Candidate contract requires eight fields")
        if any(not isinstance(value, str) or not value.strip() for value in target["candidate_contract"].values()):
            raise ValueError("Candidate contract fields must be nonempty text")
        histories = target["histories"]
        if [h["candidate_relation"] for h in histories] != ["direct", "cache_only_transformable", "insufficient_without_base_reads"]:
            raise ValueError("Expected three different candidate relationship controls")
        repair = "filter_positive_label_project_id" if target["question_id"] == 200 else "count_distinct_nonnull_ids"
        if [h.get("transform") for h in histories] != ["identity", repair, None]:
            raise ValueError("Transforms do not match the fixed target/relationship controls")
        for h in histories:
            if not isinstance(h["id"], str) or not h["id"].isalnum():
                raise ValueError("Unsafe history identifier")
            if any(not isinstance(h[k], str) or not h[k].strip() for k in ("historical_request", "sql", "relation_basis")):
                raise ValueError("Missing historical task definition")
            ids.append(h["id"])
    if len(set(ids)) != 6:
        raise ValueError("Expected six unique histories")


def review_text(targets, schema, packet_hash):
    lines = ["# 第一阶段：独立确认当前需求（不是已完成问卷）", "",
             "请两位分别填写各自文件，先独立作答，再讨论分歧。当前材料不显示历史缓存、参考SQL、候选契约或查询结果。",
             "这些是公开基准中的任务，不是你原创的任务。请记录自己的理解，不需要猜测作者的标准答案。",
             "身份/日期：____；是否独立完成：____。请勿将个人联系方式写入公开仓库。",
             f"材料指纹：{packet_hash}", "", "## 数据库表结构", "", "```sql", *schema, "```", ""]
    field_names = dict(zip(prompts.CONTRACT_FIELDS, (
        "实体/对象", "时间范围及边界（不涉及请写不适用）", "筛选条件", "汇总或分组方式",
        "重复记录如何处理", "比例/均值的分母（不涉及请写不适用）", "输出单位", "输出哪些内容、几列、是否排序")))
    for target in targets:
        lines += [f"## 任务 {target['question_id']}", "", target["question"], "",
                  "数据集给出的辅助说明：", target["evidence"] or "（无）", "",
                  "可判定性：明确 / 需要澄清 / 数据描述可能有错（请选择并说明）：____", ""]
        lines += [f"- {field}（{field_names[field]}）：____" for field in prompts.CONTRACT_FIELDS]
        lines += ["", "需要澄清的具体问题：____", "", "最容易误解的地方：____", ""]
    lines += ["完成后再打开第二阶段材料。空白、助手建议或软件检查都不算人工确认。", ""]
    return "\n".join(lines)


def build(plan_path, arcwise_dir, bird_dir, prior_dir, output):
    plan_path, arcwise_dir, bird_dir, prior_dir, output = map(Path, (plan_path, arcwise_dir, bird_dir, prior_dir, output))
    no_link_ancestors(output)
    if output.exists():
        raise ValueError("Refuse overwrite of existing package")
    plan = read_json(plan_path)
    validate_plan(plan)
    _, source = arc.verify_arcwise(arcwise_dir)
    verify_prior(prior_dir)
    acquisition = read_json(bird_dir / "dataset_manifest.json")
    db_ids = acquisition["selected_db_ids"]
    paths = {d: bird_dir / "databases" / d / f"{d}.sqlite" for d in db_ids}
    for path in paths.values():
        no_link_ancestors(path)
    db_before = {d: sql.hash_file(path) for d, path in paths.items()}
    sql.verify_download(bird_dir, db_ids, db_before)
    if db_before != read_json(prior_dir / "database_hashes_after.json"):
        raise ValueError("Database snapshot differs from prior target execution")
    targets, reference = [], {}
    for qid in plan["target_ids"]:
        row = source[qid]
        prior = read_json(prior_dir / f"q{qid}.metadata.json")
        result = read_json(prior_dir / f"q{qid}.revised.json")
        if (row != prior["revised_record"] or row["db_id"] != "toxicology" or
                result.get("complete") is not True or result["sql_sha256"] != sql.digest(row["SQL"])):
            raise ValueError("Target record/output differs from verified source")
        if result["bag_sha256"] != sql.fingerprints(result["rows"])["bag_sha256"]:
            raise ValueError("Target rows differ from recorded digest")
        targets.append(row)
        reference[qid] = result
    output.mkdir(parents=True, exist_ok=False)
    sql.save_new(output / "started.json", {"version": VERSION, "utc": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": sql.hash_file(plan_path), "arcwise_manifest_sha256": sql.hash_file(arcwise_dir / "source_manifest.json"),
        "prior_artifact_manifest_sha256": sql.hash_file(prior_dir / "artifact_hashes.json"),
        "target_count": 2, "history_count": 6, "max_new_sql_calls": 7,
        "model_calls": 0, "human_approvals": 0, "database_hashes_before": db_before,
        "limits": sql.LIMITS, "python": sys.version, "sqlite": sqlite3.sqlite_version})
    for path, name in ((plan_path, "candidate_plan.json"), (Path(__file__), "builder_source.py"),
                       (Path(prompts.__file__), "prompt_source.py"), (Path(sql.__file__), "readonly_source.py"),
                       (Path(arc.__file__), "compatibility_source.py"), (Path(fetch_bird_fixed.__file__), "acquisition_source.py")):
        shutil.copyfile(path, output / name)
    cases, previews, checks = [], [], []
    try:
        schema_result = sql.run_child(paths["toxicology"], SCHEMA_QUERY)
        sql.save_new(output / "schema_result.json", schema_result)
        if not schema_result.get("complete"):
            raise ValueError("Schema read incomplete; package not ready")
        schema = [row[0][1] for row in schema_result["rows"]]
        for target in plan["targets"]:
            qid, row = target["question_id"], source[target["question_id"]]
            for h in target["histories"]:
                result = sql.run_child(paths["toxicology"], h["sql"])
                result["sql_sha256"] = sql.digest(h["sql"])
                sql.save_new(output / f"{h['id']}.result.json", result)
                transformed = transform_rows(result["rows"], h["transform"]) if result.get("complete") else None
                diagnostic = {"history_id": h["id"], "target_id": qid, "query_status": result["status"],
                    "complete": result.get("complete", False),
                    "candidate_relation": h["candidate_relation"],
                    "raw_bag_matches_target": result.get("bag_sha256") == reference[qid]["bag_sha256"] if result.get("complete") else None,
                    "transform_bag_matches_target": sql.fingerprints(transformed)["bag_sha256"] == reference[qid]["bag_sha256"] if transformed is not None else None,
                    "semantic_approval": False}
                checks.append(diagnostic)
                cases.append({"target_id": qid, "target_record": row, "candidate_contract": target["candidate_contract"],
                    "history": h, "diagnostic": diagnostic, "human_reviews": [], "split": "development_exposed"})
                # Failed histories remain recorded, but do not get apparently ready previews.
                if result.get("complete"):
                    public_history = {"historical_request": h["historical_request"], "sql": h["sql"]}
                    for factor in ("availability", "read_cost"):
                        pair = prompts.make_pair(row["question"], row["evidence"], schema, public_history, db_before["toxicology"], factor=factor)
                        prompts.assert_pair(*pair, factor=factor)
                        previews.append({"private_history_id": h["id"], "factor": factor,
                            "envelopes": pair, "envelope_hashes": [sql.digest(e) for e in pair]})
                print(f"history={h['id']} status={result['status']}", flush=True)
        sql.save_new(output / "PRIVATE_cases.json", cases)
        sql.save_new(output / "PRIVATE_prompt_previews.json", previews)
        review_packet = {"targets": [{k: r[k] for k in ("question_id", "question", "evidence")} for r in targets], "schema": schema}
        sql.save_new(output / "task_review_packet.json", review_packet)
        form = review_text(targets, schema, sql.digest(review_packet))
        for number in (1, 2):
            with (output / f"REVIEWER_{number}_TASKS.md").open("x", encoding="utf-8") as handle:
                handle.write(form)
        stage2 = ["# 第二阶段：历史任务和关系复核", "", "请先分别完成第一阶段，再查看以下助手候选。不得把这些建议当作已审核真值。", ""]
        for case in cases:
            h = case["history"]
            stage2 += [f"## {h['id']} / 当前任务 {case['target_id']}", "", h["historical_request"],
                       "", "```sql", h["sql"], "```", "", f"候选关系：{h['candidate_relation']}",
                       h["relation_basis"], "", "当前需求的助手候选（允许否定）：", "```json",
                       sql.encoded(case["candidate_contract"]).decode("utf-8"), "```", "",
                       f"完整历史结果见 {h['id']}.result.json；软件诊断见 PRIVATE_cases.json。",
                       "历史SQL是否正确表达历史需求：____；缓存是否完整且来源正确：____；关系是否成立/需要哪些额外假设：____；复核人/日期：____", ""]
        with (output / "STAGE2_HISTORY_REVIEW.md").open("x", encoding="utf-8") as handle:
            handle.write("\n".join(stage2))
    except BaseException as exc:
        sql.save_new(output / "failure.json", {"type": type(exc).__name__, "message": str(exc), "automatic_retry": False})
        raise
    finally:
        db_after = {d: sql.hash_file(path) for d, path in paths.items()}
        sql.save_new(output / "database_hashes_after.json", db_after)
    status = ("DATABASE_CHANGED" if db_before != db_after else
              "INCOMPLETE_HISTORY_PACKAGE" if not all(c["complete"] for c in checks) else
              "CANDIDATES_PENDING_HUMAN_REVIEW")
    summary = {"status": status,
        "target_count": 2, "database_count": 1, "history_count": len(checks),
        "query_status_counts": dict(Counter(c["query_status"] for c in checks)),
        "new_sql_calls": 1 + len(checks), "prior_target_outputs_reused": 2,
        "declared_transform_checks": sum(c["transform_bag_matches_target"] is not None for c in checks),
        "transform_checks_matching": sum(c["transform_bag_matches_target"] is True for c in checks),
        "preview_pairs": len(previews), "preview_envelopes": 2*len(previews),
        "human_approvals": 0, "model_calls": 0, "inference_eligible": False,
        "official_arcwise_environment_verified": False, "database_hashes_unchanged": db_before == db_after,
        "warnings": ["Two target clusters, not six independent tasks; one development database.",
                     "Histories are researcher-constructed, not natural user logs.",
                     "Execution/matched outputs are not semantic correctness or reviewer approvals.",
                     "Insufficient cache alone may still be useful with new base reads.",
                     "Pre-result explicit-contract previews are not ordinary agent execution.",
                     "Relative costs are declared, not measured money or runtime."]}
    sql.save_new(output / "summary.json", summary)
    sql.save_new(output / "artifact_hashes.json", {p.name: sql.hash_file(p) for p in sorted(output.iterdir()) if p.is_file()})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("configs/cache_intent_pairs_dev_v1.json"))
    parser.add_argument("--arcwise-dir", type=Path, default=Path("runtime/runs/datasets/arcwise-plat-fixed-20261012-001"))
    parser.add_argument("--bird-dir", type=Path, default=Path("runtime/runs/datasets/bird-mini-fixed-20261005-002"))
    parser.add_argument("--prior-dir", type=Path, default=Path("runtime/runs/arcwise-compatibility-20261012-001"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = build(args.plan, args.arcwise_dir, args.bird_dir, args.prior_dir, args.output) if args.execute else {
        "status": "PLAN_NO_EXECUTION", "targets": 2, "histories": 6, "max_new_sql_calls": 7, "model_calls": 0}
    print(sql.encoded(result).decode("utf-8"))
    if result.get("status") in ("DATABASE_CHANGED", "INCOMPLETE_HISTORY_PACKAGE"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
