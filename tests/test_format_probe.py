"""Unit-test fixtures only; never real model evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.comprehension_probe import SINGLE, EXPLICIT
from runtime.dependency_probe import sources
from runtime.e1 import digest, save
from runtime.format_probe import ARMS, Client, LEVELS, parse, prompt, run_cell, summarize, validate


def design():
    return json.loads((Path(__file__).resolve().parents[1] / 'configs/format_probe_v1.json').read_text())


def response(obj):
    return {'model': 'SOFTWARE_FIXTURE_ONLY', 'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(obj)}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}


class FormatTests(unittest.TestCase):
    def test_prompt_and_frozen_design(self):
        d = design(); validate(d)
        old = {round(r*t+(1-r)*(1-t), 12) for rs, ts in (
            ([.65,.8,.9], [.7,.85,.95]), ([.61,.74,.86,.93], [.68,.82,.97])) for r in rs for t in ts}
        self.assertTrue(set(LEVELS).isdisjoint(old))
        for a in LEVELS:
            for value in (0, 1):
                ps = [prompt(a, value, arm) for arm in ARMS]
                self.assertEqual(ps[0][0]['content'], SINGLE + EXPLICIT)
                self.assertEqual(ps[0][1], ps[1][1]); self.assertEqual(ps[1][1], ps[2][1])
                self.assertTrue(all(p[0]['content'].endswith(EXPLICIT) for p in ps))
                obs = json.loads(ps[0][1]['content'])['observations'][0]
                self.assertEqual(set(obs), {'id', 'value', 'effective_reliability'})
        d['seed'] += 1
        with self.assertRaises(ValueError): validate(d)

    def test_strict_schemas(self):
        for obj, arm in [({'p_state_1': .5}, 'probability_only'), ({'answer': 1}, 'answer_only'),
                         ({'answer': 1, 'p_state_1': .5, 'citations': []}, 'joint')]:
            self.assertEqual(parse(response(obj), arm), obj)
        for obj, arm in [({'p_state_1': True}, 'probability_only'), ({'p_state_1': float('nan')}, 'probability_only'),
                         ({'answer': True}, 'answer_only'), ({'answer': 1, 'p_state_1': .9}, 'answer_only'),
                         ({'answer': 1, 'p_state_1': .9, 'citations': ['hidden']}, 'joint')]:
            with self.assertRaises(ValueError): parse(response(obj), arm)
        raw = response({'answer': 1}); raw['choices'][0]['finish_reason'] = 'length'
        with self.assertRaises(ValueError): parse(raw, 'answer_only')

    def test_audit_fixture_and_tampering(self):
        d = design(); cfg = {'deployment': 'native', 'model': 'SOFTWARE_FIXTURE_ONLY', 'model_path': '/fake',
             'served_model_metadata': {'root': '/fake'}, 'server_settings_reported': {'max_model_len': 4096},
             **{k: d[k] for k in ('temperature', 'top_p', 'max_tokens')}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); dr = root / 'shard-000'; dr.mkdir(); bundle = sources()
            save(dr / 'manifest.json', {'design': d, 'config': cfg, 'source_bundle': bundle, 'source_sha256': digest(bundle)})
            save(dr / 'server_metadata.json', {'data': [{'id': cfg['model'], 'root': '/fake', 'max_model_len': 4096}]})
            with patch('urllib.request.urlopen') as net:
                def respond(req, timeout):
                    payload = json.loads(req.data); system = payload['messages'][0]['content']
                    obs = json.loads(payload['messages'][1]['content'])['observations'][0]
                    q = obs['effective_reliability'] if obs['value'] else 1-obs['effective_reliability']
                    if 'Return only JSON with p_state_1' in system:
                        obj = {'p_state_1': q}
                    elif 'and citations' in system:
                        obj = {'answer': obs['value'], 'p_state_1': .5, 'citations': ['O0']}
                    else:
                        obj = {'answer': obs['value']}
                    net.return_value.__enter__.return_value.read.return_value = json.dumps(response(obj)).encode()
                    return net.return_value
                net.side_effect = respond; client = Client(cfg, 'http://127.0.0.1:8000/v1', dr)
                for cell in range(12): run_cell(client, d, cell, dr)
                self.assertEqual(net.call_count, 216)
            s, rows, contrasts, hashes = summarize(root)
            self.assertEqual(len(rows), 216); self.assertEqual(len(contrasts), 72)
            self.assertEqual(s['arms']['joint']['tie'], 1)
            self.assertEqual(s['arms']['joint']['inconsistent'], 0)
            self.assertEqual(s['arms']['probability_only']['mae'], 0)
            self.assertNotIn('mae', s['arms']['answer_only'])
            self.assertEqual(s['primary_descriptive_contrasts']['probability_only_minus_joint_tie'], -1)
            target = dr / 'i0-v0-joint-r0.json'; original = json.loads(target.read_text())
            rec = copy.deepcopy(original); rec['status'] = 'pending'; save(target, rec)
            with self.assertRaises(ValueError): summarize(root)
            rec = copy.deepcopy(original); rec['request']['seed'] += 1; save(target, rec)
            with self.assertRaises(ValueError): summarize(root)
            save(target, original); save(dr / 'i-extra.json', original)
            with self.assertRaises(ValueError): summarize(root)

    def test_failure_preserved_no_retry_and_remote_rejected(self):
        cfg = {'model': 'SOFTWARE_FIXTURE_ONLY', 'temperature': .7, 'top_p': .9, 'max_tokens': 256}
        with tempfile.TemporaryDirectory() as tmp:
            dr = Path(tmp)
            with self.assertRaises(ValueError): Client(cfg, 'https://example.com', dr)
            client = Client(cfg, 'http://127.0.0.1:8000/v1', dr)
            with patch('urllib.request.urlopen', side_effect=TimeoutError):
                with self.assertRaises(TimeoutError): client.call('i0', prompt(.6, 0, 'answer_only'), 1, 'answer_only')
            self.assertEqual(json.loads((dr / 'i0.json').read_text())['status'], 'failed')
            with self.assertRaises(ValueError): client.call('i0', prompt(.6, 0, 'answer_only'), 1, 'answer_only')


if __name__ == '__main__':
    unittest.main()
