"""Offline audit of scripted batches, including stopped and corrupt archives."""
import json
import unittest
from unittest.mock import patch

from runtime import bird_readonly_audit as audit
from runtime import student_club_readonly as scoped
from runtime import cache_route_symmetric_tools as symmetric
from runtime import cache_intent_tools as base
from runtime.student_club_batch_audit import inspect
from tests import test_student_club_trace_audit as trace_fixture


class Tests(unittest.TestCase):
    setUpClass=classmethod(trace_fixture.Tests.setUpClass.__func__)
    tearDownClass=classmethod(trace_fixture.Tests.tearDownClass.__func__)

    def sources(self):
        return {name:audit.hash_file(module.__file__) for name,module in [
            ('tool_source.py',symmetric),('base_tool_source.py',base),
            ('readonly_source.py',audit),('column_scoped_source.py',scoped)]}

    def check(self,label='out',**overrides):
        f=self.f;root=f.root/label
        args=dict(expected_plan_sha=audit.digest(f.plan),expected_archive_sha=audit.hash_file(root/'artifact_hashes.json'),
            native_counter=f.counter,plain_counter=f.plain,frozen_tool_sources=self.sources(),mode='SCRIPTED_NOT_MODEL')
        args.update(overrides)
        return inspect(root,f.plan,f.c,f.cache,**args)

    def test_complete_inventory_and_cross_replay_no_sql(self):
        with patch.object(scoped,'run_child',side_effect=AssertionError('Audit must not query database')):
            result=self.check()
        self.assertEqual(result['status'],'SEALED_STUDENT_CLUB_BATCH_REPLAY_VERIFIED')
        self.assertEqual(len(result['readouts']),12)
        self.assertEqual(result['cost_guard'],self.summary['cost_guard'])
        self.assertFalse(result['semantic_correctness_assessed'])

    def test_external_seal_and_frozen_source_refuse_changes(self):
        with self.assertRaises(ValueError):self.check(expected_archive_sha='0'*64)
        sources=self.sources();sources['column_scoped_source.py']='0'*64
        with self.assertRaises(ValueError):self.check(frozen_tool_sources=sources)
        with self.assertRaises(ValueError):self.check(mode='OFFICIAL_NATIVE_DEVELOPMENT')

    def test_extra_file_detected_without_resealing(self):
        p=self.f.root/'out'/'unexpected.json'
        try:
            p.write_bytes(b'{}')
            with self.assertRaises(ValueError):self.check()
        finally:p.unlink()

    def test_failed_batch_keeps_unstarted_not_successful_prefix_analysis(self):
        def factory(job,log):
            def fail(wire,timeout):raise TimeoutError('Scripted transport failure')
            return fail
        self.f.execute(label='stopped-audit',factory=factory)
        result=self.check('stopped-audit')
        self.assertEqual(result['status'],'SEALED_STOPPED_INVENTORY_ONLY')
        self.assertEqual(result['not_started'],11)
        self.assertFalse(result['accounting_verified'])
        self.assertFalse(result['effects_computed'])
        self.assertNotIn('readouts',result)

    def test_invalid_action_is_retained_and_not_relabelled_as_answer(self):
        first=self.f.plan['design']['jobs'][0]['id']
        def factory(job,log):
            if job!=first:return self.f.factory(job,log)
            return lambda wire,timeout:json.dumps(dict(model='deepseek-flash',
                usage=dict(prompt_tokens=100,completion_tokens=20),choices=[]))
        self.f.execute(label='invalid-audit',factory=factory)
        result=self.check('invalid-audit')
        self.assertEqual(result['readouts'][0]['loop_status'],'INVALID_ACTION_RESPONSE')
        self.assertEqual(result['readouts'][0]['tool_attempts'],0)
        self.assertEqual(len(result['readouts']),12)


if __name__=='__main__':unittest.main()
