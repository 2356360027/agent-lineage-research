"""Prepare private, condition-masked human-review worksheets from the first pilot.

No judgments are assigned. Identical contracts are grouped only when their
current request, evidence and schema also match; every original call is mapped.
This post-outcome worksheet is not a preregistration or a semantic score.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from scripts.inspect_contract_pilot_pairs import inspect
from scripts.audit_contract_pilot_results import encoded, read_json, require, safe, sha

VERSION = "first-contract-output-review-v1"
ORDER_SEED = 2026101401
VISIBLE_KEYS = ("current_request", "evidence", "schema")
FIELDS = ("entity", "time", "filter", "aggregation", "dedup", "denominator", "unit", "output")


def group_records(calls, records):
    require(len(calls) == len(records) and len(calls) > 0, "Missing assessments")
    groups, definitions = {}, None
    for index, (call, record) in enumerate(zip(calls, records)):
        require(record["index"] == index and record["status"] == "format_valid", "Not a complete valid pilot")
        contract = record["contract"]
        require(set(contract) == set(FIELDS) and all(type(v) is str for v in contract.values()), "Invalid fields")
        packet = json.loads(call["envelope"]["messages"][1]["content"])
        task = {key: packet[key] for key in VISIBLE_KEYS}
        schema = call["envelope"]["response_format"]["json_schema"]["schema"]
        current_defs = {key: schema["properties"][key]["description"] for key in FIELDS}
        if definitions is None:
            definitions = current_defs
        require(current_defs == definitions, "Field definitions changed across calls")
        # Do not combine equal contracts for different tasks, evidence or schema.
        key = sha(encoded({"task": task, "contract": contract}))
        if key not in groups:
            groups[key] = {"item_id": "O-" + key[:12], "task": task,
                           "contract": json.loads(encoded(contract)), "call_indices": []}
        groups[key]["call_indices"].append(index)
    require(len({g["item_id"] for g in groups.values()}) == len(groups), "Short ID collision")
    return sorted(groups.values(), key=lambda g: g["item_id"]), definitions


def ordered(items, reviewer):
    return sorted(items, key=lambda item: sha(encoded([ORDER_SEED, reviewer, item["item_id"]])))


def code_block(value, language=""):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    longest = max((len(part) for part in __import__("re").findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}\n"


def render(items, definitions, reviewer):
    lines = [f"# 已有模型输出复核：复核者 {reviewer}（尚未填写）", "",
             "这是首批开发结果的事后复核，不是新问卷答卷或正式确认实验。",
             "先独立完成当前需求表，再打开本文件；不要先看另一位的答案或组织者映射。",
             "本文件隐藏条件、种子、历史缓存及配对关系；原始输出未改写，内容自身仍可能提示条件。",
             "因此只能称条件标签遮蔽，不能保证完全盲化。请诚实记录先前接触情况。", "",
             "复核者自选代号：____；日期：____；是否独立完成：____。不要填写联系方式。",
             "已完成当前需求表并保存：____。此前见过本批输出/条件/助手评论：是/否/不确定：____。",
             "若未完成需求表，请先停止阅读。是否具备理解这些查询需求所需知识：____。", "",
             "## 复核标准", "",
             "只依据当前需求、公开辅助说明、表结构与下列原始字段定义判断。",
             "不要猜作者答案，不要求逐字匹配；可填无法判断，并说明缺少的信息。",
             "字段放置与完整需求含义分别评价：内容在另一字段出现不自动等于需求遗漏。",
             "每个字段可选多个标签；若不能兼容，请说明原因。", "",
             "- 适配：有依据且放置合适；不适用字段明确说明不适用也可。",
             "- 同义：表达不同但含义支持；可与适配一起选。",
             "- 错位：有依据的内容放入不对应的字段。",
             "- 不全：遗漏/未充分表达要求；需注明是否在其他字段表达。",
             "- 矛盾/添加：与要求矛盾或添加无依据限制。",
             "- 题意未决：原需求或材料有歧义。",
             "- 无法判断：知识/证据不足。", "",
             "这些标签未预填，也不会自动推导缓存诱导偏移或后续执行错误。", ""]
    for field in FIELDS:
        lines.append(f"- {field}: {definitions[field]}")
    lines += ["", "不同表述可能互相重叠；请以完整任务含义为准，记录分歧而非强行归类。", ""]
    for item in ordered(items, reviewer):
        lines += [f"## 输出 {item['item_id']}", "", "### 当前需求", "",
                  code_block(item["task"]["current_request"]), "公开辅助说明：", "",
                  code_block(item["task"]["evidence"]), "表结构：", "",
                  code_block("\n\n".join(item["task"]["schema"]), "sql"),
                  "### 模型原输出（仅JSON排版；未改字段值）", "",
                  code_block(item["contract"], "json"), "### 你的复核", "",
                  "| 字段 | 标签（可多选/无法判断） | 依据；如错位请指出对应字段 |",
                  "|---|---|---|"]
        lines += [f"| {field} | ____ | ____ |" for field in FIELDS]
        lines += ["", "整体：保留关键需求 / 有明确遗漏或矛盾 / 题意未决 / 无法判断：____。",
                  "支持上述判断的当前需求原文及输出位置：____。",
                  "置信程度（高/中/低）及原因：____。", ""]
    lines += ["## 完成记录", "", "完成时间：____；是否借助他人或模型：____（如有，请说明）。",
              "不要因猜测实验目的修改先前答案。先保存独立版本，再处理两位复核者的分歧。", ""]
    return "\n".join(lines)


def prepare(run, output):
    run, output = safe(run), safe(output)
    require(not output.exists() and not output.is_relative_to(run), "Preserve raw/existing files")
    audit = inspect(run)
    exp = run / "experiment"
    plan = read_json(exp / "plan.json")
    records = [read_json(exp / f"assessment-{i:03d}.json") for i in range(len(plan["calls"]))]
    items, definitions = group_records(plan["calls"], records)
    mapped = [i for item in items for i in item["call_indices"]]
    require(sorted(mapped) == list(range(len(records))), "Missing or multiply mapped response")
    # Files are exclusive; any interrupted creation remains preserved, not resumed.
    output.mkdir(parents=True, exist_ok=False)
    for reviewer in (1, 2):
        with (output / f"REVIEWER_{reviewer}_OUTPUTS.md").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(render(items, definitions, reviewer))
    key = {"version": VERSION, "source_plan_sha256": audit["plan_sha256"],
           "source_log_sha256": audit["log_sha256"], "all_calls_mapped": len(mapped),
           "human_reviews_completed": 0, "semantic_labels_generated": False,
           "items": [{"item_id": i["item_id"], "call_indices": i["call_indices"]} for i in items]}
    with (output / "ORGANIZER_ONLY_MAPPING.json").open("xb") as stream:
        stream.write(encoded(key))
    hashes = {p.name: sha(p.read_bytes()) for p in sorted(output.iterdir())}
    summary = {"status": "BLANK_PRIVATE_REVIEW_MATERIALS", "version": VERSION,
               "mapped_calls": len(mapped), "unique_task_contract_items": len(items),
               "human_reviews_completed": 0, "new_model_calls": 0,
               "semantic_labels_generated": False, "source_plan_sha256": audit["plan_sha256"],
               "generator_sha256": sha(Path(__file__).read_bytes()), "artifact_sha256": hashes,
               "warning": "No participant data; do not publish filled forms, raw outputs or organizer mapping."}
    with (output / "PREPARATION.json").open("xb") as stream:
        stream.write(encoded(summary))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = prepare(args.run, args.output)
    print(json.dumps({k: summary[k] for k in ("status", "mapped_calls", "unique_task_contract_items",
                                             "human_reviews_completed", "new_model_calls")}, indent=2))


if __name__ == "__main__":
    main()
