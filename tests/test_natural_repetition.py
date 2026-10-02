"""CPU fixtures only; no measured scientific outcomes or model inference."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from runtime import natural_repetition as n
from runtime.e1 import canonical, digest
from runtime.natural_qa_data import prediction_view


def fixture():
    return prediction_view(dict(_id='fixture-not-a-dataset-item', question='Where was Ada born?',
        context=[(f'Doc {i}', [f'Ada event {i}', f'Other fact {i}', f'Where event {i}']) for i in range(10)]))


def design():
    return json.loads(Path('configs/natural_repetition_v1.json').read_text())


def envelope(content, finish='stop'):
    return dict(model='fixture', choices=[dict(finish_reason=finish, message=dict(content=content))])


class FakeClient:
    def __init__(self):
        self.config = dict(model='fixture', temperature=0., top_p=1., max_tokens=256)
        self.calls = []

    def call(self, key, messages, seed, arm):
        self.calls.append((key, messages, seed, arm))
        return self.evaluator(envelope('{"answer":"","citations":[]}'), arm, 'fixture')


class NaturalRepetitionTests(unittest.TestCase):
    def test_same_document_distinct_units_retained(self):
        v = fixture()
        repeated = n.replay_units(v, design())
        self.assertEqual(len(repeated), 36)
        self.assertEqual(n.dedup(repeated), v['units'])
        self.assertEqual(len(n.dedup(repeated)), 30)

    def test_identical_text_different_source_not_merged(self):
        v = fixture()
        us = deepcopy(v['units'][:2])
        us[1]['text'] = us[0]['text']
        self.assertEqual(len(n.dedup(us)), 2)

    def test_conflicting_id_payload_rejected(self):
        u = fixture()['units'][0]
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            n.dedup([u, dict(u, text='changed')])

    def test_gold_does_not_enter_prompt_or_replay(self):
        v = fixture()
        w = dict(v, answer='SECRET_GOLD', supporting_facts=['SECRET_SUPPORT'])
        self.assertEqual(n.replay_units(v, design()), n.replay_units(w, design()))
        self.assertEqual(n.prompt(v, v['units']), n.prompt(w, w['units']))
        self.assertNotIn('SECRET', canonical(n.prompt(w, w['units'])))

    def test_round_robin_private_union_complete(self):
        v = fixture()
        parts = [n.private_units(v, a) for a in range(3)]
        self.assertEqual(len({u['id'] for part in parts for u in part}), 30)
        self.assertTrue(all(len(part) == 10 for part in parts))

    def test_exact_replay_seed_and_alias(self):
        c = FakeClient()
        result = n.run_item(c, design(), fixture(), 0)
        self.assertEqual(len(c.calls), 11)
        self.assertEqual(len(result['aliases']), 3)
        index = {k: (p, s) for k, p, s, _ in c.calls}
        for a in range(3):
            self.assertEqual(index[f'i0-once_a{a}'][1], index[f'i0-replay_a{a}'][1])
            expected = n.request(c.config, *index[f'i0-once_a{a}'])
            self.assertEqual(result['aliases'][f'dedup_replay_a{a}']['request_sha256'], digest(expected))

    def test_invalid_previous_not_repaired(self):
        messages = n.prompt(fixture(), [], 0, dict(format_valid=False, assessment=None))
        self.assertIn('unavailable (invalid prior output)', messages[1]['content'])

    def test_output_failures_retained(self):
        for content in ('{}', '{"answer":"x","answer":"y","citations":[]}',
                        '{"answer":"x","citations":["bogus"]}',
                        '{"answer":3,"citations":[]}', 'not-json'):
            self.assertFalse(n.evaluate(envelope(content), 'qa', 'fixture', set())['format_valid'])
        self.assertFalse(n.evaluate(envelope('{"answer":"","citations":[]}', 'length'), 'qa', 'fixture', set())['format_valid'])
        with self.assertRaises(ValueError):
            n.evaluate(dict(model='wrong'), 'qa', 'fixture', set())

    def test_answer_and_prior_caps(self):
        self.assertFalse(n.evaluate(envelope(json.dumps(dict(answer='x'*161, citations=[]))), 'qa', 'fixture', set())['format_valid'])
        out = n.evaluate(envelope('{"answer":"unknown","citations":[]}'), 'qa', 'fixture', set())
        self.assertTrue(out['format_valid'])
        self.assertLessEqual(len(canonical(out['assessment']).encode()), 512)
        out = n.evaluate(envelope(json.dumps(dict(answer='😀'*40, citations=[]))), 'qa', 'fixture', set())
        self.assertTrue(out['format_valid'])

    def test_no_remote_endpoint_and_redirect(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                n.Client({}, 'https://example.org/v1', Path(temp), None)
        with self.assertRaises(ValueError):
            n.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org')
        proxy = next(h for h in n.opener().handlers if isinstance(h, urllib.request.ProxyHandler)) if any(isinstance(h, urllib.request.ProxyHandler) for h in n.opener().handlers) else None
        self.assertTrue(proxy is None or proxy.proxies == {})

    def test_existing_record_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp)
            (d/'fixture.json').write_text('untouched')
            client = n.Client(dict(max_tokens=256), 'http://127.0.0.1:8000/v1', d, None)
            with patch.object(n, 'token_count', return_value=100), patch.object(n, 'request', return_value={}):
                with self.assertRaisesRegex(ValueError, 'Existing'):
                    client.call('fixture', [], 0, 'qa')
            self.assertEqual((d/'fixture.json').read_text(), 'untouched')


if __name__ == '__main__':
    unittest.main()
