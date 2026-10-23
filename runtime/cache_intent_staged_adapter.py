"""Bounded localhost adapter; no CLI or authorization implied by construction.

Caller must reserve/fingerprint the complete source tree, verify the native
model and obtain batch authorization. Transport reuses the existing bounded
subprocess, proxy-free, redirect-denying client without modifying old studies.
"""
from copy import deepcopy
from pathlib import Path
import time

from runtime import cache_intent_contract_pilot as client
from runtime import cache_intent_prompts as prompts


def response_format(stage):
    if stage != "contract":
        return {"type":"json_object"}
    return {"type":"json_schema", "json_schema":{
        "name":"observable_task_contract", "strict":True,
        "schema":{"type":"object", "properties":{
            key:{"type":"string", "description":prompts._DESCRIPTIONS[key]}
            for key in prompts.CONTRACT_FIELDS},
            "required":list(prompts.CONTRACT_FIELDS), "additionalProperties":False}}}


class Adapter:
    def __init__(self, *, endpoint, model, seed, max_tokens, max_seconds,
                 directory, save, context_check, transport=None, clock=time.monotonic,
                 protocol="staged-v1", text_count=None):
        client.endpoint_url(endpoint)
        if type(model) is not str or not model.strip():
            raise ValueError("Explicit model ID required")
        if type(seed) is not int or not 0 <= seed < 2**31:
            raise ValueError("Invalid paired seed")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 2048:
            raise ValueError("Explicit completion cap 1..2048 required")
        if type(max_seconds) is not int or not 1 <= max_seconds <= 1800:
            raise ValueError("Explicit trajectory deadline 1..1800 seconds required")
        if not callable(context_check):
            raise ValueError("Context preflight is required")
        if protocol not in ("staged-v1","recovery-v2"):
            raise ValueError("Unknown adapter protocol")
        if protocol == "recovery-v2" and (max_tokens != 512 or not callable(text_count)):
            raise ValueError("Recovery v2 requires a 512 cap and verified raw-text counter")
        self.protocol, self.text_count = protocol, text_count
        self.max_calls = 23 if protocol == "recovery-v2" else 22
        self.context_check = context_check
        self.endpoint, self.model, self.seed = endpoint, model, seed
        self.max_tokens, self.max_seconds = max_tokens, max_seconds
        self.directory, self.save = Path(directory), save
        self.transport = client.request_once if transport is None else transport
        self.clock, self.start = clock, clock()
        self.count, self.failed = 0, False

    def __call__(self, request):
        if self.failed or self.count >= self.max_calls:
            raise RuntimeError("Adapter terminal; no retry or extra calls")
        try:
            return self._call(request)
        except BaseException:
            self.failed = True
            raise

    def _call(self, request):
        index = self.count + 1
        if (type(request) is not dict or set(request) !=
                {"generation_id", "stage", "messages", "contract_event"}
                or request["generation_id"] != f"g{index:03d}"):
            raise ValueError("Invalid generation sequence")
        expected_stage = ("contract" if index == 1 else "terminal" if index == self.max_calls
                          else "recovery" if self.protocol == "recovery-v2" and index == 22 else "action")
        if (request["stage"] != expected_stage or
                request["contract_event"] != (None if index == 1 else "g001")):
            raise ValueError("Invalid stage/contract linkage")
        messages = request["messages"]
        if (type(messages) is not list or not messages or any(
            type(m) is not dict or set(m) != {"role", "content"}
            or m["role"] not in ("system", "user", "assistant")
            or type(m["content"]) is not str for m in messages)):
            raise ValueError("Invalid messages")
        remaining = self.max_seconds - (self.clock() - self.start)
        if remaining <= 0:
            raise TimeoutError("Trajectory deadline before dispatch")
        payload = {"model":self.model, "messages":deepcopy(messages), "seed":self.seed,
                   "temperature":0, "top_p":1, "max_tokens":self.max_tokens,
                   "stream":False, "response_format":response_format(request["stage"])}
        if len(client.encoded(payload)) > 4*1024*1024:
            raise ValueError("Payload exceeds transport cap; no truncation")
        name = f"http-g{index:03d}"
        context = self.context_check(deepcopy(payload))
        self.save(name + ".context", context)
        if (type(context) is not dict or context.get("payload_sha256") != client.sha(client.encoded(payload))
                or context.get("truncated") is not False or context.get("fits") is not True
                or type(context.get("prompt_tokens_local")) is not int
                or context["prompt_tokens_local"] <= 0
                or context.get("reserved_completion_tokens") != self.max_tokens
                or type(context.get("max_model_len")) is not int
                or context["prompt_tokens_local"]+self.max_tokens > context["max_model_len"]):
            raise ValueError("Context preflight failed; retain full prompt, never truncate")
        remaining = self.max_seconds - (self.clock() - self.start)
        if remaining <= 0:
            raise TimeoutError("Deadline during context preflight")
        self.save(name + ".pending", {"payload":payload, "timeout_seconds":min(180,remaining),
                  "generation_id":request["generation_id"], "automatic_retry":False})
        self.count = index
        raw = self.transport(self.directory/name, self.endpoint, "POST", min(180,remaining),
                             request=deepcopy(payload))
        # Save raw provider JSON before any parsing/model-ID checks.
        self.save(name + ".provider", {"raw":deepcopy(raw)})
        if self.clock() - self.start >= self.max_seconds:
            raise TimeoutError("Late response retained, no subsequent action")
        if type(raw) is not dict or raw.get("model") != self.model:
            raise ValueError("Provider model ID mismatch")
        usage = client.valid_usage(raw)
        if usage is not None and usage["prompt_tokens"] != context["prompt_tokens_local"]:
            raise ValueError("Provider/local token count mismatch; do not continue")
        choices = raw.get("choices")
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ValueError("Expected exactly one choice")
        choice = choices[0]
        message = choice.get("message")
        if (type(message) is not dict or message.get("tool_calls") or
                message.get("function_call") or message.get("refusal") or
                type(message.get("content")) is not str):
            raise ValueError("Unexpected provider message")
        if self.protocol == "recovery-v2":
            local_tokens = self.text_count(message["content"])
            self.save(name+".completion_context", {
                "content_sha256":client.sha(message["content"].encode("utf-8")),
                "raw_text_tokens_local":local_tokens,"cap":self.max_tokens,
                "provider_completion_tokens":None if usage is None else usage["completion_tokens"],
                "retokenization_is_not_provider_usage":True})
            if (type(local_tokens) is not int or not 0 <= local_tokens <= self.max_tokens
                    or (usage is not None and usage["completion_tokens"] > self.max_tokens)):
                raise ValueError("Recovery output exceeds its cap or counter is invalid; no truncation")
        # Preserve non-stop output. The loop records it as invalid, never repairs it.
        finish = choice.get("finish_reason")
        if finish != "stop":
            self.failed = True
        return {"content":message["content"], "finish_reason":finish,
                "usage":usage, "provider_model":raw["model"],
                "provider_record":name + ".provider", "request_sha256":client.sha(client.encoded(payload))}
