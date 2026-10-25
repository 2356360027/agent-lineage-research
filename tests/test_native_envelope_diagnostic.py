import json
import unittest
from scripts.audit_native_envelope import inspect_envelope
from tests.test_native_action_interface import response


class Tests(unittest.TestCase):
    def test_optional_zero_without_modifying_input(self):
        for present in (False, True):
            b = response()
            if present: b['choices'][0]['message']['tool_calls'][0]['index'] = 0
            raw = json.dumps(b)
            result = inspect_envelope(raw)
            self.assertEqual(result['index_extension_present'], present)
            self.assertFalse(result['execution_authorized'])
            self.assertEqual(json.dumps(b), raw)

    def test_wrong_index_rejected(self):
        for value in (True, False, 0.0, '0', None, 1, -1):
            b = response(); b['choices'][0]['message']['tool_calls'][0]['index'] = value
            with self.assertRaises(ValueError): inspect_envelope(json.dumps(b))

    def test_unknown_envelope_field_rejected(self):
        b = response(); c = b['choices'][0]['message']['tool_calls'][0]
        c.update(index=0, unknown='x')
        with self.assertRaises(ValueError): inspect_envelope(json.dumps(b))

    def test_arguments_still_strict(self):
        for arg in ('{}', '{"text":"a","text":"b"}', '{"text":"a"}{}', '{"text":"a","x":0}'):
            b = response(arguments=arg); b['choices'][0]['message']['tool_calls'][0]['index'] = 0
            with self.assertRaises(ValueError): inspect_envelope(json.dumps(b))

    def test_multiple_calls_text_and_bad_finish_rejected(self):
        for kind in ('multiple', 'text', 'finish'):
            b = response(); c = b['choices'][0]; m = c['message']
            m['tool_calls'][0]['index'] = 0
            if kind == 'multiple': m['tool_calls'] *= 2
            elif kind == 'text': m['content'] = 'extra'
            else: c['finish_reason'] = 'length'
            with self.assertRaises(ValueError): inspect_envelope(json.dumps(b))


if __name__ == '__main__': unittest.main()
