"""Local asset gates with fake tokenizer/renderer; not actual model tokens."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch
from runtime import cache_intent_tokenizer as counters
from runtime.cache_intent_capacity import frame,PREFIX


class CounterTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name); self.template="synthetic template"
        self.assets={}
        for name in counters.TOKENIZER_ASSETS:
            raw=json.dumps({"chat_template":self.template} if name=="tokenizer_config.json" else {}).encode()
            (self.root/name).write_bytes(raw); self.assets[name]=hashlib.sha256(raw).hexdigest()
        self.args=dict(expected_assets=self.assets,
            expected_template_sha256=hashlib.sha256(self.template.encode()).hexdigest(),
            expected_versions={"tokenizers":"fixture-1","jinja2":"fixture-2"})
        self.tokenizer=Mock()
        self.tokenizer.encode.side_effect=lambda text,**kw:SimpleNamespace(ids=list(text.encode()))
        self.backend=SimpleNamespace(__version__="fixture-1",Tokenizer=Mock())
        self.backend.Tokenizer.from_str.return_value=self.tokenizer
        self.renderer=Mock()
        self.renderer.render.side_effect=lambda messages,**kw:"".join(frame(m["role"],m["content"]) for m in messages)+PREFIX
        self.environment=Mock(); self.environment.return_value.from_string.return_value=self.renderer
        self.modules={"tokenizers":self.backend,"jinja2":SimpleNamespace(__version__="fixture-2"),
                      "jinja2.sandbox":SimpleNamespace(ImmutableSandboxedEnvironment=self.environment)}

    def load(self):
        with patch.dict("sys.modules",self.modules):
            return counters.local_counters(self.root,**self.args)

    def test_construct_verified_bytes_no_truncation_or_padding(self):
        chat,text,receipt=self.load()
        self.backend.Tokenizer.from_str.assert_called_once_with("{}")
        self.tokenizer.no_truncation.assert_called_once(); self.tokenizer.no_padding.assert_called_once()
        messages=[dict(role="system",content=""),dict(role="user",content="fixture")]
        self.assertEqual(chat(messages),text("".join(frame(m["role"],m["content"]) for m in messages)+PREFIX))
        self.assertFalse(receipt["provider_token_parity_verified"])

    def test_asset_change_rejected_before_backend_construction(self):
        (self.root/"tokenizer.json").write_text("changed")
        with self.assertRaises(ValueError): self.load()
        self.backend.Tokenizer.from_str.assert_not_called()

    def test_template_and_version_changes_rejected(self):
        self.args["expected_template_sha256"]="0"*64
        with self.assertRaises(ValueError): self.load()
        self.args["expected_template_sha256"]=hashlib.sha256(self.template.encode()).hexdigest()
        self.args["expected_versions"]["tokenizers"]="different"
        with self.assertRaises(ValueError): self.load()

    def test_renderer_must_match_actual_capacity_framing(self):
        chat,_,_=self.load()
        self.renderer.render.side_effect=None; self.renderer.render.return_value="different"
        with self.assertRaises(ValueError): chat([dict(role="system",content="fixture")])

    def test_unsupported_message_branches_rejected(self):
        chat,text,_=self.load()
        for messages in ([],[dict(role="user",content="x")],
                         [dict(role="system",content="x"),dict(role="tool",content="x")]):
            with self.assertRaises(ValueError): chat(messages)
        with self.assertRaises(TypeError): text(None)


if __name__=="__main__": unittest.main()
