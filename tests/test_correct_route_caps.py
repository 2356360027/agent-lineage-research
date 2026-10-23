"""Software assertions only; no model, SQL, network or research outcomes."""
import unittest
from scripts.audit_correct_route_caps import check_reply, check_observation


class CorrectRouteCapTests(unittest.TestCase):
    def test_reply_requires_scripted_complete_source(self):
        reply = {"backend":"SCRIPTED_NOT_MODEL","finish_reason":"stop","content":"{}"}
        self.assertEqual(check_reply(reply,len)["raw_tokens"],2)
        for key,value in (("backend","MODEL"),("finish_reason","length")):
            with self.assertRaises(ValueError): check_reply(dict(reply,**{key:value}),len)

    def test_reply_overflow_not_truncated(self):
        reply = {"backend":"SCRIPTED_NOT_MODEL","finish_reason":"stop","content":"a"*513}
        with self.assertRaises(ValueError): check_reply(reply,len)
        self.assertEqual(len(reply["content"]),513)

    def test_complete_result_across_metadata_superset(self):
        observation = {"status":"EXECUTED","complete":True,"rows":[],
                       "charged_units":10,"remaining_budget":180,"attempt":21}
        result = check_observation(observation,lambda messages:100)
        self.assertEqual(result["metadata_combinations"],21*201)
        self.assertFalse(result["withheld"])
        self.assertEqual(observation["remaining_budget"],180)

    def test_incomplete_and_withheld_rejected(self):
        with self.assertRaises(ValueError):
            check_observation({"complete":False},lambda messages:100)
        with self.assertRaises(ValueError):
            check_observation({"status":"EXECUTED","complete":True,"rows":[]},lambda messages:513)


if __name__ == "__main__": unittest.main()
