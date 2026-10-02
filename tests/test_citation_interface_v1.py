"""CPU-only interface fixtures; no model calls or scientific observations."""
from copy import deepcopy
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

from runtime import citation_interface_v1 as c
from runtime import natural_repetition as n
from runtime.e1 import canonical
from runtime.natural_qa_data import prediction_view


def fixture():
    return prediction_view(dict(
        _id='interface-fixture-not-a-dataset-item',
        question='Where was Ada born? Unicode source: \u6b66\u6c49.',
        context=[('Ada', ['Ada was born in London.', 'Ada worked on a machine.']),
                 ('Other source', ['Another person lived in \u6b66\u6c49.'])]))


def config():
    return dict(model='fixture', temperature=0., top_p=1., max_tokens=256)


def envelope(content, finish='stop'):
    return dict(model='fixture', choices=[dict(
        finish_reason=finish, message=dict(content=content))])


def content(answer='', citations=None):
    return json.dumps(dict(answer=answer, citations=[] if citations is None else citations))


class FakeTokenizer:
    def apply_chat_template(self, messages, tokenize, add_generation_prompt):
        return [1] * 20


class FakeClient:
    """Records requests in memory and returns only a fixed synthetic assessment."""
    def __init__(self):
        self.config = config()
        self.calls = []

    def call(self, key, messages, seed, decoder, visible):
        self.calls.append(dict(key=key, messages=deepcopy(messages), seed=seed,
                               decoder=decoder, visible=set(visible)))
        return c.evaluate(envelope(content()), 'fixture', 'fixture', set(visible))


class CitationPromptTests(unittest.TestCase):
    def test_original_prompt_is_byte_exact_in_both_contexts(self):
        view = fixture()
        for units in (view['units'], []):
            with self.subTest(context='full' if units else 'question_only'):
                expected = canonical(n.prompt(view, units)).encode('utf-8')
                actual = canonical(c.prompt(view, units, 'original')).encode('utf-8')
                self.assertEqual(actual, expected)
                self.assertEqual(c.prompt(view, units), n.prompt(view, units))

    def test_explicit_prompt_only_appends_system_contract(self):
        view = fixture()
        for units in (view['units'], []):
            original = n.prompt(view, units)
            explicit = c.prompt(view, units, 'explicit')
            self.assertEqual(len(explicit), 2)
            self.assertEqual(explicit[1], original[1])
            self.assertEqual(explicit[0]['role'], original[0]['role'])
            self.assertTrue(explicit[0]['content'].startswith(original[0]['content']))
            suffix = explicit[0]['content'][len(original[0]['content']):]
            self.assertTrue(suffix.strip())
            self.assertIn('citation', suffix.lower())
            self.assertIn('bracket', suffix.lower())
            self.assertRegex(suffix, r'\{\s*"answer"\s*:\s*""\s*,\s*'
                                    r'"citations"\s*:\s*\[\s*\]\s*\}')

    def test_gold_and_extra_fields_never_enter_prompts_or_schema(self):
        view = fixture()
        polluted = deepcopy(view)
        polluted.update(answer='SECRET_GOLD', supporting_facts=['SECRET_SUPPORT'],
                        arm='SECRET_ARM', label='SECRET_LABEL')
        for unit in polluted['units']:
            unit['gold'] = 'SECRET_UNIT_GOLD'
        for variant in ('original', 'explicit'):
            for units, extra_units in ((view['units'], polluted['units']), ([], [])):
                expected = c.prompt(view, units, variant)
                actual = c.prompt(polluted, extra_units, variant)
                self.assertEqual(actual, expected)
                self.assertNotIn('SECRET', canonical(actual))
        visible = [u['id'] for u in polluted['units']]
        self.assertNotIn('SECRET', canonical(c.schema(visible)))
        self.assertEqual(c.schema(visible), c.schema(u['id'] for u in view['units']))

    def test_unknown_prompt_variant_rejected(self):
        with self.assertRaises(ValueError):
            c.prompt(fixture(), [], 'silently-fallback')


