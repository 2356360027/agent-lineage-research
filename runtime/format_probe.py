"""Frozen output-format diagnostic: 216 real calls, no retries or output repairs."""
import argparse
import hashlib
import itertools
import json
import math
import random
import sys
import time
import urllib.request
from pathlib import Path
from statistics import mean

from .comprehension_probe import SINGLE, EXPLICIT
from .dependency_probe import check_server, sources
from .e1 import Client as LoopbackClient, canonical, digest, save
from .native import verify
from .probe_score import check, diagnostics

ARMS = ('joint', 'probability_only', 'answer_only')
LEVELS = [.526, .548, .574, .602, .638, .674, .716, .758, .802, .844, .886, .934]


def validate(d):
    expected = {'protocol': 'output-format-probe-v1', 'reliabilities': LEVELS,
                'arms': list(ARMS), 'repetitions': 3, 'seed': 93517062,
                'temperature': .7, 'top_p': .9, 'max_tokens': 256}
    check(canonical(d) == canonical(expected), 'Frozen design changed')


def prompt(reliability, value, arm):
    check(arm in ARMS and value in (0, 1), 'Arm/value')
    base = SINGLE.split('Return only JSON with ')[0]
    # Only the output-schema sentence changes. All task and event definitions,
    # including definitions of fields not requested in a given arm, stay fixed.
    schemas = {'joint': SINGLE, 'probability_only': base +
               'Return only JSON with p_state_1 (number from 0 to 1).',
               'answer_only': base + 'Return only JSON with answer (integer 0 or 1).'}
    return [{'role': 'system', 'content': schemas[arm] + EXPLICIT},
            {'role': 'user', 'content': canonical({'observations': [
                {'id': 'O0', 'value': value, 'effective_reliability': reliability}]})}]


def parse(raw, arm):
    choice = raw['choices'][0]
    check(choice.get('finish_reason') == 'stop', 'Truncated/non-stop completion')
    obj = json.loads(choice['message']['content'])
    fields = {'joint': {'answer', 'p_state_1', 'citations'},
              'probability_only': {'p_state_1'}, 'answer_only': {'answer'}}
    check(isinstance(obj, dict) and set(obj) == fields[arm], 'Unexpected schema')
    if 'answer' in obj:
        check(type(obj['answer']) is int and obj['answer'] in (0, 1), 'Invalid answer')
    if 'p_state_1' in obj:
        p = obj['p_state_1']
        check(type(p) in (float, int) and math.isfinite(p) and 0 <= p <= 1, 'Invalid probability')
    if 'citations' in obj:
        check(isinstance(obj['citations'], list) and all(x == 'O0' for x in obj['citations']), 'Invalid citations')
    return obj


def request(cfg, messages, seed):
    return {'model': cfg['model'], 'messages': messages, 'seed': seed,
            **{k: cfg[k] for k in ('temperature', 'top_p', 'max_tokens')},
            'response_format': {'type': 'json_object'}}


