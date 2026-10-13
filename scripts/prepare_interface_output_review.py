"""Private blank human-review materials for the completed 12-call diagnostic.

No new inference or labels. Keep the earlier 24-call worksheets untouched.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from scripts.audit_contract_interface_results import audit, PLAN_SHA, BUNDLE_SHA
from scripts.audit_contract_pilot_results import encoded, read_json, require, safe, sha, block_effects
from scripts.prepare_contract_output_review import group_records, ordered, render

ROOT = Path(__file__).resolve().parents[1]
SOURCE_NOTICE = ROOT / "reviews/cache_intent_output_review_20261014_001/SOURCE_NOTICE.md"


def materials(plan, records):
    require(len(plan["calls"]) == len(records) == 12, "Requires all twelve responses")
    # Adapt only the internal worksheet input; never change a saved request.
    calls = [{"envelope": deepcopy(call["request"])} for call in plan["calls"]]
    items, definitions = group_records(calls, records)
    for item in items:
        item["item_id"] = "I-" + item["item_id"][2:]
    require(sorted(i for item in items for i in item["call_indices"]) == list(range(12)), "Incomplete mapping")
    texts = {}
    for reviewer in (1, 2):
        text = render(items, definitions, reviewer)
        require(text.count("这是首批开发结果的事后复核") == 1, "Renderer heading changed")
        texts[reviewer] = text.replace("这是首批开发结果的事后复核", "这是接口开发批次的事后输出复核", 1)
    return items, texts


def prepare(run, bundle, output):
    run, output = safe(run), safe(output)
    require(not output.exists() and not output.is_relative_to(run), "Preserve raw data and prior review files")
    verified = audit(run, bundle)
    require(verified["status"] == "VERIFIED_COMPLETE_UNREVIEWED" and
            verified["structural_counts"]["format_valid"] == 12, "All outputs must be retained; no filtering failures")
    exp = run / "experiment"
    plan = read_json(exp / "plan.json")
    records = [read_json(exp / f"assessment-{i:03d}.json") for i in range(12)]
    items, texts = materials(plan, records)
    notice = SOURCE_NOTICE.read_bytes()
    # Reserve a new directory. Interrupted generation is not silently resumed.
    output.mkdir(parents=True, exist_ok=False)
    for reviewer, text in texts.items():
        with (output / f"REVIEWER_{reviewer}_OUTPUTS.md").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    with (output / "SOURCE_NOTICE.md").open("xb") as stream:
        stream.write(notice)
    mapping = {"source_plan_sha256": PLAN_SHA, "source_bundle_sha256": BUNDLE_SHA,
               "mapped_calls": 12, "independent_targets": 2, "human_reviews_completed": 0,
               "semantic_labels_generated": False,
               "reviewer_item_order": {str(r): [x["item_id"] for x in ordered(items, r)] for r in (1, 2)},
               "items": [{"item_id": x["item_id"], "calls": [{
                   "index": i, "history_id": plan["calls"][i]["history_id"],
                   "interface_arm": plan["calls"][i]["interface_arm"],
                   "request_sha256": plan["calls"][i]["request_sha256"]} for i in x["call_indices"]]} for x in items]}
    with (output / "ORGANIZER_ONLY_MAPPING.json").open("xb") as stream:
        stream.write(encoded(mapping))
    summary = {"status": "BLANK_PRIVATE_INTERFACE_REVIEW_MATERIALS", "source_plan_sha256": PLAN_SHA,
               "mapped_calls": 12, "unique_task_contract_items": len(items), "independent_targets": 2,
               "new_model_calls": 0, "human_reviews_completed": 0, "semantic_labels_generated": False,
               "perfect_blinding_claim": False,
               "artifact_sha256": {p.name: sha(p.read_bytes()) for p in sorted(output.iterdir())},
               "generator_sha256": sha(Path(__file__).read_bytes()),
               "renderer_sha256": sha((ROOT / "scripts/prepare_contract_output_review.py").read_bytes())}
    with (output / "PREPARATION.json").open("xb") as stream:
        stream.write(encoded(summary))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    block_effects()
    summary = prepare(args.run, args.bundle, args.output)
    print(json.dumps({k: v for k, v in summary.items() if k != "artifact_sha256"}, indent=2))


if __name__ == "__main__":
    main()
