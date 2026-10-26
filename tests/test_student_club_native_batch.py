"""Fixed-grid replay on synthetic SQLite; no network and no model inference."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock,patch

from runtime import bird_readonly_audit as audit
from runtime import student_club_readonly as scoped
from runtime.cache_intent_journal import verify
from runtime.student_club_native_batch import build_plan,run
from runtime.student_club_native_wire import payload
from scripts.check_student_club_reference import ENTITY_H,ENTITY_C
from tests.test_student_club_diagnostic_design import common
from tests.test_student_club_native_accounting import provider
from tests import test_student_club_readonly as fixture


class Tests(unittest.TestCase):
    setUp=fixture.Tests.setUp

    def inputs(self):
        c=common()|{'database_sha256':self.before,'history_sql':ENTITY_H}
        cache=scoped.execute(self.db,ENTITY_H);cache['sql_sha256']=audit.digest(ENTITY_H)
        self.c,self.cache=c,cache
        self.plan=build_plan(c,cache)
        self.counter=Mock()
        self.counter.inspect.side_effect=lambda r:dict(reference_input_tokens=100,wire_payload_sha256=audit.digest(payload(r)))
        self.plain=Mock();self.plain.chat.return_value=100
        self.seen=[]

    def factory(self,job_id,log):
        outputs=[]
        if not job_id.startswith('ordinary-'):
            outputs.append(provider('record_definition','SYNTHETIC description','d'))
        outputs += [provider('read_cache','v000','r1'),provider('run_sql',ENTITY_C,'q'),
                    provider('read_cache','v000','r2'),provider('answer','1','end')]
        it=iter(outputs)
        def transport(wire,timeout):
            self.seen.append((job_id,deepcopy(wire)))
            return next(it)
        return transport

    def execute(self,label='out',factory=None,**kw):
        args=dict(expected_plan_sha=audit.digest(self.plan),database=self.db,output=self.root/label,
            native_counter=self.counter,plain_counter=self.plain,transport_factory=factory or self.factory,
            deadline=500,mode='SCRIPTED_NOT_MODEL',clock=lambda:0)
        args.update(kw)
        return run(self.plan,self.c,self.cache,**args)

    def test_all_twelve_full_horizons_shared_costs_and_seals(self):
        self.inputs();result=self.execute()
        self.assertEqual(result['status'],'SCRIPTED_NOT_MODEL_FINISHED')
        self.assertEqual((result['dispatch_attempts'],result['tool_attempts']),(56,36))
        self.assertEqual(result['cost_guard']['accounted_micro_cny'],56*360)
        self.assertEqual(result['cost_guard']['uncertain_reserved_micro_cny'],0)
        self.assertEqual(result['real_model_calls'],0)
        self.assertEqual(result['semantic_labels'],0)
        self.assertEqual([r['id'] for r in result['rows']],[j['id'] for j in self.plan['design']['jobs']])
        self.assertTrue(all(r['status']=='ANSWERED' for r in result['rows']))
        self.assertNotIn('PRIVATE_',str(self.seen))
        verify(self.root/'out/batch')
        for row,job in zip(result['rows'],self.plan['design']['jobs']):
            root=self.root/'out'/row['id']
            for part in ('api','loop','tools'):verify(root/part)
            self.assertEqual(row['tools']['spent_declared_units'],10+(2*job['price'] if job['available'] else 0))
        manifest=json.loads((self.root/'out/artifact_hashes.json').read_text())
        for rel,digest in manifest.items():self.assertEqual(audit.hash_file(self.root/'out'/rel),digest)
        self.assertEqual(audit.hash_file(self.db),self.before)

    def test_failure_retains_uncertain_reserve_and_unstarted_cells(self):
        self.inputs();calls=[]
        def factory(job,log):
            def fail(wire,timeout):
                calls.append(job);raise TimeoutError('scripted failure')
            return fail
        result=self.execute(factory=factory)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['rows'][0]['status'],'FAILED')
        self.assertTrue(all(r['status']=='NOT_STARTED' for r in result['rows'][1:]))
        self.assertEqual(result['cost_guard']['uncertain_reserved_micro_cny'],4296)
        self.assertEqual(result['dispatch_attempts'],1)
        verify(self.root/'out/batch')
        self.assertTrue((self.root/'out'/calls[0]/'api/api-g001.pending.json').exists())
        with self.assertRaises(FileExistsError):self.execute(factory=factory)
        self.assertEqual(len(calls),1)

    def test_invalid_action_retained_not_replaced_other_cells_continue(self):
        self.inputs();first=self.plan['design']['jobs'][0]['id']
        def factory(job,log):
            if job!=first:return self.factory(job,log)
            def invalid(wire,timeout):
                self.seen.append((job,wire))
                return json.dumps(dict(model='deepseek-flash',usage=dict(prompt_tokens=100,completion_tokens=20),choices=[]))
            return invalid
        result=self.execute(factory=factory)
        self.assertEqual(result['rows'][0]['status'],'INVALID_ACTION_RESPONSE')
        self.assertEqual(result['rows'][0]['dispatch_attempts'],1)
        self.assertTrue(all(r['status']=='ANSWERED' for r in result['rows'][1:]))
        self.assertEqual(result['rows'][0]['tool_attempts'],0)

    def test_count_mismatch_stops_global_without_offset_or_replacement(self):
        self.inputs()
        factory=lambda job,log:lambda wire,timeout:provider('answer','1',usage=dict(prompt_tokens=101,completion_tokens=20))
        result=self.execute(factory=factory)
        self.assertEqual(result['dispatch_attempts'],1)
        self.assertTrue(result['cost_guard']['terminal'])
        self.assertEqual(result['cost_guard']['accounted_micro_cny'],362)
        self.assertEqual(sum(r['status']=='NOT_STARTED' for r in result['rows']),11)

    def test_late_success_is_accounted_but_no_next_cell_starts(self):
        self.inputs();now=[0]
        def factory(job,log):
            def late(wire,timeout):
                now[0]=501
                return provider('answer','1')
            return late
        result=self.execute(factory=factory,clock=lambda:now[0])
        self.assertEqual(result['dispatch_attempts'],1)
        self.assertEqual(result['cost_guard']['accounted_micro_cny'],360)
        self.assertEqual(result['tool_attempts'],0)
        self.assertEqual(sum(r['status']=='NOT_STARTED' for r in result['rows']),11)
        root=self.root/'out'/result['rows'][0]['id']
        self.assertTrue((root/'api/api-g001.provider.json').exists())

    def test_finalization_error_stops_remaining_cells_without_erasing_files(self):
        self.inputs()
        factory=lambda job,log:lambda wire,timeout:provider('abstain','synthetic')
        with patch.object(scoped.ToolSession,'close',side_effect=OSError('synthetic archive failure')):
            result=self.execute(factory=factory)
        self.assertEqual(result['rows'][0]['status'],'FINALIZATION_FAILED')
        self.assertEqual(sum(r['status']=='NOT_STARTED' for r in result['rows']),11)
        root=self.root/'out'/result['rows'][0]['id']
        self.assertTrue((root/'tools/started.json').exists())
        self.assertFalse((root/'tools/artifact_hashes.json').exists())
        verify(root/'api');verify(root/'loop');verify(self.root/'out/batch')

    def test_plan_tampering_and_capacity_fail_before_output_or_dispatch(self):
        self.inputs();original=deepcopy(self.plan)
        self.plan['design']['jobs'].reverse()
        with self.assertRaises(ValueError):self.execute()
        self.plan=original
        self.counter.inspect.side_effect=lambda r:dict(reference_input_tokens=8192,wire_payload_sha256=audit.digest(payload(r)))
        with self.assertRaises(ValueError):self.execute()
        self.assertFalse((self.root/'out').exists())
        self.assertEqual(self.seen,[])


if __name__=='__main__':unittest.main()
