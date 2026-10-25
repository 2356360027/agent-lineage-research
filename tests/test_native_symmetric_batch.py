"""Complete eight-cell synthetic integration. No API or research data."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_tools as base_tools
from runtime import cache_route_symmetric_tools as tool_module
from runtime import native_symmetric_batch as batch
from runtime.native_symmetric_batch_audit import inspect
from tests import test_native_symmetric_loop as fixture
from tests.test_native_trajectory_adapter import provider


class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.addCleanup(self.f.doCleanups);self.f.setUp()
        self.groups={family:dict(common=deepcopy(self.f.common),cache=deepcopy(self.f.cache),
            source_plan_sha256='a'*64,input_manifest_sha256='b'*64,admission_sha256='c'*64)
            for family in batch.FAMILIES}
        self.groups['strength']['common']['current_contract']='count entities, not distinct labels'
        self.plan=batch.build_plan(self.groups);self.sha=audit.digest(self.plan)
        self.native=Mock();self.native.inspect.return_value={'reference_input_tokens':100}
        self.plain=Mock();self.plain.chat.side_effect=lambda ms:len(ms[1]['content'])+20
        self.output=self.f.root/'batch-run';self.dispatch=[];self.now=0
        self.sources={'tool_source.py':audit.hash_file(tool_module.__file__),
            'base_tool_source.py':audit.hash_file(base_tools.__file__),
            'readonly_source.py':audit.hash_file(audit.__file__)}

    def factory(self,ident,save):
        n=0
        def transport(payload,timeout):
            nonlocal n
            n+=1;self.dispatch.append((ident,n))
            raw=(provider('read_cache','v000','c1') if n==1 else
                 provider('run_sql','SELECT id FROM t','c2') if n==2 else
                 provider('answer','1','c3'))
            save(f'transport-{n:03d}',dict(transport_status='RESPONSE_RECEIVED',response_text=raw))
            return raw
        return transport

    def run_batch(self,factory=None):
        return batch.run(self.plan,self.groups,expected_plan_sha=self.sha,database=self.f.db,
            output=self.output,native_counter=self.native,plain_counter=self.plain,
            transport_factory=factory or self.factory,deadline=600,mode='SCRIPTED_NOT_MODEL',clock=lambda:self.now)

    def inspect(self):
        return inspect(self.output,self.plan,self.groups,expected_plan_sha=self.sha,
            expected_archive_sha=audit.hash_file(self.output/'artifact_hashes.json'),mode='SCRIPTED_NOT_MODEL',
            native_counter=self.native,plain_counter=self.plain,frozen_tool_sources=self.sources)

    def test_all_eight_cells_complete_and_replay_after_db_removed(self):
        result=self.run_batch()
        self.assertEqual(result['status'],'SCRIPTED_NOT_MODEL_FINISHED')
        self.assertEqual(result['dispatch_attempts'],24)
        self.assertEqual(result['model_calls'],0)
        self.assertEqual(len(result['rows']),8)
        self.assertEqual({(j['family'],j['cell_index']) for j in self.plan['jobs']},
                         {(f,i) for f in batch.FAMILIES for i in range(4)})
        self.assertEqual(audit.hash_file(self.f.db),self.f.sha)
        self.f.db.unlink()
        replay=self.inspect()
        self.assertEqual(replay['status'],'SEALED_NATIVE_BATCH_REPLAY_VERIFIED')
        self.assertEqual(replay['reported_prompt_tokens'],2400)
        self.assertEqual(replay['cost_guard']['accounted_micro_cny'],8640)
        self.assertFalse(replay['C_type_drift_assessed'])

    def test_bad_count_stops_keeps_remaining_slots(self):
        def factory(ident,save):
            def transport(payload,timeout):
                self.dispatch.append(ident)
                raw=provider(usage=dict(prompt_tokens=101,completion_tokens=20))
                save('transport-001',dict(transport_status='RESPONSE_RECEIVED',response_text=raw))
                return raw
            return transport
        result=self.run_batch(factory)
        self.assertEqual(result['status'],'SCRIPTED_NOT_MODEL_STOPPED')
        self.assertEqual(len(self.dispatch),1)
        self.assertEqual(sum(r['status']=='NOT_STARTED' for r in result['rows']),7)
        inventory=self.inspect()
        self.assertEqual(inventory['status'],'SEALED_STOPPED_INVENTORY_ONLY')
        self.assertFalse(inventory['accounting_verified'])
        self.assertNotIn('readouts',inventory)

    def test_transport_failure_keeps_reserve_and_does_not_resume(self):
        def factory(ident,save):
            def transport(payload,timeout):
                self.dispatch.append(ident)
                save('transport-001',dict(transport_status='WORKER_TIMEOUT',billing_unknown=True))
                raise TimeoutError('fixture')
            return transport
        result=self.run_batch(factory)
        self.assertEqual(result['cost_guard']['uncertain_reserved_micro_cny'],4296)
        self.assertEqual(len(self.dispatch),1)
        self.assertEqual(self.inspect()['not_started'],7)
        with self.assertRaises(FileExistsError):self.run_batch(factory)
        self.assertEqual(len(self.dispatch),1)

    def test_invalid_format_is_outcome_not_retry_or_batch_failure(self):
        def factory(ident,save):
            def transport(payload,timeout):
                body=json.loads(provider());body['choices'][0]['message']['tool_calls'][0]['function']['arguments']='{}'
                raw=json.dumps(body);self.dispatch.append(ident)
                save('transport-001',dict(transport_status='RESPONSE_RECEIVED',response_text=raw))
                return raw
            return transport
        result=self.run_batch(factory)
        self.assertEqual(result['status'],'SCRIPTED_NOT_MODEL_FINISHED')
        self.assertEqual(len(self.dispatch),8)
        self.assertTrue(all(r['status']=='INVALID_ACTION_RESPONSE' for r in result['rows']))
        self.assertEqual(len(self.inspect()['readouts']),8)

    def test_preflight_rejects_selected_cells_and_changed_cache(self):
        self.plan['jobs'].pop()
        with self.assertRaises(ValueError):self.run_batch()
        self.assertFalse(self.output.exists())
        self.plan=batch.build_plan(self.groups)
        self.groups['positive']['cache']['columns']=['wrong_column']
        with self.assertRaises(ValueError):self.run_batch()
        self.assertFalse(self.output.exists())

    def test_initial_cap_and_changed_database_prevent_calls(self):
        self.native.inspect.return_value={'reference_input_tokens':8193}
        with self.assertRaises(ValueError):self.run_batch()
        self.assertFalse(self.output.exists())
        self.native.inspect.return_value={'reference_input_tokens':100}
        self.f.db.unlink()
        with self.assertRaises(ValueError):self.run_batch()
        self.assertFalse(self.output.exists())

    def test_tampered_raw_file_rejected_by_outer_manifest(self):
        self.run_batch()
        first=self.plan['jobs'][0]['id']
        (self.output/first/'api/transport-001.json').write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):self.inspect()

    def reseal_fixture(self):
        # Deliberately rehash this synthetic archive to exercise semantic
        # consistency checks beyond merely detecting changed file bytes.
        root=self.output/'batch'
        (root/'artifact_hashes.json').write_bytes(audit.encoded({p.name:audit.hash_file(p)
            for p in root.iterdir() if p.is_file() and p.name!='artifact_hashes.json'}))
        (self.output/'artifact_hashes.json').write_bytes(audit.encoded({p.relative_to(self.output).as_posix():audit.hash_file(p)
            for p in self.output.rglob('*') if p.is_file() and p!=self.output/'artifact_hashes.json'}))

    def test_resealed_false_cost_summary_still_rejected(self):
        self.run_batch()
        summary=json.loads((self.output/'summary.json').read_text('utf-8'))
        summary['cost_guard']['accounted_micro_cny']=0
        for path in (self.output/'summary.json',self.output/'batch/summary.json'):
            path.write_bytes(audit.encoded(summary))
        self.reseal_fixture()
        with self.assertRaises(ValueError):self.inspect()

    def test_resealed_unstarted_slot_cannot_hide_existing_work(self):
        self.run_batch()
        summary=json.loads((self.output/'summary.json').read_text('utf-8'))
        summary['rows'][0]={'id':self.plan['jobs'][0]['id'],'status':'NOT_STARTED','dispatch_attempts':0}
        for path in (self.output/'summary.json',self.output/'batch/summary.json'):
            path.write_bytes(audit.encoded(summary))
        self.reseal_fixture()
        with self.assertRaises(ValueError):self.inspect()

    def test_deadline_during_first_response_stops_later_work(self):
        def factory(ident,save):
            original=self.factory(ident,save)
            def transport(payload,timeout):
                result=original(payload,timeout);self.now=601;return result
            return transport
        self.run_batch(factory)
        self.assertEqual(len(self.dispatch),1)
        self.assertEqual(self.inspect()['not_started'],7)


if __name__=='__main__':unittest.main()
