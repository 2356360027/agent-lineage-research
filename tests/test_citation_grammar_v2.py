"""Portable software contracts, not XGrammar compilation or model observations.

The small reference reader below handles only this module's acyclic EBNF subset.
It tests the generated language with Python regexes, NOT the production compiler,
tokenizer, server admission, GPU path, or any scientific task outcome. Synthetic
preflight reports test record validation only; their token IDs are invented.
"""
from copy import deepcopy
import builtins
import itertools
import json
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from runtime import citation_grammar_v2 as c
from runtime import natural_repetition as strict
from runtime.e1 import digest


SOURCES = {
    'vllm.entrypoints.openai.protocol', 'vllm.v1.engine.processor',
    'vllm.v1.structured_output.backend_xgrammar', 'vllm.v1.structured_output.utils',
}
PACKAGES = {'vllm': '0.8.3', 'xgrammar': '0.1.17', 'outlines': '0.1.11',
            'outlines_core': '0.1.26', 'transformers': '4.57.1'}
VISIBLE = [[], ['d0s0', 'd0s1', 'd1s0']]


def reference_pattern(specification):
    """Translate the limited generated EBNF, never import a grammar backend."""
    rules = {}
    for line in specification.splitlines():
        name, expression = line.split('::=', 1)
        if name.strip() in rules:
            raise AssertionError('Duplicate reference grammar rule')
        rules[name.strip()] = expression.strip()
    scanner = re.compile(r'\s*("(?:\\.|[^"\\])*"|\[(?:\\.|[^\]\\])*\]'
                         r'|[A-Za-z_][A-Za-z_0-9]*|[|()*?])')
    compiled, active = {}, set()

    def rule(name):
        if name in compiled:
            return compiled[name]
        if name in active:
            raise AssertionError('Recursive grammar outside reference-reader scope')
        active.add(name)
        source = rules[name]
        tokens, position = [], 0
        while position < len(source):
            match = scanner.match(source, position)
            if not match:
                raise AssertionError('Unsupported EBNF reference token')
            tokens.append(match.group(1))
            position = match.end()
        index = 0

        def alternatives():
            nonlocal index
            branches = []
            while True:
                sequence = []
                while index < len(tokens) and tokens[index] not in ('|', ')'):
                    token = tokens[index]
                    index += 1
                    if token == '(':
                        atom = alternatives()
                        if index >= len(tokens) or tokens[index] != ')':
                            raise AssertionError('Unclosed EBNF group')
                        index += 1
                    elif token.startswith('"'):
                        atom = re.escape(json.loads(token))
                    elif token.startswith('['):
                        atom = token
                    elif re.fullmatch('[A-Za-z_][A-Za-z_0-9]*', token):
                        atom = rule(token)
                    else:
                        raise AssertionError('Unexpected EBNF reference operator')
                    if index < len(tokens) and tokens[index] in ('*', '?'):
                        atom = '(?:' + atom + ')' + tokens[index]
                        index += 1
                    sequence.append(atom)
                branches.append(''.join(sequence))
                if index >= len(tokens) or tokens[index] != '|':
                    break
                index += 1
            return '(?:' + '|'.join(branches) + ')'

        result = alternatives()
        if index != len(tokens):
            raise AssertionError('Unconsumed EBNF tokens')
        active.remove(name)
        compiled[name] = result
        return result

    return re.compile(rule('root'))


def encoded(answer='', citations=None, reverse=False, **kwargs):
    pairs = [('answer', answer), ('citations', [] if citations is None else citations)]
    if reverse:
        pairs.reverse()
    return json.dumps(dict(pairs), ensure_ascii=False, **kwargs)


