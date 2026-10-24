"""Adapter tests with injected transport; zero HTTP/model/database execution."""
from copy import deepcopy
import unittest
from runtime.cache_intent_staged_adapter import Adapter
from runtime import cache_intent_staged_preview as preview
from runtime.bird_readonly_audit import digest


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.time = 0
        self.saved, self.sent = {}, []
        self.response = {"model":"fixture", "choices":[{"message":{"content":"{}"},
                         "finish_reason":"stop"}]}
        def transport(*args, **kwargs):
            self.sent.append(deepcopy(kwargs["request"]))
            return deepcopy(self.response)
        self.args = dict(endpoint="http://127.0.0.1:8000/v1", model="fixture", seed=17,
            max_tokens=512, max_seconds=30, directory="not-accessed", save=self.save,
            context_check=lambda p: dict(payload_sha256=digest(p),prompt_tokens_local=12,
                reserved_completion_tokens=512,max_model_len=4096,fits=True,truncated=False),
            transport=transport, clock=lambda:self.time)
        self.request = dict(generation_id="g001", stage="contract", contract_event=None,
                            messages=[{"role":"user", "content":"Synthetic fixture"}])

    def save(self, name, record):
        if name in self.saved: raise FileExistsError(name)
        self.saved[name] = deepcopy(record)

    def test_exact_payload_no_private_wrapper_and_missing_usage_unknown(self):
        result = Adapter(**self.args)(self.request)
        self.assertIsNone(result["usage"])
        self.assertEqual(self.sent[0]["max_tokens"],512)
        self.assertEqual(self.sent[0]["seed"],17)
        canonical = preview.make_pair("fixture",None,["CREATE TABLE t(x TEXT)"],
            {"historical_request":"fixture", "sql":"SELECT x FROM t"}, "a"*64)[0]
        self.assertEqual(self.sent[0]["response_format"],canonical["response_format"])
        self.assertNotIn("generation_id",self.sent[0])
        self.assertEqual(self.saved["http-g001.provider"]["raw"],self.response)

    def test_remote_endpoint_rejected(self):
        for url in ("https://example.org/v1", "http://localhost:8000/v1", "http://u:p@127.0.0.1:8000/v1"):
            with self.assertRaises(ValueError): Adapter(**(self.args | {"endpoint":url}))
        self.assertFalse(self.sent)

    def test_deadline_before_request_no_dispatch(self):
        adapter = Adapter(**self.args)
        self.time = 31
        with self.assertRaises(TimeoutError): adapter(self.request)
        self.assertFalse(self.sent)

    def test_failure_retained_and_no_retry(self):
        self.response["model"] = "wrong"
        adapter = Adapter(**self.args)
        with self.assertRaises(ValueError): adapter(self.request)
        self.assertIn("http-g001.provider",self.saved)
        with self.assertRaises(RuntimeError): adapter(self.request)
        self.assertEqual(len(self.sent),1)

    def test_truncation_not_success_or_repaired(self):
        self.response["choices"][0]["finish_reason"] = "length"
        self.response["usage"] = {"prompt_tokens":12,"completion_tokens":512}
        result = Adapter(**self.args)(self.request)
        self.assertEqual(result["finish_reason"],"length")
        self.assertEqual(result["usage"]["completion_tokens"],512)

    def test_reused_generation_rejected(self):
        adapter = Adapter(**self.args)
        adapter(self.request)
        with self.assertRaises(ValueError): adapter(self.request)
        self.assertEqual(len(self.sent),1)

    def test_late_response_preserved_not_used(self):
        def late(*args, **kwargs):
            self.time = 31
            return self.response
        adapter = Adapter(**(self.args | {"transport":late}))
        with self.assertRaises(TimeoutError): adapter(self.request)
        self.assertIn("http-g001.provider",self.saved)

    def test_write_failure_prevents_transport(self):
        def fail(*args): raise OSError("fixture")
        adapter = Adapter(**(self.args | {"save":fail}))
        with self.assertRaises(OSError): adapter(self.request)
        self.assertFalse(self.sent)

    def test_action_stage_uses_json_and_same_frozen_seed(self):
        adapter = Adapter(**self.args)
        adapter(self.request)
        adapter(self.request | {"generation_id":"g002", "stage":"action", "contract_event":"g001"})
        self.assertEqual(self.sent[1]["response_format"],{"type":"json_object"})
        self.assertEqual(self.sent[1]["seed"],self.sent[0]["seed"])

    def test_context_overflow_stops_before_dispatch(self):
        checker = self.args["context_check"]
        adapter = Adapter(**(self.args | {"context_check":lambda p: checker(p)|{"fits":False}}))
        with self.assertRaises(ValueError): adapter(self.request)
        self.assertIn("http-g001.context",self.saved)
        self.assertFalse(self.sent)

    def test_provider_token_mismatch_preserved_then_stopped(self):
        self.response["usage"] = {"prompt_tokens":999,"completion_tokens":3}
        adapter = Adapter(**self.args)
        with self.assertRaises(ValueError): adapter(self.request)
        self.assertIn("http-g001.provider",self.saved)
        with self.assertRaises(RuntimeError): adapter(self.request)

    def test_context_check_time_counts_toward_deadline(self):
        checker = self.args["context_check"]
        def slow(p):
            self.time = 31
            return checker(p)
        with self.assertRaises(TimeoutError):
            Adapter(**(self.args | {"context_check":slow}))(self.request)
        self.assertFalse(self.sent)

    def test_recovery_schedule_supports_23_calls_and_rejects_24(self):
        adapter=Adapter(**self.args,protocol="recovery-v2",text_count=lambda text:2)
        for index in range(1,24):
            stage="contract" if index==1 else "recovery" if index==22 else "terminal" if index==23 else "action"
            adapter(self.request|{"generation_id":f"g{index:03d}","stage":stage,
                                  "contract_event":None if index==1 else "g001"})
        self.assertEqual(len(self.sent),23)
        with self.assertRaises(RuntimeError): adapter(self.request)
        self.assertIn("http-g023.completion_context",self.saved)

    def test_recovery_requires_counter_and_fixed_cap(self):
        with self.assertRaises(ValueError): Adapter(**self.args,protocol="recovery-v2")
        with self.assertRaises(ValueError):
            Adapter(**(self.args|{"max_tokens":256}),protocol="recovery-v2",text_count=lambda t:2)

    def test_recovery_overlong_output_preserved_not_retried(self):
        adapter=Adapter(**self.args,protocol="recovery-v2",text_count=lambda t:513)
        with self.assertRaises(ValueError): adapter(self.request)
        self.assertIn("http-g001.provider",self.saved)
        self.assertEqual(self.saved["http-g001.completion_context"]["raw_text_tokens_local"],513)
        with self.assertRaises(RuntimeError): adapter(self.request)
        self.assertEqual(len(self.sent),1)

    def test_recovery_provider_over_cap_rejected_even_if_retokenization_short(self):
        self.response["usage"]={"prompt_tokens":12,"completion_tokens":513}
        with self.assertRaises(ValueError):
            Adapter(**self.args,protocol="recovery-v2",text_count=lambda t:2)(self.request)
        self.assertIn("http-g001.provider",self.saved)

    def test_recovery_framed_cap_checked_separately_from_raw_cap(self):
        counter=lambda text:529 if text.startswith("<|im_start|>") else 2
        with self.assertRaises(ValueError):
            Adapter(**self.args,protocol="recovery-v2",text_count=counter)(self.request)
        self.assertEqual(self.saved["http-g001.completion_context"]["assistant_frame_tokens"],529)
        self.assertIn("http-g001.provider",self.saved)

    def test_recovery_does_not_accept_legacy_terminal_stage_at_22(self):
        adapter=Adapter(**self.args,protocol="recovery-v2",text_count=lambda t:2)
        for index in range(1,22):
            adapter(self.request|{"generation_id":f"g{index:03d}",
                "stage":"contract" if index==1 else "action",
                "contract_event":None if index==1 else "g001"})
        with self.assertRaises(ValueError):
            adapter(self.request|{"generation_id":"g022","stage":"terminal","contract_event":"g001"})
        self.assertEqual(len(self.sent),21)


if __name__ == "__main__": unittest.main()
