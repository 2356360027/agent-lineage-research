"""Illustrative fixture labels only: not people, actual judgments or results."""
from copy import deepcopy
import json
import unittest
from runtime import cache_intent_review_accounting as accounting
from runtime import bird_readonly_audit as audit
from scripts import account_staged_primary_reviews as importer
from runtime.cache_intent_journal import verify
from tests import test_prepare_staged_contract_review as fixtures


class AccountingTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.ReviewTests(); f.setUp(); self.addCleanup(f.doCleanups)
        f.seal(); f.prepare(); self.fixture=f
        self.packet=f.root/"review"
        self.template=json.loads((self.packet/"reviewer-1.json").read_text())
        self.mapping=json.loads((self.packet/"organizer-only-mapping.json").read_text())
        self.ident=next(i["item_id"] for i in self.template["items"] if i["raw_contract"] is not None)

    def filled(self,code,label):
        packet=deepcopy(self.template)
        packet["reviewer"].update(code=code,date="2026-10-24",independently_completed=True,
            prior_exposure="synthetic fixture",assistance_used="synthetic fixture",
            current_request_review_reference="synthetic-not-a-human-review")
        item=next(i for i in packet["items"] if i["item_id"]==self.ident)
        item["review"].update(whole_contract_vs_request=label,evidence_quote="fixture",
                             reason="Synthetic test only",material_omission=False,field_misplacement_only=False)
        return packet

    def summarize(self,reviews): return accounting.summarize(self.template,self.mapping,reviews)

    def test_both_missing_still_twelve_slots_and_bounds_minus_one_to_one(self):
        result=self.summarize([None,None])
        self.assertEqual(len(result["rows"]),12)
        self.assertEqual((result["bounds"]["difference_lower"],result["bounds"]["difference_upper"]),("-1","1"))
        self.assertFalse(result["task_and_reference_admission_verified"])

    def test_agreement_is_provisional_not_adjudication(self):
        result=self.summarize([self.filled("fixture-A","contradiction"),self.filled("fixture-B","contradiction")])
        row=next(r for r in result["rows"] if r["item_id"]==self.ident)
        self.assertIs(row["provisional_contradiction"],True)
        self.assertEqual(result["bounds"]["planned_pairs_supplied"],6)
        self.assertFalse(result["reviewer_independence_verified"])
        self.assertFalse(result["adjudication_completed"])

    def test_disagreement_not_majority_voted_or_replaced(self):
        result=self.summarize([self.filled("fixture-A","match"),self.filled("fixture-B","contradiction")])
        row=next(r for r in result["rows"] if r["item_id"]==self.ident)
        self.assertIsNone(row["provisional_contradiction"])
        self.assertEqual(row["accounting_reason"],"DISAGREEMENT_REQUIRES_ADJUDICATION")
        self.assertFalse(row["review_1"]["label"])
        self.assertTrue(row["review_2"]["label"])

    def test_duplicate_reviewer_codes_not_two_people(self):
        r=self.filled("same-fixture","match")
        result=self.summarize([r,deepcopy(r)])
        self.assertTrue(all(x["provisional_contradiction"] is None for x in result["rows"]))

    def test_false_or_missing_independence_disclosure_retains_unknown(self):
        for value in (False,None):
            a=self.filled("fixture-A","match"); a["reviewer"]["independently_completed"]=value
            result=self.summarize([a,self.filled("fixture-B","match")])
            self.assertTrue(all(x["provisional_contradiction"] is None for x in result["rows"]))

    def test_changed_task_or_response_rejected(self):
        a=self.filled("fixture-A","match"); a["items"][0]["current_request"]="changed"
        with self.assertRaises(ValueError): self.summarize([a,None])

    def test_price_contrast_is_eight_minus_one_with_all_six_pairs(self):
        # Pure illustrative accounting fixture, not an imported human record.
        second=next(r["item_id"] for r in self.mapping["records"] if r["job_id"]=="h200a-p8")
        item=next(i for i in self.template["items"] if i["item_id"]==second)
        item.update(raw_contract=self.fixture.contract,response_availability="FORMAT_VALID_UNREVIEWED")
        reviews=[self.filled(code,"contradiction") for code in ("fixture-A","fixture-B")]
        for review in reviews:
            target=next(i for i in review["items"] if i["item_id"]==second)
            target["review"].update(whole_contract_vs_request="match",material_omission=False,
                field_misplacement_only=False,evidence_quote="fixture",reason="Synthetic sign check")
        result=self.summarize(reviews)
        self.assertEqual(result["bounds"]["complete_case_descriptive_difference"],"-1")
        self.assertEqual(result["bounds"]["difference_lower"],"-1")
        self.assertEqual(result["bounds"]["difference_upper"],"2/3")
        self.assertEqual(result["bounds"]["planned_pairs_supplied"],6)

    def test_nonstop_response_not_promoted_by_two_matching_raters(self):
        target=next(i for i in self.template["items"] if i["item_id"]==self.ident)
        target["response_availability"]="NONSTOP_RESPONSE"
        result=self.summarize([self.filled("fixture-A","match"),self.filled("fixture-B","match")])
        self.assertTrue(all(x["provisional_contradiction"] is None for x in result["rows"]))

    def test_quote_must_be_present_and_omission_conflict_not_silently_resolved(self):
        for change in ({"evidence_quote":"not present"},{"material_omission":True}):
            a=self.filled("fixture-A","match")
            item=next(i for i in a["items"] if i["item_id"]==self.ident)
            item["review"].update(change)
            result=self.summarize([a,self.filled("fixture-B","match")])
            self.assertTrue(all(x["provisional_contradiction"] is None for x in result["rows"]))

    def test_importer_pins_and_preserves_files_without_changing_blank_packet(self):
        paths=[]
        for n in (1,2):
            path=self.fixture.root/f"filled-{n}.json"
            audit.save_new(path,self.filled(f"fixture-{n}","match"))
            paths.append((path,audit.hash_file(path)))
        before=audit.hash_file(self.packet/"artifact_hashes.json")
        result=importer.run(self.packet,packet_sha=before,review_inputs=paths,output=self.fixture.root/"imported")
        self.assertEqual(result["known_provisional_labels"],1)
        self.assertEqual(audit.hash_file(self.packet/"artifact_hashes.json"),before)
        verify(self.fixture.root/"imported")
        paths[0][0].write_text("{}")
        with self.assertRaises(ValueError):
            importer.run(self.packet,packet_sha=before,review_inputs=paths,output=self.fixture.root/"rejected")
        self.assertFalse((self.fixture.root/"rejected").exists())


if __name__=="__main__": unittest.main()