def synthetic_report(visible_sets=None):
    """Invent metadata in memory ONLY to test the portable validator.

    accepted/terminated values and token IDs are fixtures, never compiler output.
    No function under test may serialize these reports as experimental evidence.
    """
    visible_sets = VISIBLE if visible_sets is None else visible_sets
    entries = {}
    for ids in visible_sets:
        specification = c.grammar(ids)
        request = dict(model='Qwen/Qwen2.5-7B-Instruct',
            messages=[dict(role='user', content='CPU protocol fixture; no inference')],
            max_tokens=256, temperature=0., top_p=1., seed=1,
            guided_grammar=specification, guided_decoding_backend='xgrammar')
        fixtures = [dict(case, token_ids=[1, 2, 31],
                         first_rejected=None if case['expected'] else 0,
                         terminated=case['expected'], accepted=case['expected'])
                    for case in c.fixtures(ids)]
        entries[digest(specification)] = dict(visible=c.visible_ids(ids),
            grammar=specification, admitted=True, backend_after='xgrammar',
            admission_request=request, admission_request_sha256=digest(request),
            fixtures=fixtures, all_passed=True)
    return dict(kind=c.KIND, backend=c.BACKEND, packages=deepcopy(PACKAGES),
        grammars=entries, engine_sources={name: 'a' * 64 for name in SOURCES},
        tokenizer_vocab_size=32, tokenizer_eos_id=31,
        baseline_admitted=True, baseline_backend_after='xgrammar',
        software_fixture_only_not_compiler_evidence=True)


def first_entry(report):
    return next(iter(report['grammars'].values()))


