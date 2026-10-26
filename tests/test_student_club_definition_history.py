"""Synthetic provider envelopes only; no API, SQL or research measurements."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from runtime.cache_intent_journal import Journal, verify
from runtime.student_club_definition_history import DefinitionHistory, parse
from runtime.student_club_diagnostic_design import build
from tests.test_student_club_diagnostic_design import common


def response(kind, value, call_id='c1'):
    field = {'run_sql': 'sql', 'read_cache': 'cache_id'}.get(kind, 'text')
    return json.dumps({'choices': [{'index': 0, 'finish_reason': 'tool_calls',
        'message': {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': call_id, 'type': 'function', 'function': {'name': kind,
             'arguments': json.dumps({field: value}, ensure_ascii=False)}}]}}]}, ensure_ascii=False)


def make(protocol='catalog_first', save=None):
    records = {}
    def append(name, value):
        if name in records:
            raise AssertionError('Journal overwrite')
        records[name] = deepcopy(value)
    job = next(j for j in build(common())['jobs'] if j['protocol'] == protocol)
    return DefinitionHistory(common(), job, save or append), records


def events(records, kind):
    return [r for r in records.values() if r['kind'] == kind]


class Tests(unittest.TestCase):
    def test_wrong_definition_preserved_and_linked_before_program(self):
        h, records = make('request_first')
        first = h.begin()
        self.assertNotIn('history', first['context']['data'])
        original = '  WRONG synthetic request: count all, ignoring the subset.\n'
        raw = response('record_definition', original)
        definition = h.accept(raw)['event_id']
        second = h.begin()
        prior = second['context']['data']['prior_agent_definition']
        self.assertEqual(prior['text'], original)
        self.assertEqual(prior['source_event_id'], definition)
        self.assertEqual(second['messages'], [])
        h.accept(response('run_sql', 'SELECT COUNT(*) FROM synthetic', 'c2'))
        action = events(records, 'action_recorded')[0]
        self.assertEqual(action['value']['definition_event'], definition)
        self.assertLess(definition, action['event_id'])
        self.assertEqual(events(records, 'raw_response')[0]['value']['raw'], raw)
        self.assertFalse(action['value']['semantic_correctness_assessed'])

    def test_revisions_do_not_relabel_earlier_actions(self):
        h, records = make()
        h.begin(); first = h.accept(response('record_definition', 'first'))['event_id']
        h.begin(); h.accept(response('run_sql', 'SELECT 1', 'c2'))
        h.observe('c2', '{"status":"EXECUTED","rows":[[1]]}')
        h.begin(); revision = h.accept(response('revise_definition', 'second', 'c3'))['event_id']
        request = h.begin()
        self.assertEqual(request['messages'][-2]['tool_calls'][0]['function']['name'], 'revise_definition')
        self.assertEqual(request['messages'][-1]['tool_call_id'], 'c3')
        h.accept(response('answer', '1', 'c4'))
        self.assertEqual([e['value']['definition_event'] for e in events(records, 'action_recorded')], [first, revision])
        definitions = events(records, 'definition_recorded')
        self.assertEqual([d['value']['observation'] for d in definitions], ['pre_result', 'post_result'])
        self.assertEqual(definitions[1]['value']['previous_definition'], first)
        self.assertEqual(h.state, 'TERMINAL')
        with self.assertRaises(ValueError): h.begin()

    def test_revisions_consume_generations_not_sql_attempts(self):
        h, records = make()
        h.begin(); h.accept(response('record_definition', 'first'))
        for n in range(3):
            h.begin(); h.accept(response('revise_definition', f'revision {n}', f'r{n}'))
        last = h.begin()
        self.assertEqual({t['function']['name'] for t in last['tools']}, {'answer', 'abstain'})
        h.accept(response('abstain', 'uncertain', 'end'))
        self.assertEqual(h.generations, 5)
        self.assertEqual(h.action_generations, 4)
        self.assertEqual(h.observations, 0)
        self.assertEqual(len(events(records, 'action_recorded')), 1)

    def test_ordinary_never_infers_definition(self):
        h, records = make('ordinary')
        request = h.begin()
        self.assertNotIn('prior_agent_definition', request['context']['data'])
        self.assertNotIn('revise_definition', str(request['tools']))
        h.accept(response('answer', 'the result'))
        self.assertIsNone(events(records, 'action_recorded')[0]['value']['definition_event'])
        self.assertFalse(events(records, 'definition_recorded'))

    def test_invalid_raw_saved_and_no_automatic_retry(self):
        duplicate = json.loads(response('record_definition', 'x'))
        duplicate['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = '{"text":"first","text":"second"}'
        for raw in ['not json', response('answer', 'premature'),
                    json.dumps(duplicate)]:
            with self.subTest(raw=raw):
                h, records = make(); h.begin()
                with self.assertRaises(ValueError): h.accept(raw)
                self.assertEqual(h.state, 'INVALID_RESPONSE')
                self.assertEqual(events(records, 'raw_response')[0]['value']['raw'], raw)
                self.assertFalse(events(records, 'definition_recorded'))
                with self.assertRaises(ValueError): h.begin()

    def test_parser_rejects_ambiguous_envelopes_and_arguments(self):
        for mutate in (
            lambda b: b['choices'].append(deepcopy(b['choices'][0])),
            lambda b: b['choices'][0]['message'].update(content='extra answer'),
            lambda b: b['choices'][0]['message'].update(reasoning_content='extra'),
            lambda b: b['choices'][0].update(index=True),
            lambda b: b['choices'][0].update(finish_reason='length'),
            lambda b: b['choices'][0]['message']['tool_calls'][0].update(index=True),
            lambda b: b['choices'][0]['message']['tool_calls'][0]['function'].update(arguments='{"text":NaN}'),
            lambda b: b['choices'][0]['message']['tool_calls'][0]['function'].update(arguments='{"text":"a","text":"b"}'),
            lambda b: b['choices'][0]['message']['tool_calls'][0]['function'].update(arguments='{"text":""}'),
            lambda b: b['choices'][0]['message']['tool_calls'][0]['function'].update(arguments='{"text":"x","other":1}'),
        ):
            b = json.loads(response('record_definition', 'x')); mutate(b)
            with self.assertRaises(ValueError): parse(json.dumps(b))
        b = json.loads(response('record_definition', '原文'))
        b['choices'][0]['message']['tool_calls'][0]['index'] = 0
        self.assertEqual(parse(json.dumps(b))['action']['text'], '原文')

    def test_reused_ids_and_horizon_violations(self):
        h, records = make(); h.begin(); h.accept(response('record_definition', 'x'))
        h.begin()
        with self.assertRaises(ValueError): h.accept(response('answer', 'x'))
        h, records = make('ordinary')
        for n in range(3):
            h.begin(); h.accept(response('read_cache', 'v000', str(n)))
            h.observe(str(n), '{"status":"UNAVAILABLE"}')
        h.begin()
        with self.assertRaises(ValueError): h.accept(response('run_sql', 'SELECT 1', 'fourth'))
        self.assertEqual(len(events(records, 'action_recorded')), 3)

    def test_bad_observations_and_mutated_job_rejected(self):
        h, records = make('ordinary'); h.begin(); h.accept(response('run_sql', 'SELECT 1'))
        for call_id, content in [('wrong', '{}'), ('c1', 'x'*1025), ('c1', '')]:
            with self.assertRaises(ValueError): h.observe(call_id, content)
        self.assertFalse(events(records, 'tool_observation'))
        job = deepcopy(build(common())['jobs'][0]); job['price'] = 0
        with self.assertRaises(ValueError): DefinitionHistory(common(), job, lambda *a: None)

    def test_journal_failure_stops_and_sealed_round_trip(self):
        h, records = make()
        def broken(*args): raise OSError('synthetic write failure')
        h.save = broken
        with self.assertRaises(OSError): h.begin()
        self.assertEqual(h.state, 'JOURNAL_FAILED')
        with self.assertRaises(ValueError): h.begin()
        root = Path(__file__).resolve().parents[1] / 'runtime' / 'runs'
        with tempfile.TemporaryDirectory(dir=root, prefix='definition-unit-') as tmp:
            journal = Journal(Path(tmp) / 'journal')
            h, _ = make(save=journal); h.begin()
            h.accept(response('record_definition', 'synthetic definition'))
            h.begin(); h.accept(response('abstain', 'synthetic abstention', 'c2'))
            receipt = journal.seal()
            self.assertEqual(receipt, verify(journal.directory))

    def test_all_twelve_conditions_keep_frozen_bounds(self):
        calls = tools = 0
        for job in build(common())['jobs']:
            h = DefinitionHistory(common(), job, lambda *args: None)
            if job['protocol'] != 'ordinary':
                h.begin(); h.accept(response('record_definition', 'synthetic'))
            for n in range(3):
                h.begin(); h.accept(response('run_sql', 'SELECT 1', f'a{n}'))
                h.observe(f'a{n}', '{"status":"EXECUTED","rows":[[1]]}')
            h.begin(); h.accept(response('answer', '1', 'end'))
            calls += h.generations; tools += h.observations
        self.assertEqual((calls, tools), (56, 36))


if __name__ == '__main__': unittest.main()
