"""Fake-tokenizer tests only; no real token counts or weights claimed."""
from copy import deepcopy
import unittest
from runtime.cache_intent_context import ContextCheck


class FakeTokenizer:
    def __init__(self, n): self.n = n
    def apply_chat_template(self,messages,**kwargs):
        assert kwargs == dict(tokenize=True,add_generation_prompt=True,truncation=False)
        messages[0]["content"] = "fixture mutates its own copy"
        return list(range(self.n))


class ContextTests(unittest.TestCase):
    def payload(self): return dict(messages=[{"role":"user","content":"entire fixture"}],max_tokens=10)
    def checker(self,n): return ContextCheck(FakeTokenizer(n),max_model_len=20,
                                            tokenizer_sha256="a"*64,template_sha256="b"*64)
    def test_exact_fit(self): self.assertTrue(self.checker(10)(self.payload())["fits"])
    def test_one_token_over(self): self.assertFalse(self.checker(11)(self.payload())["fits"])
    def test_full_original_preserved(self):
        payload = self.payload(); before = deepcopy(payload)
        self.checker(5)(payload)
        self.assertEqual(payload,before)
    def test_empty_tokens_rejected(self):
        with self.assertRaises(ValueError): self.checker(0)(self.payload())
    def test_no_implicit_context_or_hash(self):
        with self.assertRaises(ValueError):
            ContextCheck(FakeTokenizer(1),max_model_len=True,tokenizer_sha256="unknown",template_sha256="b"*64)


if __name__ == "__main__": unittest.main()