class GeneratedGrammarContracts(unittest.TestCase):
    def test_deterministic_unique_sorted_ids_and_no_input_mutation(self):
        ids = ['d2s9', 'd0s1', 'd2s9', 'd0s0']
        original = ids[:]
        self.assertEqual(c.visible_ids(ids), ['d0s0', 'd0s1', 'd2s9'])
        self.assertEqual(c.grammar(ids), c.grammar(reversed(ids)))
        self.assertEqual(c.grammar(ids), c.grammar(iter(sorted(set(ids)))))
        self.assertEqual(ids, original)

    def test_invalid_ids_cannot_inject_grammar(self):
        for item in ('', '[d0s0]', 'd0s0 ', 'd0s0\n', 'D0S0', 'd-1s0',
                     'not-visible', 'd0s0" | "evil', 'd0s0\\'):
            with self.subTest(item=item), self.assertRaises(ValueError):
                c.grammar([item])

    def test_generated_grammar_has_no_schema_maxitems_dependency(self):
        full = c.grammar(['d0s0'])
        array_rule = next(line for line in full.splitlines() if line.startswith('array ::='))
        self.assertEqual(array_rule.count('(ws "," ws id)?'), 7)
        self.assertNotIn('maxItems', full)
        self.assertNotIn('maxLength', full)
        self.assertNotIn('id ::=', c.grammar([]))

    def test_all_generated_fixtures_match_reference_language(self):
        for ids in VISIBLE:
            pattern = reference_pattern(c.grammar(ids))
            cases = c.fixtures(ids)
            self.assertEqual(len({case['name'] for case in cases}), len(cases))
            for case in cases:
                with self.subTest(ids=ids, case=case['name']):
                    self.assertEqual(pattern.fullmatch(case['text']) is not None,
                                     case['expected'])

    def test_empty_context_forbids_every_nonempty_array(self):
        pattern = reference_pattern(c.grammar([]))
        for reverse in (False, True):
            self.assertIsNotNone(pattern.fullmatch(encoded('研究', [], reverse)))
            for citations in (['d0s0'], [''], [None], [1], [[]], [{}]):
                self.assertIsNone(pattern.fullmatch(encoded('', citations, reverse)))

    def test_strict_zero_through_eight_and_never_nine_or_more(self):
        ids = ['d0s0', 'd0s1', 'd1s0']
        pattern = reference_pattern(c.grammar(ids))
        for count, reverse, repeat in itertools.product(range(13), (False, True), (False, True)):
            citations = [ids[0] if repeat else ids[i % len(ids)] for i in range(count)]
            with self.subTest(count=count, reverse=reverse, repeat=repeat):
                self.assertEqual(pattern.fullmatch(encoded('x', citations, reverse)) is not None,
                                 count <= 8)

    def test_literal_membership_no_bracket_or_unicode_escape_repair(self):
        pattern = reference_pattern(c.grammar(['d0s0']))
        for citation in ('[d0s0]', 'd0s1', ' d0s0', 'd0s0 ', 'D0S0', 'd00s0'):
            self.assertIsNone(pattern.fullmatch(encoded('x', [citation])))
        self.assertIsNone(pattern.fullmatch(r'{"answer":"x","citations":["\u00640s0"]}'))
        self.assertIsNone(pattern.fullmatch(r'{"\u0061nswer":"x","citations":[]}'))

    def test_unicode_json_escapes_both_key_orders_and_whitespace(self):
        pattern = reference_pattern(c.grammar(['d0s0']))
        answers = ('', 'é研究', '\U0001f600', '\u2028\u2029', '"\\/\b\f\n\r\t',
                   'a' * 161, '\U0001f600' * 41)
        for answer, reverse in itertools.product(answers, (False, True)):
            self.assertIsNotNone(pattern.fullmatch(encoded(answer, [], reverse)))
        for whitespace in ('', ' ', '\t', '\n', '\r', ' \t\r\n'):
            text = whitespace + '{' + whitespace + '"answer"' + whitespace + ':' + whitespace
            text += '"x",' + whitespace + '"citations":[' + whitespace + ']}' + whitespace
            self.assertIsNotNone(pattern.fullmatch(text))
        self.assertIsNone(pattern.fullmatch('\v' + encoded()))
        self.assertIsNone(pattern.fullmatch('\u00a0' + encoded()))

    def test_unescaped_controls_invalid_escapes_and_structural_corruption_rejected(self):
        pattern = reference_pattern(c.grammar(['d0s0']))
        for code in range(32):
            self.assertIsNone(pattern.fullmatch('{"answer":"' + chr(code) + '","citations":[]}'))
        invalid = [r'{"answer":"\q","citations":[]}', r'{"answer":"\u12G4","citations":[]}',
                   '{}', '{"answer":false,"citations":[]}', '{"answer":"x"}',
                   '{"answer":"x","citations":[],"answer":"y"}',
                   '{"citations":[],"citations":[],"answer":"x"}',
                   '{"answer":"x","citations":["d0s0",]}',
                   '{"answer":"x","citations":[]}{}', '```json\n' + encoded() + '\n```']
        for text in invalid:
            with self.subTest(text=text):
                self.assertIsNone(pattern.fullmatch(text))

    def test_byte_limit_and_stop_are_separate_strict_parser_gates(self):
        pattern = reference_pattern(c.grammar(['d0s0']))
        for answer, expected in (('a' * 160, True), ('a' * 161, False),
                                 ('é' * 80, True), ('é' * 81, False),
                                 ('\U0001f600' * 40, True), ('\U0001f600' * 41, False)):
            text = encoded(answer, ['d0s0', 'd0s0'])
            self.assertIsNotNone(pattern.fullmatch(text))
            for finish in ('stop', 'length'):
                raw = dict(model='fixture', choices=[dict(finish_reason=finish,
                    message=dict(content=text))])
                before = deepcopy(raw)
                result = strict.evaluate(raw, 'fixture', 'fixture', {'d0s0'})
                self.assertEqual(result['format_valid'], expected and finish == 'stop')
                self.assertEqual(raw, before)
                if result['format_valid']:
                    self.assertEqual(result['assessment']['citations'], ['d0s0', 'd0s0'])
                else:
                    self.assertIsNone(result['assessment'])

    def test_invalid_outputs_are_never_repaired(self):
        for text in ('{}', encoded('x', ['[d0s0]']), encoded('x', ['d0s0'] * 9),
                     '```json\n' + encoded() + '\n```',
                     '{"answer":"x","answer":"y","citations":[]}'):
            raw = dict(model='fixture', choices=[dict(finish_reason='stop', message=dict(content=text))])
            before = deepcopy(raw)
            result = strict.evaluate(raw, 'fixture', 'fixture', {'d0s0'})
            self.assertFalse(result['format_valid'])
            self.assertIsNone(result['assessment'])
            self.assertEqual(raw, before)


