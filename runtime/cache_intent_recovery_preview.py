"""Explicit v2 environment preview; not a method or authorization to infer."""
from copy import deepcopy
import json
from runtime import cache_intent_staged_preview as v1
from runtime import cache_intent_prompts as legacy
from runtime import cache_intent_tools as tools

VERSION = "instrumented-cost-recovery-preview-v2"
SYSTEM = v1.SYSTEM.replace(
    "The common budget\nallows every admitted action within the 20-attempt horizon in either condition.",
    "Both conditions have 200 exploration cost units for at most 20 tool attempts,\n"
    "plus a separate reserved 10 units for one final recovery SQL attempt. After\n"
    "exploration you may answer, abstain, or write one read-only SQL query; after\n"
    "that query only answer or abstain. No reference solution is supplied.\n"
    "Complete tool observations exceeding the common 512-token exposure bound\n"
    "are withheld with an explicit incomplete status, never partially shown.")


def make_pair(current_request, evidence, schema, history, database_sha256):
    pair = deepcopy(v1.make_pair(current_request,evidence,schema,history,database_sha256))
    for envelope in pair:
        packet = json.loads(envelope["messages"][1]["content"])
        packet["context"].update(budget=210, budget_profile=tools.RECOVERY_PROFILE,
            max_attempts=21, exploration_attempts=20, exploration_allowance=200,
            recovery_allowance=10, last_attempt_sql_only=True,
            observation_policy="complete-or-explicit-oversize-v2", observation_token_bound=512)
        envelope["messages"][0]["content"] = SYSTEM
        envelope["messages"][1]["content"] = legacy._json(packet)
    return pair


def validate_preview(envelope):
    try:
        packet = json.loads(envelope["messages"][1]["content"])
        pair = make_pair(packet["current_request"],packet["evidence"],packet["schema"],
                         packet["history"],packet["context"]["database_sha256"])
        if envelope not in pair:
            raise ValueError("Noncanonical recovery preview")
        return packet
    except (KeyError,TypeError,IndexError,json.JSONDecodeError) as exc:
        raise ValueError("Invalid recovery preview") from exc


def assert_initial_session(envelope,state):
    context = validate_preview(envelope)["context"]
    expected = dict(context["cache"], sql_call_cost=10, budget_profile=tools.RECOVERY_PROFILE,
        nonbinding_for_configured_horizon=True,initial_budget=210,remaining_budget=210,
        max_attempts=21,attempts_used=0,cost_basis="declared_relative_units",
        database_sha256=context["database_sha256"],exploration_attempts=20,
        exploration_allowance=200,recovery_allowance=10,last_attempt_sql_only=True)
    if legacy._json(state) != legacy._json(expected):
        raise ValueError("Recovery preview/session mismatch")


def next_generation_messages(envelope,contract_text):
    packet = validate_preview(envelope)
    old = v1.make_pair(packet["current_request"],packet["evidence"],packet["schema"],
                      packet["history"],packet["context"]["database_sha256"])[0]
    # Reuse only contract validation and the two follow-up messages; never send
    # the legacy price/context or rewrite the raw contract in the v2 trajectory.
    tail = v1.next_generation_messages(old,contract_text)[-2:]
    return deepcopy(envelope["messages"]) + tail
