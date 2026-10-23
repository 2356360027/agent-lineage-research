"""Capacity arithmetic and rejection tests, with a synthetic byte counter."""
from copy import deepcopy
import json
import unittest
from runtime.cache_intent_capacity import RecoveryCapacity,frame,PREFIX
from runtime import cache_intent_recovery_preview as preview


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.envelope=preview.make_pair("List IDs",None,["CREATE TABLE x(id TEXT)"],
            {"historical_request":"List IDs","sql":"SELECT id FROM x"},"a"*64)[0]
        self.text=lambda s:len(s.encode())
        self.chat=lambda ms:sum(self.text(frame(m["role"],m["content"])) for m in ms)+self.text(PREFIX)
        self.args=dict(max_model_len=32768,tokenizer_sha256="a"*64,template_sha256="b"*64)
        self.cap=RecoveryCapacity(self.envelope,self.chat,self.text,**self.args)
        self.payload={"messages":deepcopy(self.envelope["messages"]),"max_tokens":512}

    def test_initial_and_maximum_counts_satisfy_derived_envelope(self):
        self.assertTrue(self.cap(self.payload)["fits"])
        for s in self.cap.fixed: self.payload["messages"].append({"role":"user","content":s})
        self.payload["messages"] += [{"role":"assistant","content":"x"*490} for _ in range(22)]
        self.payload["messages"] += [{"role":"user","content":json.dumps({"tool_observation":{}})} for _ in range(21)]
        r=self.cap(self.payload)
        self.assertLessEqual(r["prompt_tokens_local"]+512,r["full_history_bound"])

    def test_insufficient_window_rejected_before_use(self):
        with self.assertRaises(ValueError):
            RecoveryCapacity(self.envelope,self.chat,self.text,**(self.args|{"max_model_len":4096}))

    def test_nonadditive_counter_rejected(self):
        other=RecoveryCapacity(self.envelope,lambda ms:self.chat(ms)+1,self.text,**self.args)
        with self.assertRaises(ValueError): other(self.payload)

    def test_input_or_completion_cap_change_rejected(self):
        with self.assertRaises(ValueError): self.cap(self.payload|{"max_tokens":256})
        self.payload["messages"][0]["content"]+="extra"
        with self.assertRaises(ValueError): self.cap(self.payload)

    def test_oversize_messages_extra_roles_and_duplicate_instruction_rejected(self):
        for messages in ([{"role":"assistant","content":"x"*529}],
                         [{"role":"user","content":json.dumps({"tool_observation":"x"*512})}],
                         [{"role":"system","content":"extra"}],
                         [{"role":"user","content":self.cap.fixed[0]}]*2,
                         [{"role":"assistant","content":"x"}]*23,
                         [{"role":"user","content":json.dumps({"tool_observation":{}})}]*22):
            with self.subTest(messages=len(messages)):
                with self.assertRaises(ValueError): self.cap(self.payload|{"messages":self.payload["messages"]+messages})


if __name__=="__main__": unittest.main()
