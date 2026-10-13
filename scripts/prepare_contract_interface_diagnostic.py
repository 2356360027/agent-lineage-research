"""Prepare twelve OFFLINE development requests; no execution entry point.

The first 24 responses are never read. This is post-outcome interface development,
not an independently confirmed experiment or a cache-convenience effect test.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import json

from runtime import cache_intent_contract_pilot as pilot
from runtime import cache_intent_prompts as legacy
from runtime import cache_intent_prompts_v2 as candidate
from scripts.audit_contract_pilot_results import PLAN, encoded, require, safe, sha

VERSION = "contract-definition-interface-diagnostic-v1"
SCHEDULE_SEED = 2026101302
MAX_SECONDS = 600
MODEL = "Qwen/Qwen2.5-7B-Instruct"


def extract_controls(source_plan):
    """Require all fixed histories/factors, choosing the common available/cheap arm."""
    groups = {}
    require(len(source_plan["calls"]) == 24, "Expected 24 source calls")
    for call in source_plan["calls"]:
        key = (call["history_id"], call["factor"])
        require(call["history_id"] in pilot.HISTORIES and call["factor"] in ("availability", "read_cost"),
                "Unknown source history/factor")
        arms = groups.setdefault(key, {})
        require(type(call["arm"]) is int and call["arm"] in (0, 1) and call["arm"] not in arms,
                "Duplicate or invalid source arm")
        arms[call["arm"]] = call["envelope"]
    require(set(groups) == {(h, f) for h in pilot.HISTORIES for f in ("availability", "read_cost")},
            "Incomplete histories/factors")
    for (_, factor), arms in groups.items():
        require(set(arms) == {0, 1}, "Missing source arm")
        legacy.assert_pair(arms[0], arms[1], factor=factor)
    selected = {}
    for history in sorted(pilot.HISTORIES):
        available = groups[(history, "availability")][1]
        cheap = groups[(history, "read_cost")][0]
        require(encoded(available) == encoded(cheap), "Expected common condition differs")
        selected[history] = deepcopy(available)
    return selected


def construct(source_plan):
    controls = extract_controls(source_plan)
    order = sorted(controls, key=lambda h: sha(encoded([SCHEDULE_SEED, "order", h])))
    calls = []
    for position, history in enumerate(order):
        old = controls[history]
        packet = json.loads(old["messages"][1]["content"])
        new = candidate.make_pair(packet["current_request"], packet["evidence"], packet["schema"],
                                  packet["history"], packet["context"]["database_sha256"])[1]
        require(candidate._remove_verified_suffix(new) == old, "Candidate changes more than definitions")
        require(legacy._diff(old, new) == [("messages", 0, "content")], "Unexpected contrast")
        paired_seed = int(sha(encoded([SCHEDULE_SEED, "decode", history]))[:7], 16)
        # Exactly three blocks have legacy first, three have candidate first.
        arms = ("legacy", "visible_definitions") if position % 2 == 0 else ("visible_definitions", "legacy")
        for arm in arms:
            envelope = deepcopy(old if arm == "legacy" else new)
            request = pilot.payload({"seed": paired_seed, "envelope": envelope}, MODEL)
            calls.append({"index": len(calls), "history_id": history, "interface_arm": arm,
                          "paired_seed": paired_seed, "request": request,
                          "request_sha256": sha(encoded(request))})
    require(len(calls) == 12, "Invalid call count")
    return {
        "version": VERSION, "status": "PREPARED_NOT_AUTHORIZED_NOT_EXECUTED",
        "purpose": "POST_OUTCOME_UNREVIEWED_INTERFACE_DEVELOPMENT",
        "source_plan_sha256": sha(encoded(source_plan)),
        "candidate_prompt_version": candidate.VERSION, "schedule_seed": SCHEDULE_SEED,
        "model": MODEL, "independent_targets": 2, "history_count": 6,
        "maximum_model_calls": 12, "maximum_completion_tokens_per_call": pilot.MAX_TOKENS,
        "proposed_supervisor_seconds": MAX_SECONDS, "maximum_retries": 0,
        "fixed_context": {"materialized_available": True, "read_cost": 1,
                          "sql_call_cost": 10, "budget": 100},
        "human_approvals": 0, "formal_inference_eligible": False,
        "semantic_endpoints_automatically_scored": False,
        "calls": calls,
        "warnings": [
            "Offline preparation does not authorize or launch inference; no executor is included.",
            "The twelve prospective responses must be new; do not reuse the first pilot as the control arm.",
            "One call per arm/history, six histories sharing two targets; no independent-sample inference.",
            "The intervention adds definitions, length and instructional emphasis, not isolated semantics.",
            "Cache availability/cost are fixed: this does not estimate cache-convenience effects.",
            "Tools, SQL, cache reads and subsequent programs remain disabled; C is unobserved.",
            "All format failures/transport failures/missing calls stay in the fixed schedule; no replacement.",
            "Only coverage/format/tokens/timing are automated; semantic improvement needs independent review.",
            "Same seeds and temperature zero do not guarantee hardware-level determinism.",
            "Supervisor timeout is proposed, not installed; it does not stop instance billing.",
        ],
    }


def load():
    source_plan = pilot.load_plan()
    require(sha(encoded(source_plan)) == PLAN, "Frozen first-pilot input plan changed")
    result = construct(source_plan)
    root = Path(__file__).resolve().parents[1]
    paths = (Path(__file__), Path(pilot.__file__), Path(legacy.__file__), Path(candidate.__file__),
             root / "scripts/audit_contract_pilot_results.py", root / "scripts/fetch_bird_fixed.py")
    result["preparation_source_sha256"] = {path.resolve().relative_to(root).as_posix():
                                          sha(safe(path).read_bytes()) for path in paths}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="NEW private JSON file; default prints metadata only")
    args = parser.parse_args()
    plan = load()
    if args.output is not None:
        output = safe(args.output)
        require(not output.exists(), "Output exists; never overwrite")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            stream.write(encoded(plan))
    print(json.dumps({"status": plan["status"], "plan_sha256": sha(encoded(plan)),
                      "planned_calls": len(plan["calls"]), "executed_calls": 0,
                      "independent_targets": plan["independent_targets"]}, indent=2))


if __name__ == "__main__":
    main()
