"""Synthetic regression tests; no API, tool execution or historical rescore."""
import json
import unittest
from unittest.mock import patch

from runtime.native_action_interface import (
    ENVELOPE_POLICY_V2, parse_native_response, parse_native_response_v2)
from tests import test_native_action_interface as legacy


class InheritedSafetyTests(legacy.Tests):
    # Apply every original malformed-argument/envelope/choice/finish fixture to
    # the explicitly selected new parser as well as the original test module.
    def setUp(self):
        self.patcher = patch.object(legacy, 'parse_native_response', parse_native_response_v2)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)


class IndexExtensionTests(unittest.TestCase):
    def raw(self, value=0, **extra):
        body = legacy.response()
        body['choices'][0]['message']['tool_calls'][0].update(index=value, **extra)
        return json.dumps(body)

    def test_v1_still_rejects_extension(self):
        with self.assertRaises(ValueError): parse_native_response(self.raw())

    def test_v2_accepts_only_observed_extension(self):
        for raw in (self.raw(), json.dumps(legacy.response())):
            result = parse_native_response_v2(raw)
            self.assertEqual(result['envelope_policy'], ENVELOPE_POLICY_V2)
            self.assertEqual(result['action'], {'kind':'answer', 'text':'fixture'})
            self.assertFalse(result['execution_authorized'])
            self.assertFalse(result['semantic_correctness_assessed'])

    def test_index_is_exact_integer_zero(self):
        for value in (False, True, 0.0, '0', None, -1, 1, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_native_response_v2(self.raw(value))

    def test_unknown_metadata_not_ignored(self):
        with self.assertRaises(ValueError):
            parse_native_response_v2(self.raw(extra='not allowed'))

    def test_index_does_not_allow_argument_repair(self):
        for arg in ('{"text":"a"}{}', '{"text":"a","text":"b"}', '{"text":"a","x":1}', '{"text":NaN}'):
            b = legacy.response(arguments=arg)
            b['choices'][0]['message']['tool_calls'][0]['index'] = 0
            with self.assertRaises(ValueError): parse_native_response_v2(json.dumps(b))

    def test_duplicate_index_rejected_before_selection(self):
        raw = self.raw().replace('"index": 0}', '"index": 0, "index": 0}')
        self.assertNotEqual(raw, self.raw())
        with self.assertRaises(ValueError): parse_native_response_v2(raw)

    def test_multiple_index_zero_calls_not_selected(self):
        b = json.loads(self.raw())
        b['choices'][0]['message']['tool_calls'] *= 2
        with self.assertRaises(ValueError): parse_native_response_v2(json.dumps(b))


if __name__ == '__main__': unittest.main()