class CitationSchemaTests(unittest.TestCase):
    def test_schema_contract_and_visible_ids_only(self):
        visible = [u['id'] for u in fixture()['units']]
        schema = c.schema(visible + visible[:1])
        self.assertEqual(schema['type'], 'object')
        self.assertEqual(set(schema['required']), {'answer', 'citations'})
        self.assertIs(schema['additionalProperties'], False)
        self.assertEqual(set(schema['properties']), {'answer', 'citations'})
        self.assertEqual(schema['properties']['answer']['type'], 'string')
        self.assertNotIn('maxLength', schema['properties']['answer'])
        citations = schema['properties']['citations']
        self.assertEqual(citations['type'], 'array')
        self.assertEqual(citations['maxItems'], 8)
        self.assertEqual(citations['items']['type'], 'string')
        self.assertEqual(set(citations['items']['enum']), set(visible))
        self.assertEqual(len(citations['items']['enum']), len(visible))
        self.assertNotIn('HIDDEN_GOLD_ID', canonical(schema))

    def test_empty_context_uses_zero_items_without_empty_enum(self):
        schema = c.schema(iter(()))
        citations = schema['properties']['citations']
        self.assertEqual(citations['maxItems'], 0)
        self.assertEqual(citations['items'], {'type': 'string'})
        self.assertNotIn('enum', canonical(schema))
        self.assertEqual(set(schema['required']), {'answer', 'citations'})
        self.assertIs(schema['additionalProperties'], False)

    @unittest.skipIf(Draft202012Validator is None, 'optional jsonschema package unavailable')
    def test_schema_is_legal_and_rejects_empty_object_in_both_contexts(self):
        visible = [u['id'] for u in fixture()['units']]
        for ids in (visible, []):
            with self.subTest(visible=ids):
                schema = c.schema(ids)
                Draft202012Validator.check_schema(schema)
                validator = Draft202012Validator(schema)
                self.assertTrue(validator.is_valid(dict(answer='', citations=[])))
                self.assertFalse(validator.is_valid({}))
                self.assertFalse(validator.is_valid(dict(answer='x')))
                self.assertFalse(validator.is_valid(dict(answer=2, citations=[])))
                self.assertFalse(validator.is_valid(dict(answer='', citations=[], extra=1)))
                self.assertFalse(validator.is_valid(dict(answer='', citations=['hidden-id'])))
                if ids:
                    self.assertTrue(validator.is_valid(dict(answer='x', citations=[ids[0]])))
                    self.assertFalse(validator.is_valid(dict(answer='x', citations=[ids[0]] * 9)))
                    self.assertFalse(validator.is_valid(dict(answer='x', citations=['[' + ids[0] + ']'])))
                else:
                    self.assertFalse(validator.is_valid(dict(answer='x', citations=[visible[0]])))

    @unittest.skipIf(Draft202012Validator is None, 'optional jsonschema package unavailable')
    def test_schema_success_does_not_imply_utf8_parser_success(self):
        obj = dict(answer='\U0001f600' * 41, citations=[])
        self.assertTrue(Draft202012Validator(c.schema([])).is_valid(obj))
        result = c.evaluate(envelope(json.dumps(obj)), 'fixture', 'fixture', set())
        self.assertFalse(result['format_valid'])
        self.assertIsNone(result['assessment'])
        self.assertIn('answer_utf8_limit', result['diagnostics'])


class CitationRequestTests(unittest.TestCase):
    def test_json_object_payload_preserves_original_fixed_parameters(self):
        cfg = config()
        messages = c.prompt(fixture(), [], 'original')
        self.assertEqual(c.request(cfg, messages, 37, 'json_object', []),
                         n.request(cfg, messages, 37))

    def test_guided_payload_uses_top_level_schema_and_no_fallback(self):
        visible = [u['id'] for u in fixture()['units']]
        for ids in (visible, []):
            cfg = config()
            messages = c.prompt(fixture(), [], 'explicit')
            before = deepcopy((cfg, messages, ids))
            payload = c.request(cfg, messages, 37, 'guided_json', ids)
            expected = dict(model='fixture', messages=messages, seed=37,
                            temperature=0., top_p=1., max_tokens=256,
                            guided_json=c.schema(ids),
                            guided_decoding_backend='outlines:no-fallback')
            self.assertEqual(payload, expected)
            self.assertNotIn('response_format', payload)
            self.assertNotIn('extra_body', payload)
            self.assertEqual((cfg, messages, ids), before)

    def test_extra_config_fields_cannot_inject_conflicting_decoder(self):
        cfg = dict(config(), response_format={'type': 'text'},
                   guided_json={'type': 'null'}, guided_decoding_backend='other')
        messages = c.prompt(fixture(), [])
        for decoder in ('json_object', 'guided_json'):
            self.assertEqual(c.request(cfg, messages, 37, decoder, []),
                             c.request(config(), messages, 37, decoder, []))

    def test_unknown_decoder_rejected(self):
        with self.assertRaises(ValueError):
            c.request(config(), [], 37, 'automatic-fallback', [])


