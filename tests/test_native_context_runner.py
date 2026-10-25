import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from runtime import native_context_runner as runner
from runtime.cache_intent_journal import verify
from tests.test_native_transport_adapter import provider
from tests.test_research_context_interface import requests


class Tests(unittest.TestCase):
    def setUp(self):
        # Use an explicitly project-local temporary directory, not blocked OS temp.
        root=Path(__file__).resolve().parents[1]/'runtime/runs'
        self.temp=tempfile.TemporaryDirectory(dir=root,prefix='native-runner-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name)/'run'
        self.counter=Mock(); self.counter.inspect.return_value={'reference_input_tokens':100}
        self.plan=runner.build_plan(requests())

    def run_fixture(self, factory):
        return runner.run(self.plan,counter=self.counter,transport_factory=factory,
                          output=self.output,mode='SCRIPTED_NOT_MODEL',clock=lambda:0)

    def test_complete_coverage_journals_and_cost(self):
        result=self.run_fixture(lambda ident,log:lambda p,t:json.dumps(provider()))
        self.assertEqual(result['readout']['totals']['admitted'],8)
        self.assertEqual(result['cost_guard']['accounted_micro_cny'],8*360)
        self.assertEqual(result['actual_model_calls'],0)
        self.assertFalse(result['readout']['further_paid_calls_authorized'])
        verify(self.output/'batch')
        for job in self.plan['jobs']: verify(self.output/job['id'])
        with self.assertRaises(FileExistsError): self.run_fixture(lambda *a:None)

    def test_malformed_all_observed_without_retry(self):
        raw=provider(); raw['choices'][0]['message']['tool_calls']*=2
        generate=Mock(return_value=json.dumps(raw))
        result=self.run_fixture(lambda ident,log:generate)
        self.assertEqual(generate.call_count,8)
        self.assertEqual(result['readout']['totals']['invalid'],8)
        self.assertEqual(result['readout']['decision'],'STOP_INTERFACE_REVIEW')

    def test_transport_failure_stops_retains_rest_and_reservation(self):
        generate=Mock(side_effect=TimeoutError('fixture'))
        result=self.run_fixture(lambda ident,log:generate)
        self.assertEqual(generate.call_count,1)
        self.assertEqual(result['readout']['totals']['failed'],1)
        self.assertEqual(result['readout']['totals']['not_started'],7)
        self.assertEqual(result['cost_guard']['uncertain_reserved_micro_cny'],4296)

    def test_provider_count_mismatch_stops_no_offset(self):
        raw=provider(); raw['usage']['prompt_tokens']=101
        generate=Mock(return_value=json.dumps(raw))
        result=self.run_fixture(lambda ident,log:generate)
        self.assertEqual(generate.call_count,1)
        self.assertEqual(result['readout']['totals']['observed'],0)
        self.assertEqual(result['cost_guard']['accounted_micro_cny'],362)

    def test_plan_mutation_rejected(self):
        for change in ('drop','reorder','payload','label'):
            p=copy.deepcopy(self.plan)
            if change=='drop': p['jobs'].pop()
            elif change=='reorder': p['jobs'].reverse()
            elif change=='payload': p['jobs'][0]['payload']['messages'][1]['content']='changed'
            else: p['jobs'][0]['expected_action']='run_sql'
            with self.subTest(change=change),self.assertRaises(ValueError): runner.validate(p)

    def test_full_cost_admission_prevents_calls(self):
        self.counter.inspect.return_value={'reference_input_tokens':8192}
        factory=Mock()
        with self.assertRaises(ValueError): self.run_fixture(factory)
        factory.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__=='__main__': unittest.main()
