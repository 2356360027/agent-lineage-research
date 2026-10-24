"""Scripted transport and counter checks; no model service or real credentials."""
from copy import deepcopy
import json
import unittest

from runtime import cache_action_preview as preview
from runtime.cache_action_capacity import ActionCapacity
from runtime.cache_action_adapter import ActionAdapter
from runtime.cache_action_loop import run, RECOVERY, TERMINAL
from runtime.cache_intent_observation_bound import serialize
from runtime.deepseek_trajectory_adapter import CostGuard, MODEL
from tests.test_cache_action_loop import pair, FakeSession, READ, SQL, ANSWER


class Counter:
    def text(self, value): return len(value)
    def chat(self, messages): return 10+sum(self.text(m['content']) for m in messages)


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.counter = Counter()
        self.envelope = pair()[1]
        self.cap = ActionCapacity(self.envelope,self.counter)
        self.observation = dict(status='EXECUTED',complete=True,charged_units=1,
                                remaining_budget=29,attempt=1,columns=['id'],rows=[[['integer','1']]])

    def history(self, generation):
        messages = deepcopy(self.envelope['messages'])
        for k in range(1,generation):
            messages += [dict(role='assistant',content=SQL if k==3 else READ),
                         dict(role='user',content=serialize(self.observation))]
            if k>=2: messages.append(dict(role='user',content=RECOVERY if k==2 else TERMINAL))
        return messages

    def test_every_stage_bound_and_exact_sequence(self):
        for n in range(1,5):
            receipt = self.cap(preview.payload(self.history(n)))
            self.assertEqual(receipt['generation_index'],n)
            self.assertLessEqual(receipt['reference_input_tokens'],self.cap.input_bound(n))
        c=self.cap.certificate()
        self.assertEqual(c['total_output_bound'],2048)
        self.assertFalse(c['inference_authorized'])

    def test_missing_wrong_instruction_or_past_answer_rejected(self):
        histories=[]
        m=self.history(3); m.pop(); histories.append(m)
        m=self.history(3); m[-1]['content']=TERMINAL; histories.append(m)
        m=self.history(2); m[2]['content']=ANSWER; histories.append(m)
        m=self.history(4); m[-3]['content']=READ; histories.append(m)
        for m in histories:
            with self.assertRaises(ValueError): self.cap(preview.payload(m))

    def test_modified_system_and_model_settings_rejected(self):
        p=preview.payload(self.history(1)); p['temperature']=1
        with self.assertRaises(ValueError): self.cap(p)
        p=preview.payload(self.history(1)); p['messages'][0]['content']+='changed'
        with self.assertRaises(ValueError): self.cap(p)

    def test_oversize_observation_and_join_admission(self):
        obs=self.observation|dict(rows=[[['text','x'*1000]]])
        exposed=self.cap.observation_bound().apply(obs)
        self.assertEqual(exposed['observation']['status'],'OBSERVATION_TOO_LARGE')
        self.assertNotIn('rows',exposed['observation'])
        original=self.counter.text
        def toxic_join(text):
            return original(text)+100 if '\n\n'+RECOVERY in text else original(text)
        self.counter.text=toxic_join
        with self.assertRaises(ValueError): self.cap.observation_bound().apply(self.observation)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.counter=Counter(); self.envelope=pair()[1]
        self.cap=ActionCapacity(self.envelope,self.counter)
        self.records={}; self.dispatched=[]; self.now=0
        self.script=iter([READ,ANSWER])
        self.guard=CostGuard(ceiling_micro_cny=1000000,max_calls=48,input_rate=2,output_rate=8)

    def save(self,name,value):
        if name in self.records: raise FileExistsError(name)
        self.records[name]=deepcopy(value)

    def transport(self,payload,timeout):
        self.dispatched.append(deepcopy(payload))
        item=next(self.script)
        if isinstance(item,BaseException): raise item
        return dict(model=MODEL,usage=dict(prompt_tokens=self.counter.chat(payload['messages']),
                                          completion_tokens=self.counter.text(item)),
                    choices=[dict(message=dict(content=item),finish_reason='stop')])

    def adapter(self,transport=None,save=None):
        return ActionAdapter(counter=self.counter,capacity=self.cap,
            transport=transport or self.transport,save=save or self.save,guard=self.guard,
            deadline=600,clock=lambda:self.now)

    def request(self,n=1,messages=None):
        return dict(generation_id=f'g{n:03d}',stage='exploration' if n<3 else 'recovery' if n==3 else 'terminal',
                    messages=messages or deepcopy(self.envelope['messages']))

    def test_integrated_loop_preserves_payload_and_no_contract_suffix(self):
        adapter=self.adapter()
        summary=run(self.envelope,FakeSession(self.envelope),adapter,self.save,self.cap.observation_bound())
        self.assertEqual(summary['status'],'ANSWERED')
        self.assertEqual(adapter.count,2)
        self.assertEqual(self.guard.snapshot()['uncertain_reserved_micro_cny'],0)
        self.assertNotIn('Stage 1:', self.dispatched[0]['messages'][0]['content'])
        self.assertEqual(self.dispatched[1]['messages'][2]['content'],READ)
        with self.assertRaises(RuntimeError): adapter(self.request())

    def test_full_four_call_recovery_history(self):
        self.script=iter([READ,READ,SQL,ANSWER])
        adapter=self.adapter()
        summary=run(self.envelope,FakeSession(self.envelope),adapter,self.save,self.cap.observation_bound())
        self.assertEqual(summary['generation_calls'],4)
        self.assertEqual(summary['tool_attempts'],3)
        self.assertEqual(len(self.dispatched),4)

    def test_network_error_keeps_reservation_and_prevents_retry(self):
        self.script=iter([TimeoutError()]); adapter=self.adapter()
        with self.assertRaises(TimeoutError): adapter(self.request())
        self.assertGreater(self.guard.snapshot()['uncertain_reserved_micro_cny'],0)
        self.assertIn('api-g001.failure',self.records)
        with self.assertRaises(RuntimeError): adapter(self.request())
        self.assertEqual(len(self.dispatched),1)

    def test_usage_mismatch_recorded_before_stopping(self):
        def mismatch(p,t):
            raw=self.transport(p,t); raw['usage']['prompt_tokens']+=1; return raw
        adapter=self.adapter(mismatch)
        with self.assertRaises(ValueError): adapter(self.request())
        self.assertIn('api-g001.provider',self.records)
        self.assertIn('api-g001.cost',self.records)
        self.assertGreater(self.guard.accounted,0)
        self.assertTrue(adapter.terminal)

    def test_late_result_recorded_and_accounted(self):
        def late(p,t):
            raw=self.transport(p,t); self.now=601; return raw
        adapter=self.adapter(late)
        with self.assertRaises(TimeoutError): adapter(self.request())
        self.assertIn('api-g001.provider',self.records)
        self.assertEqual(len(self.dispatched),1)
        self.assertGreater(self.guard.accounted,0)

    def test_deadline_or_failed_pending_journal_prevents_transport(self):
        adapter=self.adapter(); self.now=600
        with self.assertRaises(TimeoutError): adapter(self.request())
        self.assertEqual(self.dispatched,[])
        self.now=0
        def fail(name,value):
            if name.endswith('.pending'): raise OSError('fixture failure')
            self.save(name,value)
        self.records={}; adapter=self.adapter(save=fail)
        # A fresh guard is necessary: prior terminal adapter forbids reuse.
        adapter.guard=CostGuard(ceiling_micro_cny=1000000,max_calls=48,input_rate=2,output_rate=8)
        with self.assertRaises(OSError): adapter(self.request())
        self.assertEqual(self.dispatched,[])

    def test_modified_old_assistant_output_rejected(self):
        adapter=self.adapter(); adapter(self.request())
        messages=deepcopy(self.envelope['messages'])+[
            dict(role='assistant',content=SQL),
            dict(role='user',content=serialize(dict(status='EXECUTED',complete=True)))]
        with self.assertRaises(ValueError): adapter(self.request(2,messages))
        self.assertEqual(len(self.dispatched),1)

    def test_invalid_action_is_terminal_but_not_repaired(self):
        self.script=iter([READ+' extra']); adapter=self.adapter()
        response=adapter(self.request())
        self.assertEqual(response['content'],READ+' extra')
        self.assertTrue(adapter.terminal)
        with self.assertRaises(RuntimeError): adapter(self.request())


if __name__=='__main__': unittest.main()
