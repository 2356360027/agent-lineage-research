"""Offline candidate: expose existing schema descriptions in the system message.

Not deployed, not an execution authorization, and not evidence of effectiveness.
Only a fixed heading and the legacy eight field descriptions are appended. The
legacy module, public data packet, response schema, tools and costs stay intact.
No inference, SQL, network, semantic approval or target-specific advice occurs.
"""
from __future__ import annotations

from copy import deepcopy

from runtime import cache_intent_prompts as legacy

VERSION = "cache-intent-visible-field-definitions-v2-candidate"
STATUS = "CANDIDATE_NOT_DEPLOYED_EFFECTIVENESS_UNTESTED"
DEFINITION_HEADING = "Contract field definitions:"
DEFINITION_SUFFIX = "\n\n" + DEFINITION_HEADING + "\n" + "\n".join(
    f"{field}: {legacy._DESCRIPTIONS[field]}" for field in legacy.CONTRACT_FIELDS
)


def _remove_verified_suffix(envelope):
    """Copy and strip only the exact suffix; leave the old prefix for validation."""
    if type(envelope) is not dict:
        raise ValueError("Each envelope must be an object")
    messages = envelope.get("messages")
    if (type(messages) is not list or len(messages) != 2 or
            type(messages[0]) is not dict or
            not isinstance(messages[0].get("content"), str)):
        raise ValueError("Expected two messages including a text system message")
    content = messages[0]["content"]
    if not content.endswith(DEFINITION_SUFFIX):
        raise ValueError("Missing or changed fixed field-definition suffix")
    copied = deepcopy(envelope)
    copied["messages"][0]["content"] = content[:-len(DEFINITION_SUFFIX)]
    return copied


def assert_pair(left, right, factor="availability"):
    """Validate the candidate pair, including identical unauthorized arm edits.

    Never replace the old system content wholesale: preserving that prefix
    allows the legacy validator to detect changes common to both arms.
    """
    original_left = _remove_verified_suffix(left)
    original_right = _remove_verified_suffix(right)
    legacy.assert_pair(original_left, original_right, factor=factor)


def make_pair(current_request: str, evidence: str | None, schema: list[str],
              history: dict, database_sha256: str, factor="availability"):
    """Build fresh ordered candidate envelopes; only the system suffix differs."""
    original = legacy.make_pair(current_request, evidence, schema, history,
                                database_sha256, factor=factor)
    pair = tuple(deepcopy(envelope) for envelope in original)
    for envelope in pair:
        envelope["messages"][0]["content"] += DEFINITION_SUFFIX
    assert_pair(*pair, factor=factor)
    return pair
