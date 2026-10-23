"""Explicit v2 result admission, not lossy row truncation or semantic repair."""
from copy import deepcopy
import json
from runtime.bird_readonly_audit import digest


def serialize(observation):
    return json.dumps({"tool_observation":observation},ensure_ascii=False,allow_nan=False)


class ObservationBound:
    """count_chat must be the verified full plain-chat tokenizer counter.

    The 512 limit includes an empty-system/user framing and assistant prefix.
    It does not prove an additive upper bound for complete multi-turn prompts.
    Entire raw observations must be journaled before calling this component.
    """
    def __init__(self,count_chat):
        if not callable(count_chat):
            raise TypeError("Verified chat counter required")
        self.count_chat = count_chat

    def count(self,observation):
        value = self.count_chat([{"role":"system","content":""},
                                 {"role":"user","content":serialize(observation)}])
        if type(value) is not int or value <= 0:
            raise ValueError("Invalid observation token count")
        return value

    def apply(self,observation):
        original = deepcopy(observation)
        original_tokens = self.count(original)
        exposed = original
        if original_tokens > 512:
            exposed = {"status":"OBSERVATION_TOO_LARGE","complete":False,
                "charged_units":original["charged_units"],
                "remaining_budget":original["remaining_budget"],"attempt":original["attempt"]}
            # Fatal environmental invalidity must never be hidden as overflow.
            if original.get("status") == "ENVIRONMENT_INVALID":
                exposed["status"] = "ENVIRONMENT_INVALID"
        exposed_tokens = self.count(exposed)
        if exposed_tokens > 512:
            raise ValueError("Even the explicit incomplete status exceeds the bound")
        return {"observation":exposed,"raw_observation_sha256":digest(observation),
                "raw_framed_tokens":original_tokens,"exposed_framed_tokens":exposed_tokens,
                "bound":512,"rows_partially_exposed":False}
