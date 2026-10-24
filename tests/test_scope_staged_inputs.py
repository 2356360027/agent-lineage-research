"""Synthetic receipt/prompt tests only; no model, database or human judgments."""
from copy import deepcopy
import unittest
from scripts import prepare_scope_staged_inputs as scope
from runtime import cache_intent_staged_preview as staged
from runtime import cache_intent_prompts as prompts


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.pair = staged.make_pair("Count bonds", "original evidence", ["CREATE TABLE x(a TEXT)"],
            {"historical_request":"Count incident bonds", "sql":"SELECT count(*) FROM x"},"a"*64)
        self.report = {"status":"SNAPSHOT_ONLY_NO_SEMANTIC_APPROVAL","database_unchanged":True,
            "database_sha256_before":"a"*64,"database_sha256_after":"a"*64,
            "results":{"target_mismatched_or_missing_bond":{"status":"EXECUTED",
                "truncated":False,"sql":scope.SCOPE_SQL,"columns":["rows"],"rows":[[0]]},
                "target_connection_counts":{"rows":[["PRIVATE_ANSWER_MARKER"]]}}}

    def test_verified_property_does_not_import_other_report_fields(self):
        scope.validate_scope_report(self.report,"a"*64)
        result = scope.augment_pair(self.pair,234)
        self.assertNotIn("PRIVATE_ANSWER_MARKER",str(result))
        self.assertNotIn("target_connection_counts",str(result))

    def test_only_evidence_changes_and_only_price_differs(self):
        before = deepcopy(self.pair)
        result = scope.augment_pair(self.pair,234)
        self.assertEqual(before,self.pair)
        for old,new in zip(before,result):
            self.assertEqual(prompts._diff(prompts._expanded(old),prompts._expanded(new)),
                [("messages",1,"content","evidence")])
            self.assertEqual(staged.validate_preview(new)["evidence"],"original evidence\n"+scope.SCOPE_TEXT)
        self.assertEqual(prompts._diff(*(prompts._expanded(p) for p in result)),
            [("messages",1,"content","context","cache","read_cost")])

    def test_200_unchanged_and_unshared_copy(self):
        result = scope.augment_pair(self.pair,200)
        self.assertEqual(result,self.pair)
        result[0]["messages"][0]["content"] = "changed fixture"
        self.assertNotEqual(result,self.pair)

    def test_bad_scope_receipt_rejected(self):
        for key,value in (("rows",[[1]]),("rows",[[False]]),("truncated",True),
                          ("status","FAILED"),("sql","SELECT 0"),("columns",["other"])):
            with self.subTest(key=key,value=value):
                report = deepcopy(self.report)
                report["results"]["target_mismatched_or_missing_bond"][key] = value
                with self.assertRaises(ValueError): scope.validate_scope_report(report,"a"*64)

    def test_wrong_db_or_changed_receipt_rejected(self):
        with self.assertRaises(ValueError): scope.validate_scope_report(self.report,"b"*64)
        self.report["database_unchanged"] = False
        with self.assertRaises(ValueError): scope.validate_scope_report(self.report,"a"*64)

    def test_duplicate_scope_wrong_target_or_reversed_pair_rejected(self):
        for pair,target in ((scope.augment_pair(self.pair,234),234),(self.pair,999),
                            (self.pair,True),(self.pair[::-1],234)):
            with self.assertRaises(ValueError): scope.augment_pair(pair,target)


if __name__ == "__main__": unittest.main()