class CitationEvaluationTests(unittest.TestCase):
    def test_diagnostic_labels_describe_failures_without_changing_strict_result(self):
        sid = fixture()['units'][0]['id']
        examples = ((envelope(content(), 'length'), 'non_stop'),
                    (envelope(None), 'non_text'),
                    (envelope('not-json'), 'duplicate_or_malformed_json'),
                    (envelope('{"answer":"x","answer":"y","citations":[]}'),
                     'duplicate_or_malformed_json'),
                    (envelope('{"answer":"x"}'), 'unexpected_schema'),
                    (envelope('{"answer":3,"citations":[]}'), 'answer_type'),
                    (envelope(content('x' * 161)), 'answer_utf8_limit'),
                    (envelope('{"answer":"x","citations":null}'), 'citation_type'),
                    (envelope(content('x', [sid] * 9)), 'citation_count'),
                    (envelope(content('x', ['hidden'])), 'citation_visibility'))
        for raw, diagnostic in examples:
            with self.subTest(diagnostic=diagnostic):
                result = c.evaluate(raw, 'fixture', 'fixture', {sid})
                strict = n.evaluate(raw, 'fixture', 'fixture', {sid})
                self.assertFalse(result['format_valid'])
                self.assertEqual({k: result[k] for k in strict}, strict)
                self.assertIn(diagnostic, result['diagnostics'])

    def test_valid_repeated_citations_are_preserved_not_deduplicated(self):
        sid = fixture()['units'][0]['id']
        result = c.evaluate(envelope(content('London', [sid, sid])), 'fixture', 'fixture', {sid})
        self.assertTrue(result['format_valid'])
        self.assertEqual(result['assessment'], dict(answer='London', citations=[sid, sid]))
        self.assertEqual(result['diagnostics'], [])

    def test_strict_result_matches_frozen_parser_without_output_repair(self):
        sid = fixture()['units'][0]['id']
        examples = [content(), content('London', [sid]), '{}', 'not-json',
                    '```json\n' + content() + '\n```',
                    '{"answer":"x","answer":"y","citations":[]}',
                    content('London', ['[' + sid + ']']),
                    content('London', ['unknown']), content('\U0001f600' * 41),
                    content('x', [sid] * 9), '{"answer":3,"citations":[]}',
                    '{"answer":"x","citations":"' + sid + '"}',
                    '{"answer":"x"}', '[]', 'null']
        for visible, text, finish in itertools.product((set(), {sid}), examples, ('stop', 'length')):
            with self.subTest(visible=visible, text=text, finish=finish):
                raw = envelope(text, finish)
                before = deepcopy(raw)
                expected = n.evaluate(raw, 'fixture', 'fixture', visible)
                actual = c.evaluate(raw, 'fixture', 'fixture', visible)
                self.assertEqual(set(actual), {'format_valid', 'assessment', 'violation', 'diagnostics'})
                self.assertEqual({k: actual[k] for k in expected}, expected)
                self.assertIsInstance(actual['diagnostics'], list)
                self.assertTrue(all(isinstance(x, str) for x in actual['diagnostics']))
                self.assertEqual(raw, before)

    def test_bracket_only_diagnostic_does_not_accept_or_repair(self):
        sid = fixture()['units'][0]['id']
        raw = envelope(content('London', ['[' + sid + ']']))
        result = c.evaluate(raw, 'fixture', 'fixture', {sid})
        self.assertFalse(result['format_valid'])
        self.assertIsNone(result['assessment'])
        self.assertIn('bracket_wrapped_id', result['diagnostics'])
        self.assertIn('bracket_only_mismatch', result['diagnostics'])
        self.assertIn('citation_visibility', result['diagnostics'])
        self.assertEqual(json.loads(raw['choices'][0]['message']['content'])['citations'],
                         ['[' + sid + ']'])

    def test_brackets_with_other_failure_are_not_bracket_only(self):
        sid = fixture()['units'][0]['id']
        for raw, visible in ((envelope(content('x', ['[hidden]'])), {sid}),
                             (envelope(content('x' * 161, ['[' + sid + ']'])), {sid}),
                             (envelope(content('x', ['[' + sid + ']']), 'length'), {sid}),
                             (envelope(content('x', ['[' + sid + ']'])), set())):
            result = c.evaluate(raw, 'fixture', 'fixture', visible)
            self.assertFalse(result['format_valid'])
            self.assertNotIn('bracket_only_mismatch', result['diagnostics'])

    def test_empty_object_is_diagnosed_not_completed(self):
        result = c.evaluate(envelope('{}'), 'fixture', 'fixture', set())
        self.assertFalse(result['format_valid'])
        self.assertIsNone(result['assessment'])
        self.assertIn('empty_object', result['diagnostics'])
        self.assertIn('unexpected_schema', result['diagnostics'])
        valid = c.evaluate(envelope(content()), 'fixture', 'fixture', set())
        self.assertTrue(valid['format_valid'])
        self.assertEqual(valid['assessment'], dict(answer='', citations=[]))

    def test_utf8_byte_limit_and_no_citation_requirement(self):
        for answer, expected in (('a' * 160, True), ('a' * 161, False),
                                 ('\U0001f600' * 40, True), ('\U0001f600' * 41, False)):
            result = c.evaluate(envelope(content(answer)), 'fixture', 'fixture', {'visible'})
            self.assertEqual(result['format_valid'], expected)
            if expected:
                self.assertEqual(result['assessment'], dict(answer=answer, citations=[]))

    def test_envelope_mismatch_remains_a_hard_error(self):
        for raw in (dict(model='wrong'), dict(model='fixture', choices=[]),
                    dict(model='fixture', choices=[dict(message={})])):
            with self.assertRaises(ValueError):
                c.evaluate(raw, 'fixture', 'fixture', set())


