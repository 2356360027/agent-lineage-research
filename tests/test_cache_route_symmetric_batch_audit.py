"""Synthetic sealed batches and deliberate cross-record corruption only."""
import json
import unittest
from unittest.mock import patch
from runtime import bird_readonly_audit as audit
from runtime.cache_route_symmetric_batch_audit import finished_batch,stopped_inventory
from tests import test_cache_route_symmetric_batch as fixtures


class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def check(self,fn=finished_batch):
        f=self.f
        return fn(f.output,f.plan,f.cache,f.counter,expected_plan_sha=audit.digest(f.plan),
                  mode='SCRIPTED_NOT_MODEL',ceiling_micro_cny=1000000,input_rate=2,output_rate=8)

    def rewrite(self,path,value):
        # Tampering fixture only: production archives are never rewritten.
        path.write_bytes(audit.encoded(value))

    def reseal(self,directory,recursive=False):
        entries=directory.rglob('*') if recursive else directory.iterdir()
        self.rewrite(directory/'artifact_hashes.json',
            {p.relative_to(directory).as_posix():audit.hash_file(p) for p in sorted(entries)
             if p.is_file() and p!=directory/'artifact_hashes.json'})

    def change_summary(self,change):
        summary=json.loads((self.f.output/'summary.json').read_text('utf-8'));change(summary)
        self.rewrite(self.f.output/'summary.json',summary)
        self.rewrite(self.f.output/'batch/summary.json',summary)
        self.reseal(self.f.output/'batch');self.reseal(self.f.output,True)

    def test_finished_all_four_replayed_without_sql(self):
        original=self.f.execute()
        with patch.object(audit,'run_child',side_effect=AssertionError('No audit query')):result=self.check()
        self.assertEqual(len(result['rows']),4)
        self.assertEqual(result['cost_guard'],original['cost_guard'])
        self.assertEqual(result['new_SQL_executions'],0)

    def test_unsealed_or_mutated_refused(self):
        self.f.execute();seal=self.f.output/'artifact_hashes.json'
        raw=seal.read_bytes();seal.unlink()
        with self.assertRaisesRegex(ValueError,'Unsealed'):self.check()
        seal.write_bytes(raw)
        self.rewrite(self.f.output/'summary.json',{})
        with self.assertRaises(ValueError):self.check()

    def test_stopped_inventory_keeps_all_slots_and_uncertain_fee(self):
        self.f.scripts[self.f.plan['jobs'][0]['id']]=[TimeoutError()];self.f.execute()
        with self.assertRaisesRegex(ValueError,'Stopped batch'):self.check()
        result=self.check(stopped_inventory)
        self.assertEqual([r['recorded_status'] for r in result['rows']],['FAILED']+['NOT_STARTED']*3)
        self.assertGreater(result['recorded_cost_guard']['uncertain_reserved_micro_cny'],0)
        self.assertFalse(result['fee_reconstruction_verified'])
        self.assertFalse(result['completed_prefix_scored'])
        self.assertTrue(all('final_answer' not in r for r in result['rows']))

    def test_failure_after_success_does_not_score_prefix(self):
        self.f.scripts[self.f.plan['jobs'][1]['id']]=[KeyboardInterrupt()];self.f.execute()
        result=self.check(stopped_inventory)
        self.assertEqual([r['recorded_status'] for r in result['rows']],
                         ['ANSWERED','INTERRUPTED','NOT_STARTED','NOT_STARTED'])
        self.assertEqual(result['answers_graded'],0)

    def test_resealed_missing_slot_cannot_pass(self):
        self.f.execute();self.change_summary(lambda s:s['rows'].pop())
        with self.assertRaisesRegex(ValueError,'four'):self.check()

    def test_resealed_accounting_discrepancy_cannot_pass(self):
        self.f.execute();self.change_summary(lambda s:s['cost_guard'].update(accounted_micro_cny=0))
        with self.assertRaisesRegex(ValueError,'Shared cost'):self.check()

    def test_resealed_forged_answer_rejected(self):
        self.f.execute();trace=self.f.output/self.f.plan['jobs'][0]['id']/'trace'
        path=trace/'g004.decision.json';record=json.loads(path.read_text('utf-8'))
        record['action']['text']='invented';self.rewrite(path,record)
        self.reseal(trace);self.reseal(self.f.output,True)
        with self.assertRaises(ValueError):self.check()

    def test_resealed_extra_artifact_rejected(self):
        self.f.execute();trace=self.f.output/self.f.plan['jobs'][0]['id']/'trace'
        self.rewrite(trace/'extra.json',{});self.reseal(trace);self.reseal(self.f.output,True)
        with self.assertRaises(ValueError):self.check()

    def test_invalid_model_output_not_filtered(self):
        self.f.scripts[self.f.plan['jobs'][0]['id']]=['not json'];self.f.execute()
        result=self.check()
        self.assertEqual(result['rows'][0]['final_status'],'INVALID_ACTION')
        self.assertEqual(len(result['rows']),4)

    def test_tool_finalization_error_inventory_not_ignored(self):
        from runtime.cache_route_symmetric_batch import ToolSession
        original=ToolSession.close
        def failure(session):original(session);raise OSError('fixture')
        with patch.object(ToolSession,'close',failure):self.f.execute()
        result=self.check(stopped_inventory)
        self.assertEqual(result['rows'][0]['recorded_status'],'FINALIZATION_FAILED')
        self.assertEqual(result['rows'][0]['finalization_error'],'OSError')
        self.assertEqual(result['answers_graded'],0)

    def test_nested_unsealed_failure_retained_not_scored(self):
        self.f.scripts[self.f.plan['jobs'][0]['id']]=[TimeoutError()];self.f.execute()
        trace=self.f.output/self.f.plan['jobs'][0]['id']/'trace'
        (trace/'artifact_hashes.json').unlink();self.reseal(self.f.output,True)
        result=self.check(stopped_inventory)
        self.assertEqual(result['rows'][0]['journals']['trace'],'UNSEALED_OR_INCONSISTENT')
        self.assertEqual(result['answers_graded'],0)

    def test_unstarted_artifacts_cannot_be_hidden_in_inventory(self):
        self.f.scripts[self.f.plan['jobs'][0]['id']]=[TimeoutError()];self.f.execute()
        extra=self.f.output/self.f.plan['jobs'][1]['id'];extra.mkdir()
        self.rewrite(extra/'unexpected.json',{});self.reseal(self.f.output,True)
        with self.assertRaisesRegex(ValueError,'Unstarted cell has artifacts'):self.check(stopped_inventory)


if __name__=='__main__':unittest.main()
