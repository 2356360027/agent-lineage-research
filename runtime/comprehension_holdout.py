"""Frozen 144-call competence holdout. No peers and no adaptive selection."""
import argparse
import itertools
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

from .comprehension_probe import prompt as selected_prompt
from .dependency_probe import check_server, sources
from .e1 import Client, canonical, digest, save
from .native import verify

CRITERIA = {
    'overall_mae_max': .05, 'stratum_mae_max': .075,
    'stratum_direction_min': .95, 'stratum_probability_direction_min': .95,
    'stratum_inconsistency_max': .02, 'complement_symmetry_max': .05,
    'recoding_discrepancy_max': .05,
}


def validate(d):
    expected = {
        'protocol': 'comprehension-holdout-v1',
        'source_reliabilities': [.61, .74, .86, .93],
        'observation_reliabilities': [.68, .82, .97],
        'repetitions': 3, 'seed': 81327046, 'temperature': .7,
        'top_p': .9, 'max_tokens': 256, 'criteria': CRITERIA,
    }
    if canonical(d) != canonical(expected):
        raise ValueError('Frozen design changed; create a new protocol')


def grid(d):
    return list(itertools.product(d['source_reliabilities'], d['observation_reliabilities']))


def prompt(r, t, value, encoding):
    if encoding not in ('Y', 'Z') or value not in (0, 1):
        raise ValueError('Invalid encoding/value')
    # Z=1-Y is an analyst-side relabeling of the entire generative model.
    # In this encoding, the measurement is also complemented. The response
    # field p_state_1 always names state 1 IN THE DISPLAYED encoding.
    shown = value if encoding == 'Y' else 1 - value
    result = selected_prompt(r, t, shown, 'effective', 'explicit')
    if encoding == 'Z':
        result[0]['content'] = result[0]['content'].replace('Y', 'Z')
    return result


def key_for(cell, value, encoding, repeat):
    return f'i{cell}-v{value}-{encoding}-r{repeat}'


def run_cell(client, d, cell, directory=None):
    r, t = grid(d)[cell]
    order = list(itertools.product((0, 1), ('Y', 'Z'), range(d['repetitions'])))
    random.Random(f'holdout-order:{d["seed"]}:{cell}').shuffle(order)
    result = {'cell': cell, 'r': r, 't': t, 'order': [list(x) for x in order], 'outputs': {}}
    for value, encoding, repeat in order:
        key = key_for(cell, value, encoding, repeat)
        seed = int(digest([d['protocol'], d['seed'], cell, repeat])[:7], 16)
        result['outputs'][key] = client.call(key, prompt(r, t, value, encoding), seed, {'O0'})
    if directory is not None:
        save(directory / f'result-{cell}.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--design', default='configs/comprehension_holdout_v1.json')
    p.add_argument('--native-config', default='runtime/runs/native-smoke-config.json')
    p.add_argument('--output', default='runtime/runs/comprehension-holdout-001')
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    d = json.loads(Path(a.design).read_text(encoding='utf-8')); validate(d)
    print(json.dumps({'mode': 'execute' if a.execute else 'plan_only', 'cells': len(grid(d)),
                      'calls': 144, 'design_sha256': digest(d)}, indent=2), flush=True)
    if not a.execute:
        return
    cfg = json.loads(Path(a.native_config).read_text(encoding='utf-8'))
    if cfg.get('deployment') != 'native':
        raise ValueError('Native fingerprinted model required')
    cfg.update({k: d[k] for k in ('temperature', 'top_p', 'max_tokens')})
    root = Path(a.output); root.mkdir(parents=True, exist_ok=False)
    dr = root / 'shard-000'; dr.mkdir(); bundle = sources()
    save(dr / 'manifest.json', {'design': d, 'config': cfg, 'source_bundle': bundle,
         'source_sha256': digest(bundle), 'created_unix': time.time(), 'python': sys.version})
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models', timeout=10) as response:
        metadata = json.load(response)
    check_server(cfg, metadata); save(dr / 'server_metadata.json', metadata)
    client = Client(cfg, 'http://127.0.0.1:8000/v1', dr)
    for cell in range(len(grid(d))):
        run_cell(client, d, cell, dr)
        print(f'Completed cell {cell}; total=12', flush=True)
    print('Worker completed; score with runtime.comprehension_holdout_score.', flush=True)


if __name__ == '__main__':
    main()
