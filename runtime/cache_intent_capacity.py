"""Conditional full-history capacity certificate for the pinned Qwen framing.

Not model/serving validation. Caller verifies archived assets and current
provider separately. Every actual prompt must satisfy exact token additivity;
no truncation, compression, or discarded history is used to make it fit.
"""
from copy import deepcopy
import json
from runtime import cache_intent_recovery_preview as preview
from runtime.cache_intent_staged_loop import ACTION_INSTRUCTION, RECOVERY_INSTRUCTION, TERMINAL_INSTRUCTION
from runtime.cache_intent_prompts import CONTRACT_FIELDS
from runtime.bird_readonly_audit import digest

ASSISTANT_FRAME_CAP=528
OBSERVATION_FRAME_CAP=512
COMPLETION_CAP=512
PREFIX="<|im_start|>assistant\n"


def frame(role,text):
    return "<|im_start|>"+role+"\n"+text+"<|im_end|>\n"


class RecoveryCapacity:
    def __init__(self,envelope,count_chat,count_text,*,max_model_len,tokenizer_sha256,template_sha256):
        preview.validate_preview(envelope)
        if type(max_model_len) is not int or max_model_len<=0: raise ValueError("Explicit window required")
        self.initial=deepcopy(envelope["messages"])
        self.chat,self.text=count_chat,count_text
        self.limit=max_model_len
        self.tokenizer_sha256,self.template_sha256=tokenizer_sha256,template_sha256
        # Placeholder used ONLY to recover fixed instruction text, not sent.
        placeholder=json.dumps(dict.fromkeys(CONTRACT_FIELDS,"fixture"))
        stage2=preview.next_generation_messages(envelope,placeholder)[-1]["content"]+"\n"+ACTION_INSTRUCTION
        self.fixed=(stage2,RECOVERY_INSTRUCTION,TERMINAL_INSTRUCTION)
        fixed_cost=sum(self.tokens(frame(m["role"],m["content"])) for m in self.initial)
        fixed_cost+=sum(self.tokens(frame("user",s)) for s in self.fixed)+self.tokens(PREFIX)
        self.bound=fixed_cost+22*ASSISTANT_FRAME_CAP+21*OBSERVATION_FRAME_CAP+COMPLETION_CAP
        if self.bound>self.limit: raise ValueError("Full permitted-history envelope exceeds window")

    def tokens(self,text):
        n=self.text(text)
        if type(n) is not int or n<0: raise ValueError("Invalid token counter")
        return n

    def __call__(self,payload):
        messages=payload["messages"]
        if payload["max_tokens"]!=COMPLETION_CAP or messages[:2]!=self.initial:
            raise ValueError("Capacity profile/input changed")
        assistant=observations=0
        seen=set()
        for m in messages[2:]:
            if set(m)!={"role","content"} or type(m["content"]) is not str:
                raise ValueError("Invalid chat message")
            size=self.tokens(frame(m["role"],m["content"]))
            if m["role"]=="assistant":
                assistant+=1
                if size>ASSISTANT_FRAME_CAP: raise ValueError("Assistant frame too large")
            elif m["role"]=="user" and m["content"] in self.fixed:
                if m["content"] in seen: raise ValueError("Repeated fixed instruction")
                seen.add(m["content"])
            elif m["role"]=="user":
                value=json.loads(m["content"])
                if type(value) is not dict or set(value)!={"tool_observation"}:
                    raise ValueError("Unexpected user message")
                observations+=1
                if size>OBSERVATION_FRAME_CAP: raise ValueError("Observation frame too large")
            else:
                raise ValueError("Unexpected role")
        if assistant>22 or observations>21: raise ValueError("History exceeds fixed horizon")
        composed=sum(self.tokens(frame(m["role"],m["content"])) for m in messages)+self.tokens(PREFIX)
        actual=self.chat(messages)
        if type(actual) is not int or actual<=0 or actual!=composed:
            raise ValueError("Tokenizer/template violates framed additivity; retain failure")
        if actual+COMPLETION_CAP>self.bound:
            raise AssertionError("Capacity arithmetic violated")
        return dict(payload_sha256=digest(payload),prompt_tokens_local=actual,
            reserved_completion_tokens=COMPLETION_CAP,max_model_len=self.limit,fits=True,truncated=False,
            tokenizer_sha256=self.tokenizer_sha256,template_sha256=self.template_sha256,
            full_history_bound=self.bound,framed_additivity_verified_for_this_prompt=True,
            provider_count_verified=False)
