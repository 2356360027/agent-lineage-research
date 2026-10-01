"""Revised format diagnostic: model format violations are outcomes, not retries."""
import argparse
import hashlib
import itertools
import json
import random
import sys
import time
import urllib.request
from pathlib import Path
from statistics import mean

from .comprehension_probe import SINGLE
from .dependency_probe import check_server, sources
from .e1 import Client as LoopbackClient, canonical, digest, save
from .format_probe import ARMS, LEVELS, key_for, parse, request
from .native import verify
from .probe_score import check, diagnostics


def validate(d):
    expected = {'protocol': 'output-format-probe-v2', 'reliabilities': LEVELS,
                'arms': list(ARMS), 'repetitions': 3, 'seed': 14602837,
                'temperature': .7, 'top_p': .9, 'max_tokens': 256}
    check(canonical(d) == canonical(expected), 'Frozen design changed')


def prompt(a, value, arm):
    check(arm in ARMS and value in (0, 1), 'Arm/value')
    base = SINGLE.split('Return only JSON with ')[0]
    base += 'The value field is an observed measurement, not the unknown state Y. '
    probability = ('p_state_1 always means P(Y=1 given the visible observation), '
                   'not confidence in a chosen state or probability of measurement reliability. '
                   'Keep the event Y=1 fixed even when state 0 is more likely. ')
    decision = 'answer is your estimate of Y, not whether the measurement is reliable. '
    instructions = {
        'joint': decision + probability +
            'Return only one JSON object with exactly three fields: answer (integer 0 or 1), '
            'p_state_1 (number from 0 to 1), citations (list of visible observation IDs).',
        'probability_only': probability +
            'Return only one JSON object with exactly one field: p_state_1 (number from 0 to 1).',
        'answer_only': decision +
            'Return only one JSON object with exactly one field: answer (integer 0 or 1).',
    }
    return [{'role': 'system', 'content': base + instructions[arm] + ' Do not add any other fields or text.'},
            {'role': 'user', 'content': canonical({'observations': [
                {'id': 'O0', 'value': value, 'effective_reliability': a}]})}]


