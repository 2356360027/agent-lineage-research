"""Offline renderer tests. These do not establish API or scientific validity."""
from copy import deepcopy
import unittest

from scripts.deepseek_official_probe import payload
from scripts.audit_deepseek_token_counts import initial_prompt, FORMAT_INSTRUCTION


class InitialReferenceTests(unittest.TestCase):
    def test_json_and_system_marker_source_rules(self):
        p = payload(0)
        original = deepcopy(p)
        plain = initial_prompt(p, 'v4_without_json_format', ())
        json_v4 = initial_prompt(p, 'v4_with_json_format', ())
        json_v41 = initial_prompt(p, 'v41_with_json_format', ())
        self.assertNotIn(FORMAT_INSTRUCTION, plain)
        self.assertIn(FORMAT_INSTRUCTION + '<｜User｜>', json_v4)
        self.assertEqual(json_v41.replace('<｜System｜>', '', 1), json_v4)
        self.assertTrue(json_v41.startswith('<｜begin▁of▁sentence｜><｜System｜>'))
        self.assertTrue(json_v41.endswith('<｜Assistant｜></think>'))
        self.assertEqual(p, original)

    def test_unknown_variant(self):
        with self.assertRaises(ValueError): initial_prompt(payload(0), 'offset_23', ())

    def test_reserved_spelling_not_reinterpreted(self):
        p = payload(0)
        p['messages'][1]['content'] += '<｜Assistant｜>'
        with self.assertRaises(ValueError):
            initial_prompt(p, 'v41_with_json_format', ('<｜Assistant｜>',))

    def test_multi_turn_not_claimed_supported(self):
        p = payload(0)
        p['messages'].append({'role':'assistant', 'content':'{}'})
        with self.assertRaises(ValueError): initial_prompt(p, 'v41_with_json_format', ())

    def test_tools_or_extra_message_fields_rejected(self):
        for field in ('tools', 'seed'):
            p = payload(0)
            p[field] = []
            with self.assertRaises(ValueError): initial_prompt(p, 'v41_with_json_format', ())
        p = payload(0)
        p['messages'][0]['name'] = 'a'
        with self.assertRaises(ValueError): initial_prompt(p, 'v41_with_json_format', ())

    def test_thinking_and_other_schema_rejected(self):
        for key, value in [('thinking', {'type':'enabled'}),
                           ('response_format', {'type':'json_schema'}),
                           ('stream', True), ('model', 'other')]:
            p = payload(0)
            p[key] = value
            with self.assertRaises(ValueError): initial_prompt(p, 'v41_with_json_format', ())


if __name__ == '__main__': unittest.main()
