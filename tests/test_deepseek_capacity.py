"""Conditional-capacity software checks using synthetic counters/responses."""
import json
import unittest

from runtime.deepseek_recovery_capacity import RecoveryCapacity
from runtime.deepseek_trajectory_adapter import make_payload
from runtime.cache_intent_staged_loop import run_recovery_trajectory, RECOVERY_INSTRUCTION
from tests import test_deepseek_trajectory as adapter_fixtures
from tests.test_cache_intent_recovery import FakeSession


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = adapter_fixtures.Tests()
        self.fixture.setUp()
        self.capacity = RecoveryCapacity(self.fixture.pair[0], self.fixture.counter)

    def test_certificate_and_initial_check(self):
        receipt = self.capacity(make_payload(self.fixture.pair[0]['messages']))
        self.assertLess(receipt['full_history_bound_including_completion'],32768)
        self.assertTrue(receipt['conditioned_on_runtime_checks'])
        self.assertFalse(receipt['provider_tokenizer_identity_proven'])
        certificate = self.capacity.certificate()
        self.assertEqual(certificate['input_bounds_by_generation'][-1]+512,
                         certificate['full_history_bound_including_completion'])
        self.assertEqual(sum(certificate['input_bounds_by_generation']),certificate['total_input_bound'])

    def test_full_horizon_checks_every_prompt_and_observation(self):
        f = self.fixture
        adapter = f.make_adapter([f.contract]+['{"kind":"run_sql","sql":"SELECT 1"}']*21+
                                 ['{"kind":"answer","text":"fixture"}'])
        adapter.capacity = self.capacity
        result = run_recovery_trajectory(f.pair[0],FakeSession(),adapter,f.save,
                                         self.capacity.observation_bound())
        self.assertEqual(result['status'],'ANSWERED')
        self.assertEqual(len([k for k in f.records if k.endswith('.capacity')]),23)
        self.assertIn('t021.exposure',f.records)

    def test_changed_initial_payload_rejected(self):
        p = make_payload(self.fixture.pair[0]['messages'])
        p['messages'][1]['content'] += 'changed'
        with self.assertRaises(ValueError): self.capacity(p)

    def test_join_failure_detected_before_final_recovery(self):
        original = self.fixture.counter.text
        self.fixture.counter.text = lambda s: 99999 if '\n\n'+RECOVERY_INSTRUCTION in s else original(s)
        raw = FakeSession().call('read_cache',{})
        with self.assertRaises(ValueError): self.capacity.observation_bound().apply(raw)
        self.assertTrue(raw['complete'])
        self.assertEqual(raw['status'],'EXECUTED')

    def test_oversize_stays_whole_or_explicit_incomplete(self):
        raw = FakeSession().call('read_cache',{})
        raw['rows'] = [['large'*1000]]
        exposed = self.capacity.observation_bound().apply(raw)
        self.assertEqual(exposed['observation']['status'],'OBSERVATION_TOO_LARGE')
        self.assertNotIn('rows',exposed['observation'])


if __name__ == '__main__': unittest.main()