def outcome(raw, arm, model):
    # Broken API envelopes or wrong models are infrastructure failures. A valid
    # envelope with invalid model-generated content is a retained outcome.
    check(isinstance(raw, dict) and raw.get('model') == model, 'API/model mismatch')
    choices = raw.get('choices')
    check(isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict), 'API choices missing')
    choice = choices[0]
    check(isinstance(choice.get('message'), dict) and isinstance(choice.get('finish_reason'), str), 'Broken API choice')
    if not isinstance(choice['message'].get('content'), str):
        return {'format_valid': False, 'assessment': None, 'violation': 'Non-text content'}
    # Reject duplicate JSON keys explicitly instead of silently selecting one.
    def unique_pairs(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError('Duplicate JSON key')
            obj[k] = v
        return obj
    try:
        json.loads(choice['message']['content'], object_pairs_hook=unique_pairs)
        assessment = parse(raw, arm)
    except (ValueError, TypeError) as exc:
        return {'format_valid': False, 'assessment': None, 'violation': str(exc)}
    return {'format_valid': True, 'assessment': assessment, 'violation': None}


class Client(LoopbackClient):
    def call(self, key, messages, seed, arm):
        payload = request(self.config, messages, seed); path = self.directory / (key + '.json')
        check(not path.exists(), 'Existing call; no overwrite or retry')
        rec = {'request': payload, 'request_hash': digest(payload), 'arm': arm, 'status': 'pending',
               'usage': None, 'started_unix': time.time(), 'data_status': 'MODEL_ENDPOINT_FORMAT_V2'}
        save(path, rec); started = time.perf_counter()
        try:
            req = urllib.request.Request(self.endpoint.rstrip('/') + '/chat/completions',
                    canonical(payload).encode(), {'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=180) as response:
                rec['raw_text'] = response.read().decode()
            raw = json.loads(rec['raw_text']); rec['raw_response'] = raw; rec['usage'] = raw.get('usage')
            result = outcome(raw, arm, self.config['model']); rec['outcome'] = result
            rec['status'] = 'ok' if result['format_valid'] else 'format_violation'
            return result
        except Exception as exc:
            rec['status'] = 'failed'; rec['error_type'] = type(exc).__name__
            raise
        finally:
            rec['wall_seconds'] = time.perf_counter() - started; save(path, rec)


class Audit:
    def __init__(self, directory, cfg):
        self.directory = directory; self.cfg = cfg; self.calls = []; self.keys = set()

    def call(self, key, messages, seed, arm):
        check(key not in self.keys, 'Duplicate call'); self.keys.add(key)
        rec = json.loads((self.directory / (key + '.json')).read_text(encoding='utf-8'))
        expected = request(self.cfg, messages, seed)
        check(rec['request'] == expected and rec['request_hash'] == digest(expected), 'Request mismatch')
        check(rec['arm'] == arm, 'Arm mismatch')
        check(json.loads(rec['raw_text']) == rec['raw_response'], 'Raw mismatch')
        check(rec['usage'] == rec['raw_response'].get('usage'), 'Usage mismatch')
        result = outcome(rec['raw_response'], arm, self.cfg['model'])
        check(rec['outcome'] == result, 'Outcome mismatch')
        check(rec['status'] == ('ok' if result['format_valid'] else 'format_violation'), 'Unresolved/status mismatch')
        self.calls.append(rec); return result


def run_cell(client, d, cell, directory=None):
    a = d['reliabilities'][cell]
    order = list(itertools.product((0, 1), ARMS, range(3)))
    random.Random(f'format-v2:{d["seed"]}:{cell}').shuffle(order)
    result = {'cell': cell, 'reliability': a, 'order': [list(x) for x in order], 'outputs': {}}
    for value, arm, repeat in order:
        seed = int(digest([d['protocol'], d['seed'], cell, repeat])[:7], 16)
        result['outputs'][key_for(cell, value, arm, repeat)] = client.call(
            key_for(cell, value, arm, repeat), prompt(a, value, arm), seed, arm)
    if directory is not None:
        save(directory / f'result-{cell}.json', result)
    return result


def group_stats(rows):
    n = len(rows); valid = [r for r in rows if r['format_valid']]; arm = rows[0]['arm']
    result = {'scheduled': n, 'format_valid': len(valid), 'format_violations': n-len(valid),
              'format_valid_rate': len(valid)/n}
    if arm in ('joint', 'answer_only'):
        result['valid_and_correct_answer_rate_all_calls'] = sum(r['answer_direction'] for r in valid)/n
        result['answer_direction_among_valid'] = mean(r['answer_direction'] for r in valid) if valid else None
    if arm in ('joint', 'probability_only'):
        for k in ('mae', 'mse', 'tie', 'probability_direction'):
            result[k + '_among_valid'] = mean(r[k] for r in valid) if valid else None
        ties = sum(r['tie'] for r in valid)
        result['observed_ties'] = ties
        # Partial-identification bounds, NOT imputed predictions or repaired rows.
        result['all_call_tie_rate_bounds'] = [ties/n, (ties+n-len(valid))/n]
        err = sum(r['mae'] for r in valid)
        result['all_call_mae_bounds'] = [err/n, (err+sum(max(r['oracle_p'], 1-r['oracle_p'])
                                                        for r in rows if not r['format_valid']))/n]
    if arm == 'joint':
        result['inconsistency_among_valid'] = mean(r['inconsistent'] for r in valid) if valid else None
    return result


def summarize(root):
    dr = root / 'shard-000'; m = json.loads((dr / 'manifest.json').read_text(encoding='utf-8'))
    d, cfg = m['design'], m['config']; validate(d)
    check(digest(sources()) == m['source_sha256'] == digest(m['source_bundle']), 'Source mismatch')
    check(all(cfg[k] == d[k] for k in ('temperature', 'top_p', 'max_tokens')), 'Config mismatch')
    check_server(cfg, json.loads((dr / 'server_metadata.json').read_text(encoding='utf-8')), False)
    check(len(list(dr.glob('result-*.json'))) == 12 and len(list(dr.glob('i*.json'))) == 216, 'Incomplete/extra records')
    audit = Audit(dr, cfg); rows = []; contrasts = []; symmetry = {'joint': [], 'probability_only': []}
    for cell, a in enumerate(d['reliabilities']):
        result = json.loads((dr / f'result-{cell}.json').read_text(encoding='utf-8'))
        check(result == run_cell(audit, d, cell), 'Result/order mismatch')
        index = {}
        for value, arm, repeat in itertools.product((0, 1), ARMS, range(3)):
            out = result['outputs'][key_for(cell, value, arm, repeat)]; q = a if value else 1-a
            row = {'cell': cell, 'value': value, 'arm': arm, 'repeat': repeat, 'oracle_p': q, **out}
            if out['format_valid']:
                v = out['assessment']
                if 'answer' in v:
                    row['answer_direction'] = int(v['answer'] == value)
                if 'p_state_1' in v:
                    p = v['p_state_1']; row.update(mae=abs(p-q), mse=(p-q)**2, tie=int(p == .5),
                        probability_direction=int(p != .5 and int(p > .5) == value))
                if arm == 'joint':
                    row['inconsistent'] = int(p != .5 and v['answer'] != int(p > .5))
            rows.append(row); index[(value, arm, repeat)] = row
        for value, repeat in itertools.product((0, 1), range(3)):
            j = index[value, 'joint', repeat]; b = index[value, 'probability_only', repeat]
            both = j['format_valid'] and b['format_valid']
            contrasts.append({'cell': cell, 'value': value, 'repeat': repeat, 'both_format_valid': both,
                              'B_minus_A_mae': b['mae']-j['mae'] if both else None,
                              'B_minus_A_tie': b['tie']-j['tie'] if both else None})
        for arm, repeat in itertools.product(symmetry, range(3)):
            pair = [index[value, arm, repeat] for value in (0, 1)]
            symmetry[arm].append(abs(sum(r['assessment']['p_state_1'] for r in pair)-1)
                                 if all(r['format_valid'] for r in pair) else None)
    arms = {arm: group_stats([r for r in rows if r['arm'] == arm]) for arm in ARMS}
    strata = {f'{arm}_v{v}': group_stats([r for r in rows if r['arm'] == arm and r['value'] == v])
              for arm, v in itertools.product(ARMS, (0, 1))}
    def paired(metric):
        vals = [r[metric] for r in contrasts if r['both_format_valid']]
        return {'matched_pairs': len(vals), 'scheduled_pairs': 72, 'mean': mean(vals) if vals else None}
    complete = all(isinstance(c['usage'], dict) and all(type(c['usage'].get(k)) is int and c['usage'][k] >= 0
                    for k in ('prompt_tokens', 'completion_tokens')) for c in audit.calls)
    report = {'status': 'EXPLORATORY_FORMAT_V2_COMPLETE', 'calls': 216, 'cells': 12,
        'arms': arms, 'value_strata': strata,
        'primary_reporting': {'format_valid_rates': {k: v['format_valid_rate'] for k, v in arms.items()},
            'conditional_B_minus_A_mae': paired('B_minus_A_mae'), 'conditional_B_minus_A_tie': paired('B_minus_A_tie'),
            'all_call_B_minus_A_mae_bounds': [arms['probability_only']['all_call_mae_bounds'][0]-arms['joint']['all_call_mae_bounds'][1],
                                             arms['probability_only']['all_call_mae_bounds'][1]-arms['joint']['all_call_mae_bounds'][0]]},
        'complement_symmetry': {a: {'valid_pairs': sum(v is not None for v in vs), 'scheduled_pairs': 36,
            'mean_among_valid_pairs': mean([v for v in vs if v is not None]) if any(v is not None for v in vs) else None}
            for a, vs in symmetry.items()},
        'usage_complete': complete,
        'prompt_tokens': sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,
        'completion_tokens': sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,
        'sum_call_seconds': sum(c['wall_seconds'] for c in audit.calls),
        'warning': 'Exploratory revision after one aborted v1 call. Same grid/new seeds, not fresh population validation. Invalid outputs retained with no repaired probabilities; conditional estimates may have selection bias. Bounds are not imputations or confidence intervals. No significance/pass claim or DICE efficacy.'}
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.rglob('*.json'))}
    return report, rows, contrasts, hashes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--design', default='configs/format_probe_v2.json')
    p.add_argument('--native-config', default='runtime/runs/native-smoke-config.json')
    p.add_argument('--output', default='runtime/runs/format-diagnostic-v2-001')
    for action in ('execute', 'score', 'audit-only'):
        p.add_argument('--'+action, action='store_true')
    args = p.parse_args(); root = Path(args.output)
    check(sum([args.execute, args.score, args.audit_only]) <= 1, 'Choose one action')
    if args.audit_only:
        print(json.dumps(diagnostics(root), indent=2)); return
    if args.score:
        s, r, c, h = summarize(root)
        for name, data in [('summary', s), ('cell_metrics', r), ('paired_contrasts', c), ('audit_input_hashes', h)]:
            save(root / (name+'.json'), data)
        print(json.dumps(s, indent=2)); return
    d = json.loads(Path(args.design).read_text(encoding='utf-8')); validate(d)
    print(json.dumps({'mode': 'execute' if args.execute else 'plan_only', 'calls': 216,
                      'design_sha256': digest(d)}, indent=2), flush=True)
    if not args.execute:
        return
    cfg = json.loads(Path(args.native_config).read_text(encoding='utf-8'))
    check(cfg.get('deployment') == 'native', 'Native model required')
    cfg.update({k: d[k] for k in ('temperature', 'top_p', 'max_tokens')})
    root.mkdir(parents=True, exist_ok=False); dr = root/'shard-000'; dr.mkdir(); bundle = sources()
    save(dr/'manifest.json', {'design': d, 'config': cfg, 'source_bundle': bundle,
        'source_sha256': digest(bundle), 'created_unix': time.time(), 'python': sys.version})
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models', timeout=10) as response:
        metadata = json.load(response)
    check_server(cfg, metadata); save(dr/'server_metadata.json', metadata)
    client = Client(cfg, 'http://127.0.0.1:8000/v1', dr)
    for cell in range(12):
        run_cell(client, d, cell, dr); print(f'Completed cell {cell}; total=12', flush=True)
    print('Worker completed.', flush=True)


if __name__ == '__main__':
    main()
