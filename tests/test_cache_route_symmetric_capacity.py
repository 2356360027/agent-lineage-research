"""Mock counter checks; real tokenizer is evaluated separately offline."""
from copy import deepcopy
import unittest

from runtime.cache_action_preview import payload
from runtime.cache_intent_observation_bound import serialize
from runtime.cache_intent_staged_loop import TERMINAL_INSTRUCTION
from runtime.cache_route_symmetric_capacity import SymmetricCapacity
from runtime.cache_route_symmetric_design import build
from tests.test_cache_action_adapter import Counter
from tests.test_cache_route_symmetric_loop import READ, SQL, ANSWER


class Tests(unittest.TestCase):
    def setUp(self):
        self.common=dict(shared_contract='Shared',history_contract='History',current_contract='Current',
            schema=['CREATE TABLE t(x)'],history_sql='SELECT x FROM t',database_sha256='a'*64)
        self.envelope=build(self.common)[3]
        self.counter=Counter()
        self.capacity=SymmetricCapacity(self.envelope,self.common,self.counter)
        self.observation=dict(status='EXECUTED',complete=True,charged_units=20,
                              remaining_budget=40,attempt=1,columns=['x'],rows=[[['text','x']]])

    def history(self,generation,third=READ):
        messages=deepcopy(self.envelope['messages'])
        for number in range(1,generation):
            messages.extend([dict(role='assistant',content=third if number==3 else READ),
                             dict(role='user',content=serialize(self.observation))])
        if generation==4: messages.append(dict(role='user',content=TERMINAL_INSTRUCTION))
        return messages

    def test_each_generation_accepts_exact_schedule(self):
        for third in (READ,SQL):
            for generation in range(1,5):
                receipt=self.capacity(payload(self.history(generation,third)))
                self.assertEqual(receipt['generation_index'],generation)
                self.assertLessEqual(receipt['reference_input_tokens'],receipt['input_bound'])
        self.assertEqual(self.capacity.certificate()['total_output_bound'],2048)

    def test_no_old_recovery_stage_no_extra_turns_no_prior_answer(self):
        histories=[]
        messages=self.history(3);messages.append(dict(role='user',content='SQL only'));histories.append(messages)
        messages=self.history(4);messages.pop();histories.append(messages)
        messages=self.history(4);messages[-1]['content']='changed';histories.append(messages)
        messages=self.history(2);messages[2]['content']=ANSWER;histories.append(messages)
        messages=self.history(4);messages+=messages[2:4];histories.append(messages)
        for messages in histories:
            with self.assertRaises(ValueError):self.capacity(payload(messages))

    def test_payload_settings_and_initial_packet_bound(self):
        for key,value in [('model','other'),('max_tokens',513),('temperature',1),('stream',True)]:
            candidate=payload(self.history(1));candidate[key]=value
            with self.assertRaises(ValueError):self.capacity(candidate)
        changed=self.history(1);changed[1]['content']+=' '
        with self.assertRaises(ValueError):self.capacity(payload(changed))

    def test_overlong_action_and_observation_rejected(self):
        messages=self.history(2);messages[2]['content']=' '*513+READ
        with self.assertRaises(ValueError):self.capacity(payload(messages))
        messages=self.history(2);messages[3]['content']=serialize(self.observation|{'rows':['x'*1100]})
        with self.assertRaises(ValueError):self.capacity(payload(messages))

    def test_wrong_serialization_or_roles_rejected(self):
        messages=self.history(2);messages[3]['content']+=' '
        with self.assertRaises(ValueError):self.capacity(payload(messages))
        messages=self.history(2);messages[2]['role']='user'
        with self.assertRaises(ValueError):self.capacity(payload(messages))

    def test_bad_generation_and_counter_and_window(self):
        for value in (True,0,5,1.0):
            with self.assertRaises(ValueError):self.capacity.input_bound(value)
        self.counter.chat=lambda m:32700
        with self.assertRaises(ValueError):SymmetricCapacity(self.envelope,self.common,self.counter)
        self.counter.chat=lambda m:True
        with self.assertRaises(ValueError):SymmetricCapacity(self.envelope,self.common,self.counter)

    def test_actual_multiturn_count_violation_not_clipped(self):
        original=self.counter.chat
        self.counter.chat=lambda m:self.capacity.input_bound(2)+1 if len(m)>2 else original(m)
        with self.assertRaisesRegex(ValueError,'Actual reference count'):
            self.capacity(payload(self.history(2)))

    def test_observation_bound_uses1024_and_preserves_raw(self):
        raw=self.observation|{'rows':[['text','x'*1100]]}
        exposed=self.capacity.observation_bound().apply(raw)
        self.assertEqual(exposed['bound'],1024)
        self.assertEqual(exposed['observation']['status'],'OBSERVATION_TOO_LARGE')
        self.assertNotIn('rows',exposed['observation'])
        self.assertIn('rows',raw)


if __name__=='__main__':unittest.main()
