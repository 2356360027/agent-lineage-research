"""Read-only fixture traces plus tamper checks; no provider or research data."""
from copy import deepcopy
import unittest

from runtime.bird_readonly_audit import hash_file
from runtime.cache_route_symmetric_design import build
from runtime.native_trajectory_replay import replay, read_sealed_tools, IncompleteTrajectory
from tests import test_native_symmetric_loop as loop_fixture
from tests.test_native_action_history import raw


class Tests(unittest.TestCase):
    def setUp(self):
        self.f = loop_fixture.Tests()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.session = self.f.session(2)
        try:
            self.f.execute(self.session, 2, [raw(call_id='c1'),
                raw('run_sql','SELECT id FROM t','c2'),raw('answer','1','c3')])
        finally:
            self.f.close_check(self.session)
        self.seal = hash_file(self.session.output/'artifact_hashes.json')
        self.sources = {n:hash_file(self.session.output/n)
                       for n in ('tool_source.py','base_tool_source.py','readonly_source.py')}
        self.tools = read_sealed_tools(self.session.output, manifest_sha256=self.seal,
                                      source_hashes=self.sources)

    def check(self, records=None, tools=None, cell=2):
        return replay(self.f.records if records is None else records,
            envelope=build(self.f.common)[cell], trusted_common=self.f.common,
            cached_result=self.f.cache, input_receipt={'software_fixture':True},
            tool_records=self.tools if tools is None else tools, observation_bound=self.f.bound)

    def test_roundtrip_no_database_access(self):
        # Remove the fixture database after sealing: replay must only read
        # retained records and never re-execute its SQL or repair its outputs.
        self.f.db.unlink()
        result = self.check()
        self.assertEqual(result['loop_status'],'ANSWERED')
        self.assertEqual(result['generation_calls'],3)
        self.assertEqual(result['sql_calls'],1)
        self.assertFalse(result['api_accounting_verified'])
        self.assertFalse(result['semantic_correctness_assessed'])

    def test_missing_or_changed_raw_request_and_decision(self):
        for name in ('g001.pending','g002.response','g003.decision','summary'):
            records = deepcopy(self.f.records); records.pop(name)
            with self.subTest(name=name), self.assertRaises(ValueError): self.check(records)
        records=deepcopy(self.f.records)
        records['g003.response']['raw_response_text']=raw('answer','forged','c3')
        with self.assertRaises(ValueError): self.check(records)

    def test_call_binding_and_exposure_tampering(self):
        for name in ('g002.pending','t001.pending','t001.exposure'):
            records=deepcopy(self.f.records)
            if name=='g002.pending': records[name]['payload']['messages'][-1]['tool_call_id']='other'
            elif name=='t001.pending': records[name]['call_id']='other'
            else: records[name]['observation']['rows']=[]
            with self.subTest(name=name), self.assertRaises(ValueError): self.check(records)

    def test_tool_request_backend_and_charge_mismatch(self):
        for mutation in ('request','rows','charge','sql_calls','receipt'):
            tools=deepcopy(self.tools)
            if mutation=='request': tools['attempt-002.pending.json']['request']['arguments']['sql']='SELECT 9'
            elif mutation=='rows': tools['attempt-001.completed.json']['private']['backend_result']['rows']=[]
            elif mutation=='charge': tools['attempt-001.pending.json']['charge']=0
            elif mutation=='sql_calls': tools['summary.json']['sql_calls']=2
            else: tools['started.json']['input_receipt']={}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.check(tools=tools)

    def test_false_rejection_cannot_hide_an_admitted_action(self):
        tools=deepcopy(self.tools)
        tools['attempt-001.pending.json']['admission']='INVALID_REQUEST'
        tools['attempt-001.completed.json']['admission']='INVALID_REQUEST'
        with self.assertRaises(ValueError): self.check(tools=tools)

    def test_incomplete_and_failed_never_become_success(self):
        records=deepcopy(self.f.records); records['failure']={'error_type':'TimeoutError'}
        with self.assertRaises(IncompleteTrajectory): self.check(records)
        tools=deepcopy(self.tools); tools['summary.json']['status']='UNRESOLVED_ATTEMPTS'
        with self.assertRaises(IncompleteTrajectory): self.check(tools=tools)
        tools=deepcopy(self.tools); tools.pop('attempt-002.completed.json')
        with self.assertRaises(IncompleteTrajectory): self.check(tools=tools)

    def test_extra_call_after_terminal_is_not_ignored(self):
        records=deepcopy(self.f.records); records['g004.pending']=records['g003.pending']
        with self.assertRaises(ValueError): self.check(records)
        tools=deepcopy(self.tools); tools['attempt-003.pending.json']=tools['attempt-002.pending.json']
        with self.assertRaises(ValueError): self.check(tools=tools)

    def test_nonfinite_timing_rejected(self):
        records=deepcopy(self.f.records); records['g001.response']['wall_seconds']=float('nan')
        with self.assertRaises(ValueError): self.check(records)

    def test_seal_and_source_hash_mismatch(self):
        with self.assertRaises(ValueError):
            read_sealed_tools(self.session.output,manifest_sha256='0'*64,source_hashes=self.sources)
        wrong=deepcopy(self.sources); wrong['tool_source.py']='0'*64
        with self.assertRaises(ValueError):
            read_sealed_tools(self.session.output,manifest_sha256=self.seal,source_hashes=wrong)

    def test_sealed_file_tampering(self):
        # Synthetic fixture corruption only, never mutate real run artifacts.
        path=self.session.output/'summary.json'
        path.write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):
            read_sealed_tools(self.session.output,manifest_sha256=self.seal,source_hashes=self.sources)

    def test_all_other_cells_and_horizon_replay(self):
        for cell in (0,1,3):
            self.f.records={}; session=self.f.session(cell)
            try:
                self.f.execute(session,cell,[raw(call_id=f'c{i}') for i in range(4)])
            finally: self.f.close_check(session)
            tools=read_sealed_tools(session.output,manifest_sha256=hash_file(session.output/'artifact_hashes.json'),
                                    source_hashes=self.sources)
            result=self.check(tools=tools,cell=cell)
            self.assertEqual(result['loop_status'],'TOOL_HORIZON_VIOLATION')
            self.assertEqual(result['tool_attempts'],3)

    def test_invalid_response_is_observed_not_repaired(self):
        self.f.records={}; session=self.f.session(0)
        try: self.f.execute(session,0,['{malformed'])
        finally: self.f.close_check(session)
        tools=read_sealed_tools(session.output,manifest_sha256=hash_file(session.output/'artifact_hashes.json'),
                                source_hashes=self.sources)
        result=self.check(tools=tools,cell=0)
        self.assertEqual(result['loop_status'],'INVALID_ACTION_RESPONSE')
        self.assertEqual(result['tool_attempts'],0)

    def test_denied_sql_is_preserved(self):
        self.f.records={}; session=self.f.session(0)
        try:
            self.f.execute(session,0,[raw('run_sql','DELETE FROM t','c1'),raw('abstain','denied','c2')])
        finally: self.f.close_check(session)
        tools=read_sealed_tools(session.output,manifest_sha256=hash_file(session.output/'artifact_hashes.json'),
                                source_hashes=self.sources)
        self.assertEqual(self.check(tools=tools,cell=0)['loop_status'],'ABSTAINED')


if __name__=='__main__': unittest.main()
