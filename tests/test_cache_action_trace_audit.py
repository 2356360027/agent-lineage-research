"""Synthetic replay tests, not research calls or human correctness labels."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from runtime.cache_action_trace_audit import replay, finished_batch
from runtime.deepseek_trajectory_adapter import CostGuard
from tests import test_cache_action_batch as fixture_module


class TraceAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.BatchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def load(self, job):
        root=self.fixture.root/'batch-output'/job['id']
        def records(folder):
            return {p.stem:json.loads(p.read_text('utf-8')) for p in folder.glob('*.json')
                    if p.name != 'artifact_hashes.json'}
        return records(root/'trace'), records(root/'tools')

    def guard(self):
        return CostGuard(ceiling_micro_cny=500000,max_calls=48,input_rate=2,output_rate=8)

    def check(self, job, trace, tools, guard=None, mode='SCRIPTED_NOT_MODEL'):
        with (patch('runtime.bird_readonly_audit.run_child',side_effect=AssertionError('No SQL in replay')),
              patch('runtime.deepseek_official_transport.invoke',side_effect=AssertionError('No API in replay'))):
            return replay(job,trace,tools,self.fixture.caches[job['history_id']],
                          self.fixture.counter,guard or self.guard(),mode=mode)

    def test_all_slots_replay_without_queries_or_generations(self):
        summary=self.fixture.execute(); guard=self.guard(); outputs=[]
        for job in self.fixture.plan['jobs']:
            trace,tools=self.load(job)
            outputs.append(self.check(job,trace,tools,guard))
        self.assertEqual(guard.snapshot(),summary['cost_guard'])
        self.assertEqual(sum(r['dispatch_attempts'] for r in outputs),24)
        self.assertEqual(sum(r['successful_cache_reads'] for r in outputs),6)
        self.assertEqual(sum(r['successful_SQL_results'] for r in outputs),6)
        self.assertTrue(all(r['final_answer']=='1' and r['semantic_labels']==0 for r in outputs))

    def test_invalid_action_not_repaired_or_dropped(self):
        self.fixture.responses=[fixture_module.READ+' extra']; self.fixture.execute()
        job=self.fixture.plan['jobs'][0]; trace,tools=self.load(job)
        result=self.check(job,trace,tools)
        self.assertEqual(result['final_status'],'INVALID_ACTION')
        self.assertIsNone(result['final_answer'])

    def test_changed_raw_answer_or_exposed_observation_rejected(self):
        self.fixture.execute(); job=self.fixture.plan['jobs'][0]; trace,tools=self.load(job)
        changed=deepcopy(trace); changed['g002.response']['response']['content']='replaced'
        with self.assertRaises(ValueError): self.check(job,changed,tools)
        changed=deepcopy(trace); changed['t001.exposure']['observation']['remaining_budget']=0
        with self.assertRaises(ValueError): self.check(job,changed,tools)

    def test_admission_cost_and_unrecorded_attempt_rejected(self):
        self.fixture.execute(); job=self.fixture.plan['jobs'][0]; trace,tools=self.load(job)
        changed=deepcopy(tools); changed['attempt-001.pending']['charge']=0
        with self.assertRaises(ValueError): self.check(job,trace,changed)
        changed=deepcopy(tools); changed['attempt-002.pending']={}
        with self.assertRaises(ValueError): self.check(job,trace,changed)

    def test_backend_row_mutation_not_hidden_by_same_exposed_text(self):
        self.fixture.execute(); job=self.fixture.plan['jobs'][0]
        trace,tools=self.load(job)
        self.check(job,trace,tools)
        tools['attempt-001.completed']['private']['backend_result']['rows'][0][0][1]='9'
        with self.assertRaises(ValueError): self.check(job,trace,tools)

    def test_unavailable_cache_rejection_still_consumes_attempt(self):
        self.fixture.responses=[fixture_module.READ,fixture_module.ANSWER]; self.fixture.execute()
        guard=self.guard()
        for job in self.fixture.plan['jobs']:
            trace,tools=self.load(job); result=self.check(job,trace,tools,guard)
            if not job['available']:
                self.assertEqual(result['failed_cache_reads'],1)
                self.assertEqual(result['spent_declared_units'],0)
                self.assertEqual(result['tool_attempts'],1)

    def test_saved_transport_json_binding_in_official_mode(self):
        self.fixture.execute(); job=self.fixture.plan['jobs'][0]; trace,tools=self.load(job)
        trace['backend']['kind']='OFFICIAL_DEVELOPMENT'
        # Artificial transport records are test fixtures, never official evidence.
        for n in (1,2):
            trace[f'transport-{n:03d}']=dict(transport_status='RESPONSE_RECEIVED',
                response_text=json.dumps(trace[f'api-g{n:03d}.provider']['raw']))
        self.assertEqual(self.check(job,trace,tools,mode='OFFICIAL_DEVELOPMENT')['final_status'],'ANSWERED')
        trace['transport-001']['response_text']='{}'
        with self.assertRaises(ValueError): self.check(job,trace,tools,mode='OFFICIAL_DEVELOPMENT')

    def test_error_or_unsealed_record_not_assumed_finished(self):
        self.fixture.execute(); job=self.fixture.plan['jobs'][0]; trace,tools=self.load(job)
        trace['g003.failure']={'error_type':'fixture'}
        with self.assertRaises(ValueError): self.check(job,trace,tools)

    def audit_batch(self):
        return finished_batch(self.fixture.root/'batch-output',self.fixture.plan,self.fixture.caches,
            self.fixture.counter,expected_plan_sha256=fixture_module.audit.digest(self.fixture.plan),
            mode='SCRIPTED_NOT_MODEL')

    def test_full_sealed_batch_reconstructs_cost_and_keeps_all_slots(self):
        self.fixture.execute()
        with patch('runtime.bird_readonly_audit.run_child',side_effect=AssertionError('No SQL')):
            result=self.audit_batch()
        self.assertEqual(result['status'],'SEALED_FINISHED_TRACE_REPLAY_VERIFIED')
        self.assertEqual(len(result['rows']),12)
        self.assertEqual(result['independent_task_units'],2)
        self.assertEqual(result['semantic_labels'],0)

    def test_seal_mutation_and_stopped_batch_not_graded_as_complete(self):
        self.fixture.execute()
        path=self.fixture.root/'batch-output/summary.json'
        path.write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError): self.audit_batch()

    def test_stopped_batch_retained_not_success_prefix_audited(self):
        self.fixture.responses=[TimeoutError()]; self.fixture.execute()
        with self.assertRaisesRegex(ValueError,'Not a finished batch'): self.audit_batch()
        self.assertTrue((self.fixture.root/'batch-output/summary.json').is_file())

    def test_native_backend_has_no_independent_sql_hash(self):
        self.fixture.cache=deepcopy(self.fixture.cache)
        self.fixture.cache.pop('sql_sha256')
        self.fixture.execute()
        result=self.audit_batch()
        self.assertEqual(sum(r['successful_SQL_results'] for r in result['rows']),6)


if __name__=='__main__': unittest.main()
