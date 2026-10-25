"""Synthetic format fixtures motivated by failures; NOT research observations."""
from copy import deepcopy
import json
import unittest

from runtime.cache_intent_staged_loop import parse_action
from runtime.cache_action_preview import payload
from scripts.audit_action_json_interface import diagnose
from tests import test_cache_route_symmetric_loop as loop_fixture


SQL = '{"kind":"run_sql","sql":"SELECT 1"}'
BAD = (SQL+SQL, SQL+"'}", SQL+' trailing',
       '{"kind":"run_sql","kind":"answer","sql":"SELECT 1"}',
       '['+SQL+']', '```json\n'+SQL+'\n```',
       '{"kind":"run_sql","sql":"SELECT 1","extra":true}')


class FormatRegression(unittest.TestCase):
    def test_invalid_first_response_never_dispatches_or_retries(self):
        for raw in BAD:
            with self.subTest(raw=raw):
                harness = loop_fixture.Tests(); harness.setUp(); harness.script = iter([raw])
                result = harness.execute()
                self.assertEqual(result['status'], 'INVALID_ACTION')
                self.assertEqual(len(harness.requests), 1)
                self.assertEqual(harness.session.requests, [])
                self.assertEqual(harness.records['g001.response']['response']['content'], raw)
                self.assertEqual(result['tool_attempts'], 0)

    def test_one_object_and_surrounding_whitespace_still_valid(self):
        for raw in (SQL, ' \n'+SQL+'\t '):
            self.assertEqual(parse_action(raw), dict(kind='run_sql', sql='SELECT 1'))

    def fixture(self, content=SQL+SQL):
        harness = loop_fixture.Tests(); harness.setUp()
        request = payload(harness.cell['messages'])
        raw = dict(choices=[dict(message=dict(content=content), finish_reason='stop')],
                   usage=dict(completion_tokens=32))
        from runtime.cache_route_symmetric_design import classify
        return [request, dict(transport_status='RESPONSE_RECEIVED', response_text=json.dumps(raw)),
                dict(raw=deepcopy(raw)), dict(response=dict(content=content, finish_reason='stop')),
                classify(content, 1)]

    def test_diagnostic_preserves_invalid_disposition(self):
        result = diagnose(*self.fixture())
        self.assertFalse(result['syntax']['valid_single_json'])
        self.assertEqual(result['syntax']['error'], 'Extra data')
        self.assertFalse(result['dispatch_tool'])
        self.assertTrue(result['json_mode_enabled'])
        self.assertTrue(result['canonical_action_examples_present'])

    def test_mismatch_rejected_instead_of_silent_repair(self):
        for index in (2, 3, 4):
            records = self.fixture()
            if index == 2: records[2]['raw']['choices'][0]['message']['content'] = SQL
            elif index == 3: records[3]['response']['content'] = SQL
            else: records[4]['status'] = 'TOOL_PROPOSED'
            with self.assertRaises(ValueError): diagnose(*records)


if __name__ == '__main__':
    unittest.main()
