import copy
import json
import unittest

from runtime.native_action_interface import (
    FIELDS, INSTRUCTION, build_candidate, parse_native_response, tool_definitions)
from runtime.cache_intent_staged_loop import ACTION_INSTRUCTION
from runtime.deepseek_official_transport import validate_payload


def request():
    return dict(model='deepseek-flash', thinking={'type': 'disabled'}, temperature=0,
                max_tokens=512, stream=False, response_format={'type': 'json_object'},
                messages=[dict(role='system', content='Context\n'+ACTION_INSTRUCTION),
                          dict(role='user', content='Synthetic task, no reference answer')])


def response(name='answer', arguments='{"text":"fixture"}'):
    return {'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
        'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'call-fixture', 'type': 'function',
             'function': {'name': name, 'arguments': arguments}}]}}]}


class Tests(unittest.TestCase):
    def test_candidate_preserves_context_and_settings(self):
        source = request(); before = copy.deepcopy(source)
        draft = build_candidate(source); payload = draft['payload']
        self.assertEqual(source, before)
        self.assertEqual(payload['messages'][1], source['messages'][1])
        self.assertEqual(payload['messages'][0]['content'].replace(INSTRUCTION, ACTION_INSTRUCTION),
                         source['messages'][0]['content'])
        for key in ('model', 'thinking', 'temperature', 'max_tokens', 'stream'):
            self.assertEqual(source[key], payload[key])
        self.assertEqual(payload['tool_choice'], 'required')
        self.assertNotIn('response_format', payload)
        for key in ('inference_authorized', 'transport_available', 'token_bound_verified', 'SQL_execution_enabled'):
            self.assertFalse(draft[key])
        with self.assertRaises(ValueError): validate_payload(payload)

    def test_all_four_actions_including_terminal_choices(self):
        values = {'read_cache': 'v000', 'run_sql': 'SELECT 1', 'answer': 'fixture', 'abstain': 'insufficient'}
        for name, field in FIELDS.items():
            with self.subTest(name=name):
                parsed = parse_native_response(json.dumps(response(name, json.dumps({field: values[name]}))))
                self.assertEqual(parsed['action'], {'kind': name, field: values[name]})
                self.assertFalse(parsed['execution_authorized'])
                self.assertFalse(parsed['semantic_correctness_assessed'])

    def test_schemas_and_fresh_copy(self):
        tools = tool_definitions()
        self.assertEqual([t['function']['name'] for t in tools], list(FIELDS))
        for tool in tools:
            f = tool['function']; p = f['parameters']
            self.assertTrue(f['strict']); self.assertFalse(p['additionalProperties'])
            self.assertEqual(p['required'], list(p['properties']))
        tools[0]['function']['name'] = 'changed'
        self.assertEqual(tool_definitions()[0]['function']['name'], 'read_cache')

    def test_full_argument_rejection(self):
        for arg in ('{}', '[]', 'null', '{"text":""}', '{"text":"  "}', '{"text":5}',
                    '{"text":"a","text":"b"}', '{"text":"a","extra":"b"}',
                    '{"text":"a"}{"text":"a"}', '{"text":"a"}\' }', '{"text":NaN}'):
            with self.subTest(arg=arg), self.assertRaises(ValueError):
                parse_native_response(json.dumps(response(arguments=arg)))

    def test_unknown_name_cache_and_argument_type(self):
        for name, arg in [('delete', '{}'), ('read_cache', '{"cache_id":"v001"}'),
                          ('answer', {'text': 'not a string envelope'}), ('answer', None)]:
            with self.subTest(name=name, arg=arg), self.assertRaises(ValueError):
                parse_native_response(json.dumps(response(name, arg)))

    def test_no_first_call_selection(self):
        for count in (0, 2):
            body = response(); message = body['choices'][0]['message']
            message['tool_calls'] *= count
            with self.assertRaises(ValueError): parse_native_response(json.dumps(body))

    def test_no_text_fallback_or_reasoning(self):
        for key, value in [('content', 'extra'), ('content', ' '), ('reasoning_content', 'thought')]:
            body = response(); body['choices'][0]['message'][key] = value
            with self.assertRaises(ValueError): parse_native_response(json.dumps(body))

    def test_abnormal_finish_and_multiple_choices(self):
        for finish in ('stop', 'length', 'content_filter', 'aborted', 'insufficient_system_resource'):
            body = response(); body['choices'][0]['finish_reason'] = finish
            with self.assertRaises(ValueError): parse_native_response(json.dumps(body))
        for count in (0, 2):
            body = response(); body['choices'] *= count
            with self.assertRaises(ValueError): parse_native_response(json.dumps(body))

    def test_raw_provider_duplicate_trailing_and_non_object(self):
        raw = json.dumps(response())
        for bad in (raw+raw, raw+'junk', '[]', '{"choices":[],"choices":[]}',
                    '{"error":"failure"}', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError): parse_native_response(bad)

    def test_bad_first_request(self):
        for text in ('absent', ACTION_INSTRUCTION*2, ACTION_INSTRUCTION+INSTRUCTION):
            source = request(); source['messages'][0]['content'] = text
            with self.assertRaises(ValueError): build_candidate(source)
        source = request(); source['messages'].append({'role': 'assistant', 'content': 'prior'})
        with self.assertRaises(ValueError): build_candidate(source)


if __name__ == '__main__':
    unittest.main()
