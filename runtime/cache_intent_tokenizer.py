"""Pinned local plain-chat counters. No model loading, HTTP or downloads.

Provider token parity is still checked on each actual request by the adapter.
These counters alone do not prove the service uses these tokenizer bytes.
"""
import hashlib
from pathlib import Path
from runtime.cache_intent_deployment import TOKENIZER_ASSETS
from runtime.cache_intent_entry import pinned_bytes
from runtime.cache_intent_capacity import frame,PREFIX
from scripts.fetch_bird_fixed import strict_json


def local_counters(model_path,*,expected_assets,expected_template_sha256,expected_versions):
    if set(expected_assets)!=TOKENIZER_ASSETS:
        raise ValueError("Exactly five pinned tokenizer/config assets required")
    if set(expected_versions)!={"tokenizers","jinja2"}:
        raise ValueError("Both tokenizer and renderer versions must be pinned")
    root=Path(model_path)
    if not root.is_absolute(): raise ValueError("Absolute existing local model directory required")
    raw={name:pinned_bytes(root/name,sha) for name,sha in expected_assets.items()}
    template=strict_json(raw["tokenizer_config.json"].decode("utf-8"))["chat_template"]
    if (type(template) is not str or
            hashlib.sha256(template.encode("utf-8")).hexdigest()!=expected_template_sha256):
        raise ValueError("Pinned template mismatch")
    import tokenizers
    import jinja2
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    versions={"tokenizers":tokenizers.__version__,"jinja2":jinja2.__version__}
    if versions!=expected_versions: raise ValueError("Tokenizer or renderer version changed")
    # Construct from the bytes already verified, not a second filesystem read.
    tokenizer=tokenizers.Tokenizer.from_str(raw["tokenizer.json"].decode("utf-8"))
    tokenizer.no_truncation(); tokenizer.no_padding()
    renderer=ImmutableSandboxedEnvironment(trim_blocks=True,lstrip_blocks=True).from_string(template)

    def count_text(text):
        if type(text) is not str: raise TypeError("Raw text required")
        return len(tokenizer.encode(text,add_special_tokens=False).ids)

    def count_chat(messages):
        if (type(messages) is not list or not messages or any(type(m) is not dict
                or set(m)!={"role","content"} or m["role"] not in ("system","user","assistant")
                or type(m["content"]) is not str for m in messages)
                or messages[0]["role"]!="system"):
            raise ValueError("Only the pinned plain-role chat branch is supported")
        rendered=renderer.render(messages=messages,tools=None,add_generation_prompt=True)
        independent="".join(frame(m["role"],m["content"]) for m in messages)+PREFIX
        if rendered!=independent: raise ValueError("Actual template differs from capacity framing")
        return count_text(rendered)

    return count_chat,count_text,dict(tokenizer_files_sha256=dict(expected_assets),
        template_sha256_utf8=expected_template_sha256,versions=versions,
        construction="VERIFIED_IN_MEMORY_LOCAL_BYTES",truncation=False,padding=False,
        model_loaded=False,provider_token_parity_verified=False)
