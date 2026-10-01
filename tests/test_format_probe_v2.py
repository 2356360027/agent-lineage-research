"""Software fixtures, including deliberately invalid outputs; not model evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.dependency_probe import sources
from runtime.e1 import digest, save
from runtime.format_probe_v2 import ARMS, Client, group_stats, outcome, prompt, run_cell, summarize, validate


def design():
    return json.loads((Path(__file__).resolve().parents[1]/'configs/format_probe_v2.json').read_text())


def response(obj):
    return {'model': 'SOFTWARE_FIXTURE_ONLY', 'choices': [{'finish_reason': 'stop',
        'message': {'content': json.dumps(obj)}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}


class FormatV2Tests(unittest.TestCase):
    def test_prompts_and_design(self):
        d = design(); validate(d)
        for v in (0, 1):
            ps = {a: prompt(.7, v, a) for a in ARMS}
            self.assertEqual(len({p[1]['content'] for p in ps.values()}), 1)
            self.assertNotIn('p_state_1', ps['answer_only'][0]['content'])
            self.assertNotIn('citations', ps['answer_only'][0]['content'])
            self.assertNotIn('answer', ps['probability_only'][0]['content'])
            self.assertNotIn('citations', ps['probability_only'][0]['content'])
        d['seed'] += 1
        with self.assertRaises(ValueError): validate(d)

    def test_format_vs_service_failure(self):
        raw = response({'answer': 1, 'p_state_1': .6})
        self.assertFalse(outcome(raw, 'answer_only', raw['model'])['format_valid'])
        for content in ('not JSON', '{"answer":1,"answer":0}', '{"answer":true}', 'null'):
            raw['choices'][0]['message']['content'] = content
            self.assertFalse(outcome(raw, 'answer_only', raw['model'])['format_valid'])
        raw = response({'answer': 1}); raw['choices'][0]['finish_reason'] = 'length'
        self.assertFalse(outcome(raw, 'answer_only', raw['model'])['format_valid'])
        with self.assertRaises(ValueError): outcome(raw, 'answer_only', 'WRONG_MODEL')
        with self.assertRaises(ValueError): outcome({'model': raw['model']}, 'answer_only', raw['model'])

    def test_mixed_invalid_records_audit_and_bounds(self):
        d = design(); cfg = {'deployment': 'native', 'model': 'SOFTWARE_FIXTURE_ONLY', 'model_path': '/fake',
            'served_model_metadata': {'root': '/fake'}, 'server_settings_reported': {'max_model_len': 4096},
            **{k: d[k] for k in ('temperature', 'top_p', 'max_tokens')}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); dr = root/'shard-000'; dr.mkdir(); bundle = sources()
            save(dr/'manifest.json', {'design': d, 'config': cfg, 'source_bundle': bundle, 'source_sha256': digest(bundle)})
            save(dr/'server_metadata.json', {'data': [{'id': cfg['model'], 'root': '/fake', 'max_model_len': 4096}]})
            with patch('urllib.request.urlopen') as net:
                def respond(req, timeout):
                    payload = json.loads(req.data); system = payload['messages'][0]['content']
                    obs = json.loads(payload['messages'][1]['content'])['observations'][0]
                    q = obs['effective_reliability'] if obs['value'] else 1-obs['effective_reliability']
                    if 'exactly three fields' in system:
                        obj = {'answer': obs['value'], 'p_state_1': .5, 'citations': ['O0']}
                    elif 'p_state_1' in system:
                        obj = {'p_state_1': q}
                        if obs['value'] == 1: obj['extra'] = 'deliberate fixture violation'
                    else:
                        obj = {'answer': obs['value']}
                    net.return_value.__enter__.return_value.read.return_value = json.dumps(response(obj)).encode()
                    return net.return_value
                net.side_effect = respond; client = Client(cfg, 'http://127.0.0.1:8000/v1', dr)
                for cell in range(12): run_cell(client, d, cell, dr)
                self.assertEqual(net.call_count, 216)
            s, rows, contrasts, hashes = summarize(root)
            self.assertEqual(len(rows), 216)
            self.assertEqual(s['arms']['probability_only']['format_violations'], 36)
            self.assertEqual(s['arms']['probability_only']['mae_among_valid'], 0)
            self.assertEqual(s['arms']['probability_only']['all_call_tie_rate_bounds'], [0, .5])
            self.assertGreater(s['arms']['probability_only']['all_call_mae_bounds'][1], 0)
            self.assertEqual(s['primary_reporting']['conditional_B_minus_A_mae']['matched_pairs'], 36)
            self.assertEqual(s['value_strata']['probability_only_v1']['mae_among_valid'], None)
            self.assertEqual(s['complement_symmetry']['probability_only']['valid_pairs'], 0)
            self.assertTrue(all(r['assessment'] is None for r in rows if not r['format_valid']))
            target = dr/'i0-v1-probability_only-r0.json'; orig = json.loads(target.read_text())
            self.assertEqual(orig['status'], 'format_violation')
            changed = copy.deepcopy(orig); changed['status'] = 'ok'; save(target, changed)
            with self.assertRaises(ValueError): summarize(root)
            changed = copy.deepcopy(orig); changed['request']['seed'] += 1; save(target, changed)
            with self.assertRaises(ValueError): summarize(root)
            save(target, orig); save(dr/'i-extra.json', orig)
            with self.assertRaises(ValueError): summarize(root)

    def test_network_failure_stops_and_no_retry(self):
        cfg = {'model': 'SOFTWARE_FIXTURE_ONLY', 'temperature': .7, 'top_p': .9, 'max_tokens': 256}
        with tempfile.TemporaryDirectory() as tmp:
            dr = Path(tmp); client = Client(cfg, 'http://127.0.0.1:8000/v1', dr)
            with patch('urllib.request.urlopen', side_effect=TimeoutError):
                with self.assertRaises(TimeoutError): client.call('i0', prompt(.7, 0, 'joint'), 1, 'joint')
            self.assertEqual(json.loads((dr/'i0.json').read_text())['status'], 'failed')
            with self.assertRaises(ValueError): client.call('i0', prompt(.7, 0, 'joint'), 1, 'joint')
            with self.assertRaises(ValueError): Client(cfg, 'https://example.com', dr)


if __name__ == '__main__':
    unittest.main()
