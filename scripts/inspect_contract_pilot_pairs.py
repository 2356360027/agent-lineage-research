"""Offline post-outcome inspection, NOT a semantic scorer or inference runner.

Requires the first pilot's preserved artifacts. Writes a NEW private report only;
does not alter raw data, infer intent-drift labels, run SQL, or contact a server.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import json
from pathlib import Path
import re

from scripts.audit_contract_pilot_results import (
    PLAN, encoded, read_json, require, safe, sha, verify_hashes,
)


def parse_received(line):
    """Parse logged Python literals/AST without executing logged expressions."""
    match = re.search(r"Received request ([^ :]+): prompt: (.*)$", line)
    if not match:
        return None
    prompt_repr, sep, tail = match[2].rpartition(", params: SamplingParams(")
    require(bool(sep), "Unrecognized received-request log format")
    prompt = ast.literal_eval(prompt_repr)
    require(type(prompt) is str, "Logged prompt is not a string")
    params, sep, _ = tail.rpartition("), prompt_token_ids:")
    require(bool(sep), "Missing sampling parameter terminator")
    node = ast.parse("SamplingParams(" + params + ")", mode="eval").body
    require(isinstance(node, ast.Call), "Invalid sampling parameters")
    guided = [k.value for k in node.keywords if k.arg == "guided_decoding"]
    require(len(guided) == 1 and isinstance(guided[0], ast.Call), "Missing guided schema")
    schemas = [k.value for k in guided[0].keywords if k.arg == "json"]
    require(len(schemas) == 1, "Missing JSON schema")
    schema = ast.literal_eval(schemas[0])
    require(type(schema) is dict, "Guided schema is not an object")
    return match[1], prompt, schema


def qwen_prompt(messages):
    require([m.get("role") for m in messages] == ["system", "user"], "Unexpected roles")
    return "".join("<|im_start|>" + m["role"] + "\n" + m["content"] +
                   "<|im_end|>\n" for m in messages) + "<|im_start|>assistant\n"


def compare_pairs(calls, records):
    require(len(calls) == len(records), "Missing records")
    groups, envelopes = defaultdict(dict), defaultdict(list)
    for index, (call, record) in enumerate(zip(calls, records)):
        require(record["index"] == index and record["status"] == "format_valid", "Invalid record")
        key = (call["history_id"], call["factor"])
        arm = call["arm"]
        require(type(arm) is int and arm in (0, 1) and arm not in groups[key], "Duplicate/invalid arm")
        groups[key][arm] = index
        envelopes[sha(encoded(call["envelope"]))].append(index)
    pairs = []
    for (history, factor), arms in sorted(groups.items()):
        require(set(arms) == {0, 1}, "Incomplete pair")
        low, high = (records[arms[a]]["contract"] for a in (0, 1))
        require(set(low) == set(high), "Contract fields differ")
        diffs = {field: [low[field], high[field]] for field in sorted(low) if low[field] != high[field]}
        pairs.append({"history_id": history, "factor": factor,
                      "call_indices_arm_0_then_1": [arms[0], arms[1]],
                      "string_differences": diffs, "semantic_label": None})
    duplicates = [{"envelope_sha256": digest, "call_indices": indices,
                   "seeds": [calls[i]["seed"] for i in indices],
                   "all_contracts_exactly_equal": len({encoded(records[i]["contract"]) for i in indices}) == 1}
                  for digest, indices in sorted(envelopes.items()) if len(indices) > 1]
    return {"pairs": pairs, "pair_count": len(pairs),
            "pairs_with_string_differences": sum(bool(p["string_differences"]) for p in pairs),
            "different_field_slots": sum(len(p["string_differences"]) for p in pairs),
            "unique_envelopes": len(envelopes),
            "unique_contract_objects": len({encoded(r["contract"]) for r in records}),
            "duplicate_envelopes_not_full_request_replicates": duplicates}


def verify_call_binding(call, request, record, index, max_tokens):
    expected = call["envelope"] | {
        "model": "Qwen/Qwen2.5-7B-Instruct", "seed": call["seed"], "temperature": 0,
        "top_p": 1, "max_tokens": max_tokens, "stream": False,
    }
    require(encoded(request) == encoded(expected), "Saved request differs from frozen plan")
    expected_meta = {k: call[k] for k in ("history_id", "factor", "arm", "seed")}
    expected_meta.update(index=index, semantic_status="UNREVIEWED")
    require(encoded({k: record.get(k) for k in expected_meta}) == encoded(expected_meta),
            "Assessment metadata differs from frozen plan")


def inspect(run):
    run = safe(run)
    verify_hashes(run, "SUPERVISOR_HASHES.json")
    exp = run / "experiment"
    verify_hashes(exp, "artifact_hashes.json")
    plan_path = exp / "plan.json"
    require(sha(plan_path.read_bytes()) == PLAN, "Not the frozen first pilot")
    plan = read_json(plan_path)
    calls = plan["calls"]
    records = [read_json(exp / f"assessment-{i:03d}.json") for i in range(len(calls))]
    requests, by_id = [], {}
    for i in range(len(calls)):
        request = read_json(exp / f"call-{i:03d}/request.json")
        response = read_json(exp / f"call-{i:03d}/response.raw")
        verify_call_binding(calls[i], request, records[i], i, plan["max_completion_tokens_per_call"])
        require(response["model"] == request["model"], "Response model ID differs from request")
        require(response["id"] not in by_id, "Duplicate response ID")
        require(json.loads(response["choices"][0]["message"]["content"]) == records[i]["contract"],
                "Contract differs from raw response")
        by_id[response["id"]] = i
        requests.append(request)
    log_path = run / "server-startup.log"
    rows, seen = [], set()
    for lineno, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), 1):
        parsed = parse_received(line)
        if parsed is None:
            continue
        request_id, prompt, schema = parsed
        require(request_id in by_id and request_id not in seen, "Extra or duplicate logged request")
        seen.add(request_id)
        index = by_id[request_id]
        request = requests[index]
        expected = request["response_format"]["json_schema"]["schema"]
        require(schema == expected, "Logged guided schema differs from saved request")
        require(prompt == qwen_prompt(request["messages"]), "Logged text differs from submitted messages")
        rows.append({"call_index": index, "line": lineno, "request_id": request_id,
                     "prompt_utf8_bytes": len(prompt.encode("utf-8")),
                     "prompt_sha256": sha(prompt.encode("utf-8")),
                     "full_definition_present_in_text": {
                         field: value["description"] in prompt for field, value in expected["properties"].items()}})
    require(seen == set(by_id), "Missing logged prompts")
    return {"status": "POST_OUTCOME_EXPLORATORY_INTERFACE_AUDIT", "semantic_scoring_performed": False,
            "human_approvals": 0, "intent_drift_event": None, "later_program_event": None,
            "causal_explanation_established": False, "independent_targets": plan["independent_targets"],
            "plan_sha256": PLAN, "log_sha256": sha(log_path.read_bytes()),
            "log_bytes": log_path.stat().st_size,
            "logged_prompts": len(rows), "logged_guided_schemas_match_saved_requests": len(rows),
            "definition_in_text_counts": {field: sum(r["full_definition_present_in_text"][field] for r in rows)
                                          for field in rows[0]["full_definition_present_in_text"]},
            "prompt_checks": rows, "string_comparison": compare_pairs(calls, records),
            "limitations": ["String differences are not semantic errors or intent drift.",
                            "Missing text definitions do not prove the cause of field-placement errors.",
                            "Logged text and schema metadata do not attest all backend internals.",
                            "No later SQL/program/cache use or final answer was observed.",
                            "Post-outcome inspection; not a preregistered effect test."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run, output = safe(args.run), safe(args.output)
    require(not output.exists() and not output.is_relative_to(run), "Output exists or would alter raw run")
    result = inspect(run)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(encoded(result))
    print(json.dumps({k: result[k] for k in ("status", "logged_prompts", "definition_in_text_counts")}))
    print(json.dumps({k: v for k, v in result["string_comparison"].items()
                      if k not in ("pairs", "duplicate_envelopes_not_full_request_replicates")}))


if __name__ == "__main__":
    main()