class CitationJobTests(unittest.TestCase):
    def test_eight_jobs_are_the_complete_two_by_two_by_two_design(self):
        triples = set(itertools.product(('full', 'question_only'),
                                        ('original', 'explicit'),
                                        ('json_object', 'guided_json')))
        self.assertEqual(len(c.JOBS), 8)
        self.assertEqual(set(c.JOBS.values()), triples)
        self.assertEqual(c.JOBS, {'__'.join(triple): triple for triple in triples})

    def test_run_item_uses_same_seed_and_exact_context_for_eight_calls(self):
        view = fixture()
        design = dict(protocol='fixture-citation-interface-v1', seed=17)
        client = FakeClient()
        result = c.run_item(client, design, view, 4)
        self.assertEqual(result['item'], 4)
        self.assertEqual(result['id'], view['id'])
        self.assertEqual(set(result['outputs']), set(c.JOBS))
        self.assertEqual(set(result['order']), set(c.JOBS))
        self.assertEqual(len(result['order']), 8)
        self.assertEqual(len(client.calls), 8)
        self.assertEqual(len({row['seed'] for row in client.calls}), 1)
        self.assertEqual(len({row['key'] for row in client.calls}), 8)
        self.assertTrue(all(type(row['seed']) is int for row in client.calls))
        for job, row in zip(result['order'], client.calls):
            context, variant, decoder = c.JOBS[job]
            units = view['units'] if context == 'full' else []
            self.assertEqual(row['messages'], c.prompt(view, units, variant))
            self.assertEqual(row['visible'], {u['id'] for u in units})
            self.assertEqual(row['decoder'], decoder)
        repeated = FakeClient()
        self.assertEqual(c.run_item(repeated, design, view, 4), result)
        self.assertEqual(repeated.calls, client.calls)


class CitationClientSafetyTests(unittest.TestCase):
    def test_non_loopback_and_unsafe_endpoints_rejected_before_network(self):
        endpoints = ('https://example.org/v1', 'http://example.org/v1',
                     'https://127.0.0.1/v1', 'http://user:password@127.0.0.1/v1',
                     'http://127.0.0.1/v1?secret=x', 'http://127.0.0.1/v1#fragment')
        with tempfile.TemporaryDirectory() as temp:
            for endpoint in endpoints:
                with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                    c.Client(config(), endpoint, Path(temp), FakeTokenizer())

    def test_ambient_proxy_and_redirects_are_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            client = c.Client(config(), 'http://127.0.0.1:8000/v1', Path(temp), FakeTokenizer())
            proxies = [h for h in client.opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
            self.assertTrue(all(h.proxies == {} for h in proxies))
            redirects = [h for h in client.opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
            self.assertEqual(len(redirects), 1)
            with self.assertRaises(ValueError):
                redirects[0].redirect_request(None, None, 302, '', {}, 'https://example.org')

    def test_existing_record_never_overwritten_or_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            record = directory / 'fixture.json'
            record.write_text('untouched', encoding='utf-8')
            client = c.Client(config(), 'http://127.0.0.1:8000/v1', directory, FakeTokenizer())
            with patch.object(client.opener, 'open', side_effect=AssertionError('Network forbidden')) as network:
                with self.assertRaises(ValueError):
                    client.call('fixture', c.prompt(fixture(), []), 37, 'json_object', set())
            network.assert_not_called()
            self.assertEqual(record.read_text(encoding='utf-8'), 'untouched')


if __name__ == '__main__':
    unittest.main()
