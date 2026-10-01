import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from runtime.e1 import Client, messages, parse, run_item, world
from runtime.score import summarize


def response():
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(
        {'answer': 1, 'p_state_1': 0.6, 'citations': []})}}],
        'usage': {'prompt_tokens': 12, 'completion_tokens': 8}}


class Fixture:
    """TEST ONLY, not an experimental backend."""
    def __init__(self): self.calls = []
    def call(self, key, prompt, seed, visible):
        self.calls.append((key, prompt, seed, visible))
        return parse(response(), visible)


class Tests(unittest.TestCase):
    def test_world_reproducible(self):
        self.assertEqual(world(4, 7), world(4, 7))

    def test_gold_not_in_prompt(self):
        obs, _ = world(4, 7)
        self.assertNotIn('label', json.dumps(messages(obs)))
        self.assertNotIn('gold', json.dumps(messages(obs)))

    def test_pair_invariants(self):
        f = Fixture()
        r = run_item(f, {'seed': 10}, 3, 0)
        self.assertEqual(len(f.calls), 7)
        self.assertEqual(r['arms']['E1C0']['evidence_hash'], r['arms']['E1C1']['evidence_hash'])
        self.assertEqual(len({c[2] for c in f.calls[3:]}), 1)
        payloads = [json.loads(c[1][1]['content']) for c in f.calls[3:]]
        self.assertTrue(all(x['your_previous_assessment'] == r['initial'] for x in payloads))

    def test_bad_probability(self):
        raw = response()
        raw['choices'][0]['message']['content'] = '{"answer":1,"p_state_1":NaN,"citations":[]}'
        with self.assertRaises(ValueError): parse(raw, set())

    def test_truncation(self):
        raw = response(); raw['choices'][0]['finish_reason'] = 'length'
        with self.assertRaises(ValueError): parse(raw, set())

    def test_remote_endpoint_rejected(self):
        with self.assertRaises(ValueError): Client({}, 'http://example.com/v1', Path('.'))

    def test_scoring_incomplete_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); shard = root / 'shard-000'; shard.mkdir()
            config = {'seed': 10, 'items': 2, 'repetitions': 1}
            (shard / 'manifest.json').write_text(json.dumps(
                {'config': config, 'code_hash': 'test', 'shards': 1}))
            (shard / 'result-0-0.json').write_text(json.dumps(run_item(Fixture(), config, 0, 0)))
            report = summarize(root)
            self.assertEqual(report['completed_pairs'], 1)
            self.assertEqual(report['missing_pairs'], [(1, 0)])
            self.assertIsNone(report['reported_prompt_tokens'])

    def test_resume_and_failure(self):
        config = {'model': 'test', 'temperature': 0.7, 'top_p': 0.9, 'max_tokens': 256}
        with tempfile.TemporaryDirectory() as d:
            c = Client(config, 'http://127.0.0.1:8000/v1', Path(d))
            with patch('urllib.request.urlopen') as mocked:
                mocked.return_value.__enter__.return_value.read.return_value = json.dumps(response()).encode()
                c.call('one', messages([]), 1, set())
                c.call('one', messages([]), 1, set())
                self.assertEqual(mocked.call_count, 1)
                with self.assertRaises(ValueError): c.call('one', messages([]), 2, set())
            with patch('urllib.request.urlopen', side_effect=TimeoutError):
                with self.assertRaises(TimeoutError): c.call('two', messages([]), 1, set())
            with self.assertRaises(RuntimeError): c.call('two', messages([]), 1, set())


if __name__ == '__main__': unittest.main()
