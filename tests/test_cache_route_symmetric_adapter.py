"""Fake provider/usage only. Never calls official APIs or supplies a key."""
from copy import deepcopy
import unittest

from runtime.cache_route_symmetric_adapter import SymmetricAdapter
from runtime.cache_route_symmetric_capacity import SymmetricCapacity
from runtime.cache_route_symmetric_design import build
from runtime.cache_route_symmetric_loop import run
from runtime.cache_intent_observation_bound import serialize
from runtime.deepseek_trajectory_adapter import CostGuard, MODEL
from tests.test_cache_action_adapter import Counter
from tests import test_cache_route_symmetric_loop as loop_fixture
from tests.test_cache_route_symmetric_loop import Session, READ, SQL, ANSWER


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.harness=loop_fixture.Tests();self.harness.setUp()
        self.cell=self.harness.cell;self.common=self.harness.common
        self.counter=Counter();self.capacity=SymmetricCapacity(self.cell,self.common,self.counter)
        self.guard=CostGuard(ceiling_micro_cny=1000000,max_calls=16,input_rate=2,output_rate=8)
        self.now=0;self.dispatched=[];self.script=iter([READ,READ,READ,ANSWER])

    def transport(self,payload,timeout):
        self.dispatched.append(deepcopy(payload))
        self.assertGreater(timeout,0);self.assertLessEqual(timeout,90)
        item=next(self.script)
        if isinstance(item,BaseException):raise item
        return dict(model=MODEL,usage=dict(prompt_tokens=self.counter.chat(payload['messages']),
                    completion_tokens=self.counter.text(item)),
                    choices=[dict(message=dict(content=item),finish_reason='stop')])

    def adapter(self,transport=None,save=None):
        return SymmetricAdapter(counter=self.counter,capacity=self.capacity,transport=transport or self.transport,
            save=save or self.harness.save,guard=self.guard,deadline=600,clock=lambda:self.now)

    def request(self,index=1,messages=None):
        return dict(generation_id=f'g{index:03d}',
            stage='terminal' if index==4 else 'symmetric_tool_opportunity',
            messages=deepcopy(self.cell['messages']) if messages is None else messages)

    def test_full_symmetric_loop_all_cells_third_cache_or_sql(self):
        for cell in build(self.common):
            for third in (READ,SQL):
                self.setUp();self.cell=cell
                self.capacity=SymmetricCapacity(cell,self.common,self.counter)
                self.script=iter([READ,READ,third,ANSWER]);adapter=self.adapter()
                session=Session(cell,self.common)
                result=run(cell,self.common,session,adapter,self.harness.save,self.capacity.observation_bound(),
                           deadline=600,clock=lambda:self.now)
                self.assertEqual(result['status'],'ANSWERED')
                self.assertEqual(adapter.count,4)
                self.assertEqual(len(self.dispatched),4)
                self.assertTrue(adapter.terminal)
                self.assertFalse(self.guard.terminal)
                self.assertEqual(self.guard.snapshot()['uncertain_reserved_micro_cny'],0)
                self.assertEqual(self.dispatched[0]['messages'],cell['messages'])
                self.assertEqual(self.dispatched[3]['messages'][6]['content'],third)

    def test_bad_stage_and_expired_before_dispatch(self):
        for stage in ('exploration','recovery'):
            self.setUp();adapter=self.adapter()
            with self.assertRaises(ValueError):adapter(self.request()|{'stage':stage})
            self.assertEqual(self.dispatched,[])
            self.assertTrue(self.guard.terminal)
        self.setUp();adapter=self.adapter();self.now=600
        with self.assertRaises(TimeoutError):adapter(self.request())
        self.assertEqual(self.dispatched,[])

    def test_timeout_interrupt_uncertain_reservation_no_retry(self):
        for exc in (TimeoutError(),KeyboardInterrupt()):
            self.setUp();self.script=iter([exc]);adapter=self.adapter()
            with self.assertRaises(type(exc)):adapter(self.request())
            self.assertGreater(self.guard.snapshot()['uncertain_reserved_micro_cny'],0)
            self.assertIn('api-g001.failure',self.harness.records)
            with self.assertRaises(RuntimeError):adapter(self.request())
            self.assertEqual(len(self.dispatched),1)

    def test_provider_mismatch_preserved_and_counted_before_failure(self):
        for mode in ('input','output','model','missing_usage','bad_usage'):
            self.setUp()
            def mismatch(p,t):
                raw=self.transport(p,t)
                if mode=='input':raw['usage']['prompt_tokens']+=1
                elif mode=='output':raw['usage']['completion_tokens']=513
                elif mode=='model':raw['model']='unexpected'
                elif mode=='missing_usage':raw.pop('usage')
                else:raw['usage']['prompt_tokens']=True
                return raw
            adapter=self.adapter(mismatch)
            with self.assertRaises(ValueError):adapter(self.request())
            self.assertIn('api-g001.provider',self.harness.records)
            self.assertTrue(self.guard.terminal)
            self.assertEqual(len(self.dispatched),1)
            if mode in ('missing_usage','bad_usage'):
                self.assertGreater(self.guard.snapshot()['uncertain_reserved_micro_cny'],0)
            else:self.assertGreater(self.guard.accounted,0)

    def test_late_response_accounted_but_not_used(self):
        def late(p,t):
            raw=self.transport(p,t);self.now=601;return raw
        adapter=self.adapter(late)
        with self.assertRaises(TimeoutError):adapter(self.request())
        self.assertIn('api-g001.provider',self.harness.records)
        self.assertGreater(self.guard.accounted,0)
        self.assertTrue(self.guard.terminal)

    def test_pending_write_failure_no_transport_and_provider_write_failure_no_retry(self):
        for suffix in ('.pending','.provider'):
            self.setUp()
            def fail(name,value):
                if name.endswith(suffix):raise OSError('scripted disk failure')
                self.harness.save(name,value)
            adapter=self.adapter(save=fail)
            with self.assertRaises(OSError):adapter(self.request())
            self.assertEqual(len(self.dispatched),0 if suffix=='.pending' else 1)
            self.assertEqual(adapter.count,len(self.dispatched))
            self.assertTrue(self.guard.terminal)
            with self.assertRaises(RuntimeError):adapter(self.request())

    def test_raw_prior_assistant_output_must_be_preserved(self):
        adapter=self.adapter();adapter(self.request())
        messages=deepcopy(self.cell['messages'])+[
            dict(role='assistant',content=SQL),
            dict(role='user',content=serialize({'status':'EXECUTED'}))]
        with self.assertRaises(ValueError):adapter(self.request(2,messages))
        self.assertEqual(len(self.dispatched),1)

    def test_invalid_action_is_outcome_not_repaired_and_stops_only_trajectory(self):
        self.script=iter(['invalid response']);adapter=self.adapter()
        response=adapter(self.request())
        self.assertEqual(response['content'],'invalid response')
        self.assertTrue(adapter.terminal);self.assertFalse(self.guard.terminal)
        with self.assertRaises(RuntimeError):adapter(self.request())

    def test_unsupported_provider_fields_and_multiple_choices_rejected(self):
        for field in ('reasoning_content','tool_calls','function_call','refusal','choices'):
            self.setUp()
            def changed(p,t):
                raw=self.transport(p,t)
                if field=='choices':raw['choices']*=2
                else:raw['choices'][0]['message'][field]='unexpected'
                return raw
            adapter=self.adapter(changed)
            with self.assertRaises(ValueError):adapter(self.request())
            self.assertTrue(self.guard.terminal)
            self.assertIn('api-g001.provider',self.harness.records)

    def test_cost_limit_stops_before_transport(self):
        self.guard=CostGuard(ceiling_micro_cny=1,max_calls=16,input_rate=2,output_rate=8)
        adapter=self.adapter()
        with self.assertRaises(RuntimeError):adapter(self.request())
        self.assertEqual(self.dispatched,[])
        self.assertEqual(self.guard.calls,0)

    def test_truncated_response_remains_original_terminal_outcome(self):
        def truncated(p,t):
            raw=self.transport(p,t);raw['choices'][0]['finish_reason']='length';return raw
        adapter=self.adapter(truncated)
        response=adapter(self.request())
        self.assertEqual(response['finish_reason'],'length')
        self.assertEqual(response['content'],READ)
        self.assertTrue(adapter.terminal)
        self.assertFalse(self.guard.terminal)

    def test_shared_guard_blocks_next_trajectory_after_infrastructure_failure(self):
        self.script=iter([TimeoutError()]);adapter=self.adapter()
        with self.assertRaises(TimeoutError):adapter(self.request())
        self.harness.records={};self.script=iter([ANSWER])
        other=self.adapter()
        with self.assertRaises(RuntimeError):other(self.request())
        self.assertEqual(len(self.dispatched),1)


if __name__=='__main__':unittest.main()