class Client(LoopbackClient):
    def call(self, key, messages, seed, arm):
        payload = request(self.config, messages, seed); path = self.directory / (key + '.json')
        if path.exists():
            raise ValueError('Existing call; no overwrite or automatic retry')
        rec = {'request': payload, 'request_hash': digest(payload), 'arm': arm,
               'status': 'pending', 'usage': None, 'started_unix': time.time(),
               'data_status': 'MODEL_ENDPOINT_FORMAT_DIAGNOSTIC'}
        save(path, rec); started = time.perf_counter()
        try:
            req = urllib.request.Request(self.endpoint.rstrip('/') + '/chat/completions',
                                         canonical(payload).encode(), {'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=180) as response:
                rec['raw_text'] = response.read().decode()
            raw = json.loads(rec['raw_text']); rec['raw_response'] = raw; rec['usage'] = raw.get('usage')
            check(raw['model'] == self.config['model'], 'Model mismatch')
            obj = parse(raw, arm); rec['status'] = 'ok'
            return obj
        except Exception as exc:
            rec['status'] = 'failed'; rec['error_type'] = type(exc).__name__
            raise
        finally:
            rec['wall_seconds'] = time.perf_counter() - started; save(path, rec)


class Audit:
    def __init__(self, directory, cfg):
        self.directory = directory; self.cfg = cfg; self.calls = []; self.keys = set()

    def call(self, key, messages, seed, arm):
        check(key not in self.keys, 'Duplicate logical call'); self.keys.add(key)
        rec = json.loads((self.directory / (key + '.json')).read_text(encoding='utf-8'))
        payload = request(self.cfg, messages, seed)
        check(rec['status'] == 'ok' and rec['arm'] == arm, 'Unresolved call/arm mismatch')
        check(rec['request'] == payload and rec['request_hash'] == digest(payload), 'Request mismatch')
        check(json.loads(rec['raw_text']) == rec['raw_response'], 'Raw mismatch')
        check(rec['raw_response']['model'] == self.cfg['model'], 'Model mismatch')
        check(rec['usage'] == rec['raw_response'].get('usage'), 'Usage mismatch')
        self.calls.append(rec)
        return parse(rec['raw_response'], arm)


def key_for(cell, value, arm, repeat):
    return f'i{cell}-v{value}-{arm}-r{repeat}'


def run_cell(client, d, cell, directory=None):
    a = d['reliabilities'][cell]
    order = list(itertools.product((0, 1), ARMS, range(d['repetitions'])))
    random.Random(f'format-order:{d["seed"]}:{cell}').shuffle(order)
    result = {'cell': cell, 'reliability': a, 'order': [list(x) for x in order], 'outputs': {}}
    for value, arm, repeat in order:
        key = key_for(cell, value, arm, repeat)
        seed = int(digest([d['protocol'], d['seed'], cell, repeat])[:7], 16)
        result['outputs'][key] = client.call(key, prompt(a, value, arm), seed, arm)
    if directory is not None:
        save(directory / f'result-{cell}.json', result)
    return result


def summarize(root):
    dr = root / 'shard-000'; m = json.loads((dr / 'manifest.json').read_text(encoding='utf-8'))
    d, cfg = m['design'], m['config']; validate(d)
    check(digest(sources()) == m['source_sha256'] == digest(m['source_bundle']), 'Source mismatch')
    check(all(cfg[k] == d[k] for k in ('temperature', 'top_p', 'max_tokens')), 'Config mismatch')
    check_server(cfg, json.loads((dr / 'server_metadata.json').read_text(encoding='utf-8')), False)
    check(len(list(dr.glob('result-*.json'))) == 12 and len(list(dr.glob('i*.json'))) == 216,
          'Incomplete/extra records; do not impute')
    audit = Audit(dr, cfg); rows = []; arms = {}; strata = {}; symmetry = {'joint': [], 'probability_only': []}
    contrasts = []
    for cell, a in enumerate(d['reliabilities']):
        result = json.loads((dr / f'result-{cell}.json').read_text(encoding='utf-8'))
        check(result == run_cell(audit, d, cell), 'Result/order mismatch')
        for value, arm, repeat in itertools.product((0, 1), ARMS, range(3)):
            v = result['outputs'][key_for(cell, value, arm, repeat)]; metrics = {}
            q = a if value else 1-a
            if 'answer' in v:
                metrics['answer_direction'] = int(v['answer'] == value)
            if 'p_state_1' in v:
                p = v['p_state_1']
                metrics.update(mae=abs(p-q), mse=(p-q)**2, tie=int(p == .5),
                               probability_direction=int(p != .5 and int(p > .5) == value))
            if arm == 'joint':
                metrics['inconsistent'] = int(v['p_state_1'] != .5 and v['answer'] != int(v['p_state_1'] > .5))
            rows.append({'cell': cell, 'reliability': a, 'value': value, 'arm': arm, 'repeat': repeat,
                         'oracle_p': q, 'raw_output': v, **metrics})
            arms.setdefault(arm, []).append(metrics); strata.setdefault(f'{arm}_v{value}', []).append(metrics)
        for repeat in range(3):
            for arm in symmetry:
                ps = [result['outputs'][key_for(cell, v, arm, repeat)]['p_state_1'] for v in (0, 1)]
                symmetry[arm].append(abs(sum(ps)-1))
            for value in (0, 1):
                q = a if value else 1-a
                joint = result['outputs'][key_for(cell, value, 'joint', repeat)]
                prob = result['outputs'][key_for(cell, value, 'probability_only', repeat)]['p_state_1']
                answer = result['outputs'][key_for(cell, value, 'answer_only', repeat)]['answer']
                contrasts.append({'cell': cell, 'value': value, 'repeat': repeat,
                    'probability_only_minus_joint_mae': abs(prob-q)-abs(joint['p_state_1']-q),
                    'probability_only_minus_joint_tie': int(prob == .5)-int(joint['p_state_1'] == .5),
                    'answer_only_minus_joint_direction': int(answer == value)-int(joint['answer'] == value)})
    def group_stats(groups):
        return {a: {'n': len(vs), **{k: mean(v[k] for v in vs) for k in vs[0]}} for a, vs in groups.items()}
    complete = all(isinstance(c['usage'], dict) and all(type(c['usage'].get(k)) is int and
                   c['usage'][k] >= 0 for k in ('prompt_tokens', 'completion_tokens')) for c in audit.calls)
    report = {'status': 'EXPLORATORY_OUTPUT_FORMAT_DIAGNOSTIC', 'cells': 12, 'calls': 216,
              'arms': group_stats(arms), 'value_strata': group_stats(strata),
              'mean_complement_error': {k: mean(v) for k, v in symmetry.items()},
              'primary_descriptive_contrasts': {k: mean(x[k] for x in contrasts) for k in (
                  'probability_only_minus_joint_mae', 'probability_only_minus_joint_tie')},
              'secondary_answer_direction_contrast': mean(x['answer_only_minus_joint_direction'] for x in contrasts),
              'usage_complete': complete,
              'prompt_tokens': sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,
              'completion_tokens': sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,
              'sum_call_seconds': sum(c['wall_seconds'] for c in audit.calls),
              'warning': 'Selected finite grid and repeated draws, not 216 independent tasks. No significance/pass criteria. Output-schema intervention also changes generated length and joint citation requirement. No causal attribution to a unique cognitive mechanism. This does not overturn failed holdout or establish DICE efficacy.'}
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.rglob('*.json'))}
    return report, rows, contrasts, hashes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--design', default='configs/format_probe_v1.json')
    p.add_argument('--native-config', default='runtime/runs/native-smoke-config.json')
    p.add_argument('--output', default='runtime/runs/format-diagnostic-001')
    p.add_argument('--execute', action='store_true'); p.add_argument('--score', action='store_true')
    p.add_argument('--audit-only', action='store_true'); args = p.parse_args(); root = Path(args.output)
    if sum([args.execute, args.score, args.audit_only]) > 1:
        p.error('Choose only one action')
    if args.audit_only:
        print(json.dumps(diagnostics(root), indent=2)); return
    if args.score:
        s, r, c, h = summarize(root)
        for name, data in [('summary', s), ('cell_metrics', r), ('paired_contrasts', c), ('audit_input_hashes', h)]:
            save(root / (name + '.json'), data)
        print(json.dumps(s, indent=2)); return
    d = json.loads(Path(args.design).read_text(encoding='utf-8')); validate(d)
    print(json.dumps({'mode': 'execute' if args.execute else 'plan_only', 'calls': 216,
                      'cells': 12, 'design_sha256': digest(d)}, indent=2), flush=True)
    if not args.execute:
        return
    cfg = json.loads(Path(args.native_config).read_text(encoding='utf-8'))
    check(cfg.get('deployment') == 'native', 'Native model required')
    cfg.update({k: d[k] for k in ('temperature', 'top_p', 'max_tokens')})
    root.mkdir(parents=True, exist_ok=False); dr = root / 'shard-000'; dr.mkdir(); bundle = sources()
    save(dr / 'manifest.json', {'design': d, 'config': cfg, 'source_bundle': bundle,
         'source_sha256': digest(bundle), 'created_unix': time.time(), 'python': sys.version})
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models', timeout=10) as response:
        metadata = json.load(response)
    check_server(cfg, metadata); save(dr / 'server_metadata.json', metadata)
    client = Client(cfg, 'http://127.0.0.1:8000/v1', dr)
    for cell in range(12):
        run_cell(client, d, cell, dr); print(f'Completed cell {cell}; total=12', flush=True)
    print('Worker completed; score with runtime.format_probe --score.', flush=True)


if __name__ == '__main__':
    main()
