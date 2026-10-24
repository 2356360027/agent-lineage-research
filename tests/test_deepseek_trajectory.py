"""Scripted adapter/loop tests, never API or model results."""
from copy import deepcopy
import json
import unittest

from runtime.deepseek_text_counter import render, JSON_INSTRUCTION
from runtime.deepseek_trajectory_adapter import Adapter, CostGuard, make_payload
from runtime.cache_intent_prompts import CONTRACT_FIELDS
from runtime.cache_intent_staged_loop import run_recovery_trajectory
from runtime.cache_intent_recovery_preview import make_pair
from runtime.cache_intent_observation_bound import ObservationBound
from tests.test_cache_intent_recovery import FakeSession


class FixtureCounter:
    """Synthetic count for control-flow tests, NOT a tokenizer."""
    def chat(self, messages): return max(1, len(render(messages))//4)
    def text(self, text): return len(text)//4


class Tests(unittest.TestCase):
    def setUp(self):
        self.counter = FixtureCounter()
        self.records, self.sent = {}, []
        self.guard = CostGuard(ceiling_micro_cny=1_000_000, max_calls=23,
                               input_rate=2, output_rate=8)
        self.pair = make_pair('List IDs', None, ['CREATE TABLE x(id TEXT)'],
            {'historical_request':'List IDs','sql':'SELECT id FROM x'}, 'a'*64)
        self.contract = json.dumps(dict.fromkeys(CONTRACT_FIELDS, 'fixture'))

    def save(self, name, value):
        if name in self.records: raise FileExistsError(name)
        self.records[name] = deepcopy(value)

    def request(self):
        return {'generation_id':'g001', 'stage':'contract',
                'contract_event':None, 'messages':self.pair[0]['messages']}

    def make_adapter(self, contents, mutate=lambda r:r, clock=lambda:0):
        iterator = iter(contents)
        def transport(payload, timeout):
            self.sent.append(deepcopy(payload))
            return mutate({'model':'deepseek-flash', 'usage':{
                'prompt_tokens':self.counter.chat(payload['messages']), 'completion_tokens':10},
                'choices':[{'message':{'content':next(iterator)}, 'finish_reason':'stop'}]})
        return Adapter(counter=self.counter, transport=transport, save=self.save,
                       guard=self.guard, max_seconds=30, clock=clock)

    def test_full_horizon_keeps_recovery_and_terminal(self):
        sql = '{"kind":"run_sql","sql":"SELECT 1"}'
        answer = '{"kind":"answer","text":"fixture"}'
        adapter = self.make_adapter([self.contract] + [sql]*21 + [answer])
        session = FakeSession()
        result = run_recovery_trajectory(self.pair[0], session, adapter, self.save,
                                         ObservationBound(self.counter.chat))
        self.assertEqual(result['status'], 'ANSWERED')
        self.assertEqual(len(self.sent), 23)
        self.assertEqual(session.attempts, 21)
        self.assertEqual(session.balance, 0)
        self.assertEqual(self.records['g022.pending']['stage'], 'recovery')
        self.assertEqual(self.records['g023.pending']['stage'], 'terminal')
        self.assertIn('api-g023.provider', self.records)
        self.assertFalse(self.guard.uncertain)
        self.assertTrue(all('seed' not in p for p in self.sent))
        self.assertTrue(all('Stage 1 task-contract output ONLY' in p['messages'][0]['content']
                            for p in self.sent))

    def test_late_or_mismatched_response_saved_before_rejection(self):
        for field in ('model', 'usage'):
            self.setUp()
            def mutate(r):
                if field == 'model': r['model'] = 'unexpected'
                else: r['usage']['prompt_tokens'] += 1
                return r
            adapter = self.make_adapter([self.contract], mutate)
            with self.assertRaises(ValueError): adapter(self.request())
            self.assertIn('api-g001.provider', self.records)
            with self.assertRaises(RuntimeError): adapter(self.request())
            self.assertEqual(len(self.sent), 1)
            self.assertTrue(self.guard.terminal)

    def test_transport_failure_retains_reservation(self):
        def broken(payload, timeout):
            self.sent.append(payload)
            raise TimeoutError('unsafe transport detail is never logged')
        adapter = Adapter(counter=self.counter, transport=broken, save=self.save,
                          guard=self.guard)
        with self.assertRaises(TimeoutError): adapter(self.request())
        self.assertIn('api-g001.failure', self.records)
        self.assertTrue(self.guard.uncertain)
        self.assertNotIn('unsafe transport detail', json.dumps(self.records))
        with self.assertRaises(RuntimeError): adapter(self.request())
        self.assertEqual(len(self.sent), 1)

    def test_bad_usage_retains_raw_and_unknown_billing(self):
        adapter = self.make_adapter([self.contract], lambda r:dict(r, usage={'prompt_tokens':True}))
        with self.assertRaises(ValueError): adapter(self.request())
        self.assertIn('api-g001.provider', self.records)
        self.assertTrue(self.guard.uncertain)

    def test_money_and_time_prevent_dispatch(self):
        self.guard.ceiling = 1
        adapter = self.make_adapter([self.contract])
        with self.assertRaises(RuntimeError): adapter(self.request())
        self.assertFalse(self.sent)
        self.setUp()
        now = [0]
        adapter = self.make_adapter([self.contract], clock=lambda:now[0])
        now[0] = 31
        with self.assertRaises(TimeoutError): adapter(self.request())
        self.assertFalse(self.sent)

    def test_late_return_is_retained_and_charged(self):
        now = [0]
        def late(r): now[0] = 31; return r
        adapter = self.make_adapter([self.contract], late, clock=lambda:now[0])
        with self.assertRaises(TimeoutError): adapter(self.request())
        self.assertIn('api-g001.provider', self.records)
        self.assertGreater(self.guard.accounted, 0)

    def test_invalid_contract_is_not_repaired(self):
        adapter = self.make_adapter(['{"entity":"only one field"}'])
        result = run_recovery_trajectory(self.pair[0], FakeSession(), adapter, self.save,
                                         ObservationBound(self.counter.chat))
        self.assertEqual(result['status'], 'INVALID_CONTRACT')
        self.assertEqual(len(self.sent), 1)

    def test_truncated_response_not_repaired(self):
        def mutate(r): r['choices'][0]['finish_reason'] = 'length'; return r
        adapter = self.make_adapter([self.contract], mutate)
        result = run_recovery_trajectory(self.pair[0], FakeSession(), adapter, self.save,
                                         ObservationBound(self.counter.chat))
        self.assertEqual(result['status'], 'INVALID_CONTRACT_RESPONSE')
        self.assertTrue(adapter.failed)

    def test_pair_difference_not_changed_by_provider_adaptation(self):
        a, b = [make_payload(e['messages']) for e in self.pair]
        pa, pb = [json.loads(p['messages'][1]['content']) for p in (a,b)]
        self.assertEqual((pa['context']['cache']['read_cost'], pb['context']['cache']['read_cost']), (1,8))
        pb['context']['cache']['read_cost'] = 1
        self.assertEqual(pa, pb)
        self.assertEqual(a['messages'][0], b['messages'][0])

    def test_render_consecutive_users_and_all_history(self):
        messages = [{'role':'system','content':'S'}, {'role':'user','content':'U'},
                    {'role':'assistant','content':'A'}, {'role':'user','content':'O'},
                    {'role':'user','content':'R'}]
        original = deepcopy(messages)
        text = render(messages)
        self.assertIn('A<｜end▁of▁sentence｜><｜User｜>O\n\nR', text)
        self.assertEqual(text.count(JSON_INSTRUCTION), 1)
        self.assertEqual(messages, original)
        messages[-1]['content'] = '<｜User｜>'
        with self.assertRaises(ValueError): render(messages, ('<｜User｜>',))

    def test_guard_does_not_double_settle_or_drop_uncertain_calls(self):
        ticket = self.guard.reserve(100, 512)
        self.guard.settle(ticket, {'prompt_tokens':100,'completion_tokens':10})
        self.assertEqual(self.guard.accounted, 280)
        with self.assertRaises(ValueError):
            self.guard.settle(ticket, {'prompt_tokens':100,'completion_tokens':10})
        self.guard.reserve(100, 512)
        self.assertEqual(self.guard.snapshot()['uncertain_reserved_micro_cny'], 4296)


if __name__ == '__main__': unittest.main()