class PortablePreflightValidation(unittest.TestCase):
    def validate(self, report):
        c.validate_preflight(report, VISIBLE, PACKAGES)

    def test_synthetic_report_validation_is_read_only_without_backend_imports(self):
        report = synthetic_report()
        before = deepcopy(report)
        real_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name.split('.')[0] in {'vllm', 'xgrammar', 'torch', 'transformers'}:
                raise AssertionError('Portable validation must not import compiler/GPU libraries')
            return real_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=guarded_import), \
                patch.object(c, 'grammar_preflight', side_effect=AssertionError('No compiler rerun')):
            self.validate(report)
        self.assertEqual(report, before)

    def test_duplicate_visible_sets_share_one_grammar_record(self):
        inputs = [[], ['d0s0'], [], ['d0s0', 'd0s0']]
        report = synthetic_report(inputs)
        self.assertEqual(len(report['grammars']), 2)
        c.validate_preflight(report, inputs, PACKAGES)

    def test_kind_backend_packages_and_baseline_tampering_rejected(self):
        changes = [('kind', 'CPU_COMPILATION_ONLY'), ('backend', 'outlines:no-fallback'),
                   ('packages', dict(PACKAGES, xgrammar='other')),
                   ('baseline_admitted', False), ('baseline_admitted', 1),
                   ('baseline_backend_after', 'auto')]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                report = synthetic_report()
                report[field] = value
                with self.assertRaises(ValueError):
                    self.validate(report)

    def test_source_hash_membership_and_syntax_tampering_rejected(self):
        for changed in ({}, {name: 'short' for name in SOURCES},
                        {name: 'A' * 64 for name in SOURCES},
                        {**{name: 'a' * 64 for name in SOURCES}, 'V0.extra': 'b' * 64}):
            report = synthetic_report()
            report['engine_sources'] = changed
            with self.assertRaises(ValueError):
                self.validate(report)

    def test_missing_and_extra_grammar_records_rejected(self):
        for remove in (False, True):
            report = synthetic_report()
            if remove:
                report['grammars'].pop(next(iter(report['grammars'])))
            else:
                report['grammars']['f' * 64] = deepcopy(first_entry(report))
            with self.assertRaises(ValueError):
                self.validate(report)

    def test_visible_grammar_admission_and_backend_tampering_rejected(self):
        for field, value in [('visible', ['d9s9']), ('grammar', 'root ::= "x"'),
                             ('admitted', False), ('admitted', 1), ('backend_after', 'auto')]:
            report = synthetic_report()
            first_entry(report)[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(report)

    def test_admission_hash_grammar_and_backend_tampering_rejected(self):
        for field, value in [('guided_grammar', 'root ::= "x"'),
                             ('guided_decoding_backend', 'outlines:no-fallback')]:
            report = synthetic_report()
            entry = first_entry(report)
            entry['admission_request'][field] = value
            entry['admission_request_sha256'] = digest(entry['admission_request'])
            with self.assertRaises(ValueError):
                self.validate(report)
        report = synthetic_report()
        first_entry(report)['admission_request_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            self.validate(report)

    def test_complete_admission_payload_is_fixed_even_with_rehashed_tampering(self):
        changes = [('model', 'another-model'), ('messages', []), ('max_tokens', 512),
                   ('temperature', 1.), ('top_p', .9), ('seed', 2),
                   ('response_format', {'type': 'json_object'}),
                   ('temperature', False), ('seed', True)]
        for field, value in changes:
            report = synthetic_report()
            entry = first_entry(report)
            entry['admission_request'][field] = value
            entry['admission_request_sha256'] = digest(entry['admission_request'])
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.validate(report)

    def test_fixture_count_order_text_and_expected_tampering_rejected(self):
        mutations = [lambda fs: fs.pop(), lambda fs: fs.append(deepcopy(fs[0])),
                     lambda fs: fs.reverse(),
                     lambda fs: fs[0].update(name='other'),
                     lambda fs: fs[0].update(text='{}'),
                     lambda fs: fs[0].update(expected=False),
                     lambda fs: fs[0].update(expected=1)]
        for mutate in mutations:
            report = synthetic_report()
            mutate(first_entry(report)['fixtures'])
            with self.assertRaises(ValueError):
                self.validate(report)

    def test_acceptance_rejection_and_suite_tampering_rejected(self):
        for field, value in [('accepted', False), ('accepted', 1),
                             ('first_rejected', 0), ('first_rejected', True),
                             ('first_rejected', -1), ('first_rejected', 3),
                             ('terminated', False), ('terminated', 1)]:
            report = synthetic_report()
            first_entry(report)['fixtures'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.validate(report)
        for value in (False, 1):
            report = synthetic_report()
            first_entry(report)['all_passed'] = value
            with self.assertRaises(ValueError):
                self.validate(report)

    def test_token_vector_type_and_negative_values_rejected(self):
        for value in ([], None, '123', [True, 31], [1.0, 31], [-1, 31],
                      [32, 31], [1, 2], [1, 2, 30]):
            report = synthetic_report()
            first_entry(report)['fixtures'][0]['token_ids'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(report)

    def test_vocab_and_eos_metadata_require_in_range_integers(self):
        for field, value in [('tokenizer_vocab_size', 0), ('tokenizer_vocab_size', -1),
                             ('tokenizer_vocab_size', 32.), ('tokenizer_vocab_size', True),
                             ('tokenizer_eos_id', -1), ('tokenizer_eos_id', 32),
                             ('tokenizer_eos_id', True), ('tokenizer_eos_id', 31.)]:
            report = synthetic_report()
            report[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.validate(report)

    def test_negative_fixture_cannot_be_declared_accepted_or_inconsistently_terminated(self):
        for change in (dict(accepted=True),
                       dict(first_rejected=None, terminated=True),
                       dict(terminated='false')):
            report = synthetic_report()
            negative = next(case for case in first_entry(report)['fixtures'] if not case['expected'])
            negative.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(report)

    def test_missing_required_fields_fail_closed(self):
        for field in ('kind', 'backend', 'packages', 'engine_sources', 'grammars',
                      'baseline_admitted', 'baseline_backend_after',
                      'tokenizer_vocab_size', 'tokenizer_eos_id'):
            report = synthetic_report()
            del report[field]
            with self.subTest(field=field), self.assertRaises((KeyError, ValueError, TypeError)):
                self.validate(report)


class FixtureMatcherControlFlowTests(unittest.TestCase):
    """Mocks test bookkeeping only, never production grammar acceptance."""
    def run_mock_match(self, acceptance, terminated):
        tokenizer = SimpleNamespace(encode=Mock(return_value=[4, 5]), eos_token_id=9)
        matcher = SimpleNamespace(accept_token=Mock(side_effect=acceptance),
                                  is_terminated=Mock(return_value=terminated))
        constructor = Mock(return_value=matcher)
        context = object()
        with patch.dict(sys.modules, {'xgrammar': SimpleNamespace(GrammarMatcher=constructor)}):
            result = c.fixture_match(context, tokenizer, 'synthetic text only')
        constructor.assert_called_once_with(context)
        tokenizer.encode.assert_called_once_with('synthetic text only', add_special_tokens=False)
        self.assertEqual(result['token_ids'], [4, 5, 9])
        return result, matcher

    def test_eos_is_appended_and_all_tokens_plus_termination_required(self):
        result, matcher = self.run_mock_match([True, True, True], True)
        self.assertEqual([call.args[0] for call in matcher.accept_token.call_args_list], [4, 5, 9])
        self.assertIsNone(result['first_rejected'])
        self.assertIs(result['accepted'], True)
        self.assertIs(result['terminated'], True)

    def test_rejection_stops_at_first_token_and_records_full_attempted_vector(self):
        result, matcher = self.run_mock_match([True, False], False)
        self.assertEqual([call.args[0] for call in matcher.accept_token.call_args_list], [4, 5])
        self.assertEqual(result['first_rejected'], 1)
        self.assertIs(result['accepted'], False)

    def test_all_accepted_but_unterminated_and_eos_rejection_both_fail(self):
        result, _ = self.run_mock_match([True, True, True], False)
        self.assertIsNone(result['first_rejected'])
        self.assertIs(result['accepted'], False)
        result, _ = self.run_mock_match([True, True, False], False)
        self.assertEqual(result['first_rejected'], 2)
        self.assertIs(result['accepted'], False)


if __name__ == '__main__':
    unittest.main()
