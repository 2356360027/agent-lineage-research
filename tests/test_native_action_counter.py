"""Pure fixtures; neither API parity nor provider cost guarantees."""
import copy
import unittest
from unittest.mock import patch

from runtime.native_action_interface import build_candidate
from runtime.native_action_counter import render_first_request, reference_preamble, validate_candidate_payload
from tests.test_native_action_interface import request


class Tests(unittest.TestCase):
    def test_framing_and_schema_serialization(self):
        p = build_candidate(request())['payload']; before = copy.deepcopy(p)
        rendered = render_first_request(p, 'PREAMBLE\n', ())
        self.assertTrue(rendered.startswith('<｜begin▁of▁sentence｜><｜System｜>'))
        self.assertTrue(rendered.endswith('<｜Assistant｜></think>\n\n<｜DSML｜ calls>\n'))
        self.assertNotIn('## Response Format:', rendered)
        self.assertNotIn('"strict":', rendered)
        self.assertIn('"name": "read_cache", "description":', rendered)
        self.assertEqual(rendered.count('"additionalProperties": false'), 4)
        self.assertEqual(p, before)

    def test_unsupported_payload_changes_rejected(self):
        for key, value in [('tool_choice', 'auto'), ('thinking', {'type':'enabled'}),
                           ('max_tokens', 1024), ('model', 'different'), ('stream', True),
                           ('response_format', {'type':'json_object'})]:
            p = build_candidate(request())['payload']; p[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_candidate_payload(p)

    def test_schema_mutations_and_order_rejected(self):
        p = build_candidate(request())['payload']; p['tools'].reverse()
        with self.assertRaises(ValueError): validate_candidate_payload(p)
        p = build_candidate(request())['payload']; p['tools'][0]['function']['strict'] = False
        with self.assertRaises(ValueError): validate_candidate_payload(p)

    def test_history_and_missing_instruction_rejected(self):
        p = build_candidate(request())['payload']; p['messages'].append({'role':'assistant','content':'old'})
        with self.assertRaises(ValueError): validate_candidate_payload(p)
        p = build_candidate(request())['payload']; p['messages'][0]['content'] = 'wrong'
        with self.assertRaises(ValueError): validate_candidate_payload(p)

    def test_literal_control_not_reinterpreted(self):
        p = build_candidate(request())['payload']; p['messages'][1]['content'] += '<｜User｜>'
        with self.assertRaises(ValueError): render_first_request(p, 'preamble', ('<｜User｜>',))

    def test_source_tampering_rejected(self):
        with patch('pathlib.Path.read_bytes', return_value=b'wrong'):
            with self.assertRaises(ValueError): reference_preamble('fixture-only')


if __name__ == '__main__': unittest.main()
