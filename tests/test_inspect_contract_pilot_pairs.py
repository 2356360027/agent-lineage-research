"""Offline synthetic fixtures, not actual experimental outcomes."""
from copy import deepcopy
import unittest

from scripts.inspect_contract_pilot_pairs import compare_pairs, parse_received, qwen_prompt, verify_call_binding


class InspectionTests(unittest.TestCase):
    def test_safe_literal_and_schema_extraction(self):
        prompt = "a\\b\n'user' , params: SamplingParams(fake)"
        schema = {"properties": {"time": {"description": "Time interval."}}}
        line = (f"INFO Received request id-1: prompt: {prompt!r}, params: SamplingParams("
                f"n=1, guided_decoding=GuidedDecodingParams(json={schema!r}, regex=None)), "
                "prompt_token_ids: None, lora_request: None.")
        self.assertEqual(parse_received(line), ("id-1", prompt, schema))

    def test_no_code_execution(self):
        for prompt in ("__import__('os').getcwd()", "None"):
            with self.assertRaises(Exception):
                parse_received(f"Received request id: prompt: {prompt}, params: SamplingParams(), prompt_token_ids: None")
        self.assertIsNone(parse_received("INFO server is ready"))

    def test_template(self):
        self.assertEqual(qwen_prompt([{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]),
                         "<|im_start|>system\nS<|im_end|>\n<|im_start|>user\nU<|im_end|>\n<|im_start|>assistant\n")

    def test_pair_counts_do_not_label_semantics(self):
        calls = [{"history_id": "h", "factor": "availability", "arm": i, "seed": i,
                  "envelope": {"fixture": 1}} for i in range(2)]
        records = [{"index": i, "status": "format_valid", "contract": {"unit": v}}
                   for i, v in enumerate(("molecule", "molecule_id"))]
        before = deepcopy((calls, records))
        result = compare_pairs(calls, records)
        self.assertEqual((calls, records), before)
        self.assertEqual(result["pair_count"], 1)
        self.assertEqual(result["different_field_slots"], 1)
        self.assertIsNone(result["pairs"][0]["semantic_label"])
        self.assertEqual(result["unique_envelopes"], 1)
        self.assertFalse(result["duplicate_envelopes_not_full_request_replicates"][0]["all_contracts_exactly_equal"])

    def test_missing_duplicate_and_invalid_records_rejected(self):
        call = {"history_id": "h", "factor": "availability", "arm": 0, "seed": 0, "envelope": {}}
        record = {"index": 0, "status": "format_valid", "contract": {"time": "not applicable"}}
        for calls, records in (([call], [record]), ([call], []),
                               ([call, call], [record, dict(record, index=1)]),
                               ([call], [dict(record, status="format_invalid")])):
            with self.assertRaises(Exception):
                compare_pairs(calls, records)

    def test_request_and_assessment_bind_to_frozen_call(self):
        call = {"history_id": "h", "factor": "availability", "arm": 0, "seed": 15,
                "envelope": {"messages": [{"role": "user", "content": "fixture"}]}}
        request = call["envelope"] | {"model": "Qwen/Qwen2.5-7B-Instruct", "seed": 15,
                                      "temperature": 0, "top_p": 1, "max_tokens": 768, "stream": False}
        record = {k: call[k] for k in ("history_id", "factor", "arm", "seed")}
        record.update(index=0, semantic_status="UNREVIEWED")
        verify_call_binding(call, request, record, 0, 768)
        for key, value in (("seed", 16), ("temperature", 1), ("messages", []), ("extra", "x")):
            with self.assertRaises(Exception):
                verify_call_binding(call, request | {key: value}, record, 0, 768)
        for key, value in (("history_id", "other"), ("factor", "read_cost"), ("arm", 1),
                           ("seed", 16), ("index", 1), ("semantic_status", "REVIEWED")):
            with self.assertRaises(Exception):
                verify_call_binding(call, request, record | {key: value}, 0, 768)


if __name__ == "__main__":
    unittest.main()
