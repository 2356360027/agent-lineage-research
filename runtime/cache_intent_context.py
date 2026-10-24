"""No-truncation context check using an operator-supplied local tokenizer.

Caller verifies tokenizer/template files and serving configuration beforehand.
Digest strings here identify that receipt, not proof of serving equivalence.
No downloads, model loading, network calls or guessed characters/token ratios.
"""
from copy import deepcopy
import re
from runtime.bird_readonly_audit import digest


class ContextCheck:
    def __init__(self, tokenizer, *, max_model_len, tokenizer_sha256, template_sha256):
        if type(max_model_len) is not int or not 1 <= max_model_len <= 1048576:
            raise ValueError("Explicit verified serving context limit required")
        for value in (tokenizer_sha256,template_sha256):
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}",value) is None:
                raise ValueError("Verified tokenizer/template receipt hashes required")
        self.tokenizer = tokenizer
        self.limit = max_model_len
        self.tokenizer_sha256 = tokenizer_sha256
        self.template_sha256 = template_sha256

    def __call__(self, payload):
        messages = deepcopy(payload["messages"])
        maximum = payload["max_tokens"]
        if type(maximum) is not int or maximum <= 0:
            raise ValueError("Positive completion reservation required")
        ids = self.tokenizer.apply_chat_template(messages, tokenize=True,
                   add_generation_prompt=True, truncation=False)
        if type(ids) is not list or not ids or any(type(i) is not int or i < 0 for i in ids):
            raise ValueError("Expected flat nonempty token ID list")
        count = len(ids)
        return {"payload_sha256":digest(payload), "prompt_tokens_local":count,
                "reserved_completion_tokens":maximum, "max_model_len":self.limit,
                "fits":count+maximum <= self.limit, "truncated":False,
                "tokenizer_sha256":self.tokenizer_sha256,
                "template_sha256":self.template_sha256,
                "provider_count_verified":False}
