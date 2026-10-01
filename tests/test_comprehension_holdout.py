"""Software fixtures only. These responses are NOT model experiments."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.comprehension_holdout import CRITERIA, grid, key_for, prompt, run_cell, validate
from runtime.comprehension_holdout_score import gate, summarize
from runtime.comprehension_probe import prompt as selected_prompt
from runtime.dependency_probe import sources
from runtime.e1 import Client, digest, save


def design():
    return json.loads((Path(__file__).resolve().parents[1] / 'configs/comprehension_holdout_v1.json').read_text())


class HoldoutTests(unittest.TestCase):
    def test_frozen_design_and_prompts(self):
        d = design(); validate(d)
        self.assertEqual(len(grid(d)), 12)
        self.assertTrue(set(d['source_reliabilities']).isdisjoint({.65, .8, .9}))
        self.assertTrue(set(d['observation_reliabilities']).isdisjoint({.7, .85, .95}))
        for r, t in grid(d):
            for value in (0, 1):
                y = prompt(r, t, value, 'Y'); z = prompt(r, t, value, 'Z')
                self.assertEqual(y, selected_prompt(r, t, value, 'effective', 'explicit'))
                self.assertEqual(z[0]['content'], y[0]['content'].replace('Y', 'Z'))
                yo, zo = [json.loads(p[1]['content'])['observations'][0] for p in (y, z)]
                self.assertEqual(yo['value'] + zo['value'], 1)
                self.assertEqual(yo['effective_reliability'], zo['effective_reliability'])
                self.assertEqual(set(yo), {'id', 'value', 'effective_reliability'})
        for field, replacement in [('seed', 42), ('repetitions', 4), ('criteria', {})]:
            changed = copy.deepcopy(d); changed[field] = replacement
            with self.assertRaises(ValueError): validate(changed)

    def test_schedule_and_matched_seeds(self):
        class Recorder:
            def __init__(self): self.calls = []
            def call(self, key, p, seed, visible):
                self.calls.append((key, p, seed, visible)); return {'fixture': True}
        a, b = Recorder(), Recorder(); d = design()
        self.assertEqual(run_cell(a, d, 0), run_cell(b, d, 0))
        self.assertEqual(len(a.calls), 12)
        self.assertEqual(len({x[0] for x in a.calls}), 12)
        for repeat in range(3):
            self.assertEqual(len({x[2] for x in a.calls if x[0].endswith(f'-r{repeat}')}), 1)

    def test_gate_is_conjunctive(self):
        good = {'mae': 0., 'direction': 1., 'probability_direction': 1., 'inconsistent': 0.}
        groups = {'Y_shown0': dict(good)}
        self.assertTrue(gate(good, groups, {'Y': 0., 'Z': 0.}, 0., CRITERIA)['passed'])
        groups['Y_shown0']['probability_direction'] = .94
        self.assertFalse(gate(good, groups, {'Y': 0., 'Z': 0.}, 0., CRITERIA)['passed'])

    def test_audited_fixture_and_failures(self):
        d = design(); cfg = {'deployment': 'native', 'model': 'SOFTWARE_FIXTURE_ONLY', 'model_path': '/fake',
             'served_model_metadata': {'root': '/fake'}, 'server_settings_reported': {'max_model_len': 4096},
             **{k: d[k] for k in ('temperature', 'top_p', 'max_tokens')}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); dr = root / 'shard-000'; dr.mkdir(); bundle = sources()
            save(dr / 'manifest.json', {'design': d, 'config': cfg, 'source_bundle': bundle, 'source_sha256': digest(bundle)})
            save(dr / 'server_metadata.json', {'data': [{'id': cfg['model'], 'root': '/fake', 'max_model_len': 4096}]})
            with patch('urllib.request.urlopen') as net:
                # Perfect synthetic oracle only validates scoring software.
                def respond(req, timeout):
                    payload = json.loads(req.data)
                    obs = json.loads(payload['messages'][1]['content'])['observations'][0]
                    q = obs['effective_reliability'] if obs['value'] else 1-obs['effective_reliability']
                    raw = {'model': cfg['model'], 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
                        'answer': obs['value'], 'p_state_1': q, 'citations': ['O0']})}}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
                    net.return_value.__enter__.return_value.read.return_value = json.dumps(raw).encode()
                    return net.return_value
                net.side_effect = respond
                client = Client(cfg, 'http://127.0.0.1:8000/v1', dr)
                for cell in range(12): run_cell(client, d, cell, dr)
                self.assertEqual(net.call_count, 144)
            report, rows, hashes = summarize(root)
            self.assertTrue(report['gate']['passed']); self.assertEqual(len(rows), 144)
            self.assertAlmostEqual(report['overall']['mae'], 0)
            self.assertAlmostEqual(report['recoding_discrepancy'], 0)
            target = dr / (key_for(0, 0, 'Y', 0) + '.json')
            original = json.loads(target.read_text())
            rec = copy.deepcopy(original); rec['status'] = 'pending'; save(target, rec)
            with self.assertRaises(ValueError): summarize(root)
            rec = copy.deepcopy(original); rec['request']['seed'] += 1; save(target, rec)
            with self.assertRaises(ValueError): summarize(root)
            save(target, original); save(dr / 'i-extra.json', original)
            with self.assertRaises(ValueError): summarize(root)


if __name__ == '__main__':
    unittest.main()
