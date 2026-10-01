"""Fixed competence-gated pipeline: 144 + 72 + 288 maximum real calls."""
import argparse
from functools import partial
import hashlib
import itertools
import json
import random
import sys
import time
import urllib.request
from pathlib import Path
from statistics import mean

from .dependency_probe import check_server, sources
from .e1 import canonical, digest, parse, save
from .format_probe_v2 import Client, Audit, outcome, prompt as joint_prompt
from .native import verify
from .probe_score import check, diagnostics

SINGLE_LEVELS = [.517,.533,.557,.581,.613,.629,.653,.677,.701,.719,.737,.761,
                 .779,.797,.811,.829,.853,.871,.893,.911,.929,.947,.961,.977]
GATE = {'mae_max': .05, 'stratum_mae_max': .075, 'direction_min': .95,
        'inconsistency_max': .02, 'complement_max': .05}
STAGES = ('single', 'double', 'communication')


def validate(d):
    expected = {'protocol': 'readiness-pipeline-v1', 'single_reliabilities': SINGLE_LEVELS,
        'double_cells': 12, 'communication_cells': 12, 'repetitions': 3, 'seed': 25108369,
        'temperature': .7, 'top_p': .9, 'max_tokens': 256, 'gate': GATE}
    check(canonical(d) == canonical(expected), 'Frozen design changed')


def cells(d, stage):
    check(stage in STAGES, 'Stage')
    if stage == 'single':
        return [{'reliabilities': [a], 'base_values': [0], 'regime': 'single'} for a in SINGLE_LEVELS]
    rng = random.Random(f'{d["protocol"]}:{d["seed"]}:{stage}:worlds')
    pairs = []
    while len(pairs) < 12:
        # Explicit restriction avoids exact/near decision ties. It is part of
        # the task domain, not an exclusion based on model outcomes.
        ints = rng.sample(range(620, 951), 2)
        if abs(ints[0]-ints[1]) < 80 or tuple(ints) in pairs:
            continue
        pairs.append(tuple(ints))
    return [{'reliabilities': [r/1000 for r in pair],
             'base_values': [0, 0 if i % 2 == 0 else 1],
             'regime': 'agree' if i % 2 == 0 else 'conflict'} for i, pair in enumerate(pairs)]


def observations(cell, flip):
    return [{'id': f'O{i}', 'value': v ^ flip, 'effective_reliability': a}
            for i, (v, a) in enumerate(zip(cell['base_values'], cell['reliabilities']))]


def oracle(obs):
    unique = {}
    for o in obs:
        if o['id'] in unique:
            check(unique[o['id']] == o, 'Conflicting observation ID')
        unique[o['id']] = o
    l1 = l0 = 1.
    for o in unique.values():
        a = o['effective_reliability']; v = o['value']
        l1 *= a if v else 1-a; l0 *= 1-a if v else a
    return l1/(l1+l0)


def prompt(cell, flip, stage, condition):
    obs = observations(cell, flip)
    if stage == 'single':
        check(condition == 'hidden', 'Single condition')
        return joint_prompt(obs[0]['effective_reliability'], obs[0]['value'], 'joint')
    check(condition in ('hidden', 'repeat', 'verdict0', 'verdict1'), 'Condition')
    suffix = joint_prompt(.7, 0, 'joint')[0]['content'].split('The value field is an observed measurement', 1)[1]
    system = ('Infer binary state Y with prior P(Y=1)=0.5. Each unique observation X '
              'equals Y with its effective_reliability, symmetrically for Y=0 and Y=1. '
              'Distinct observation IDs are conditionally independent given Y. '
              'Repeated observation IDs denote the same measurement, not new evidence. '
              'Peer assessments, if present, are advisory judgments, may be wrong, '
              'and contain no observations beyond the observations list. '
              'The value field is an observed measurement' + suffix)
    peers = [] if condition in ('hidden', 'repeat') else [{'agent': 'peer', 'answer': int(condition[-1])}]
    evidence = obs + [dict(obs[0])] if condition == 'repeat' else obs
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': canonical(
        {'observations': evidence, 'peer_assessments': peers})}]


def evaluate(raw, arm, model, visible):
    result = outcome(raw, arm, model)
    # The existing strict evaluator checks duplicate keys, envelope, types and
    # schema first. Only its single-observation citation allowlist is extended.
    if result['violation'] == 'Invalid citations':
        try:
            assessment = parse(raw, visible)
        except (ValueError, TypeError):
            return result
        return {'format_valid': True, 'assessment': assessment, 'violation': None}
    return result


def count(stage):
    return {'single': 144, 'double': 72, 'communication': 288}[stage]


def run_cell(client, d, stage, cell_index, directory=None):
    cell = cells(d, stage)[cell_index]
    arms = ('hidden', 'repeat', 'verdict0', 'verdict1') if stage == 'communication' else ('hidden',)
    schedule = list(itertools.product((0, 1), arms, range(3)))
    random.Random(f'{d["seed"]}:{stage}:{cell_index}:order').shuffle(schedule)
    result = {'cell': cell_index, 'task': cell, 'schedule': [list(x) for x in schedule], 'outputs': {}}
    for flip, arm, repeat in schedule:
        seed = int(digest([d['protocol'], d['seed'], stage, cell_index, repeat])[:7], 16)
        key = f'i{cell_index}-f{flip}-{arm}-r{repeat}'
        result['outputs'][key] = client.call(key, prompt(cell, flip, stage, arm), seed, 'joint')
    if directory is not None:
        save(directory/f'result-{cell_index}.json', result)
    return result


def stats(rows):
    valid = [r for r in rows if r['format_valid']]
    result = {'scheduled': len(rows), 'valid': len(valid), 'invalid': len(rows)-len(valid)}
    for name in ('mae', 'mse', 'direction', 'probability_direction', 'tie', 'inconsistent'):
        result[name+'_among_valid'] = mean(r[name] for r in valid) if valid else None
    return result


def gate(rows, symmetry):
    all_valid = all(r['format_valid'] for r in rows)
    checks = {'all_format_valid': all_valid}
    if not all_valid:
        return {'passed': False, 'checks': checks, 'reason': 'Invalid outputs prevent competence gate'}
    checks['overall_mae'] = mean(r['mae'] for r in rows) <= GATE['mae_max']
    checks['complement'] = mean(symmetry) <= GATE['complement_max']
    for group in sorted({r['stratum'] for r in rows}):
        rs = [r for r in rows if r['stratum'] == group]
        checks[group+'_mae'] = mean(r['mae'] for r in rs) <= GATE['stratum_mae_max']
        for metric in ('direction', 'probability_direction'):
            checks[group+'_'+metric] = mean(r[metric] for r in rs) >= GATE['direction_min']
        checks[group+'_inconsistent'] = mean(r['inconsistent'] for r in rs) <= GATE['inconsistency_max']
    return {'passed': all(checks.values()), 'checks': checks,
            'meaning': 'Operational finite-grid gate, not a population or efficacy claim'}


def summarize(root):
    dr = root/'shard-000'; m = json.loads((dr/'manifest.json').read_text(encoding='utf-8'))
    d, cfg, stage = m['design'], m['config'], m['stage']; validate(d)
    check(digest(sources()) == m['source_sha256'] == digest(m['source_bundle']), 'Source mismatch')
    check(all(cfg[k] == d[k] for k in ('temperature', 'top_p', 'max_tokens')), 'Config mismatch')
    check_server(cfg, json.loads((dr/'server_metadata.json').read_text(encoding='utf-8')), False)
    tasks = cells(d, stage)
    check(len(list(dr.glob('result-*.json'))) == len(tasks) and len(list(dr.glob('i*.json'))) == count(stage), 'Incomplete/extra records')
    audit = Audit(dr, cfg, partial(evaluate, visible={'O0'} if stage == 'single' else {'O0', 'O1'}))
    rows = []; sym = []; contrasts = []
    for i, cell in enumerate(tasks):
        result = json.loads((dr/f'result-{i}.json').read_text(encoding='utf-8'))
        check(result == run_cell(audit, d, stage, i), 'Results mismatch'); indexed = {}
        for flip, arm, repeat in result['schedule']:
            out = result['outputs'][f'i{i}-f{flip}-{arm}-r{repeat}']; q = oracle(observations(cell, flip))
            row = {'cell': i, 'flip': flip, 'condition': arm, 'repeat': repeat, 'oracle_p': q,
                   'stratum': cell['regime']+'_target'+str(int(q > .5)), **out}
            if out['format_valid']:
                v = out['assessment']; p = v['p_state_1']
                row.update(mae=abs(p-q), mse=(p-q)**2, direction=int(v['answer'] == int(q > .5)),
                           probability_direction=int(p != .5 and (p > .5) == (q > .5)), tie=int(p == .5),
                           inconsistent=int(p != .5 and v['answer'] != int(p > .5)))
            rows.append(row); indexed[flip, arm, repeat] = row
        for repeat in range(3):
            pair = [indexed[f, 'hidden', repeat] for f in (0, 1)]
            if all(r['format_valid'] for r in pair):
                sym.append(abs(sum(r['assessment']['p_state_1'] for r in pair)-1))
            if stage == 'communication':
                for flip in (0, 1):
                    selected = [indexed[flip, arm, repeat] for arm in ('hidden','repeat','verdict0','verdict1')]
                    valid = all(r['format_valid'] for r in selected)
                    h, dup, v0, v1 = selected
                    contrasts.append({'cell': i, 'flip': flip, 'repeat': repeat, 'all_valid': valid,
                        'verdict1_minus_verdict0_probability': v1['assessment']['p_state_1']-v0['assessment']['p_state_1'] if valid else None,
                        'repeat_minus_hidden_mae': dup['mae']-h['mae'] if valid else None})
    report = {'stage': stage, 'status': 'COMPLETE', 'calls': count(stage), 'cells': len(tasks),
              'overall': stats(rows), 'strata': {g: stats([r for r in rows if r['stratum']==g]) for g in sorted({r['stratum'] for r in rows})},
              'conditions': {a: stats([r for r in rows if r['condition']==a]) for a in sorted({r['condition'] for r in rows})},
              'hidden_complement_error': mean(sym) if sym else None, 'hidden_complement_valid_pairs': len(sym)}
    if stage != 'communication':
        report['gate'] = gate(rows, sym)
    else:
        valid = [r for r in contrasts if r['all_valid']]
        report['primary_descriptive_contrasts'] = {'scheduled_pairs': 72, 'valid_pairs': len(valid),
            **{k: mean(r[k] for r in valid) if valid else None for k in (
                'verdict1_minus_verdict0_probability', 'repeat_minus_hidden_mae')}}
    complete = all(isinstance(c['usage'], dict) and all(type(c['usage'].get(k)) is int and c['usage'][k] >= 0
        for k in ('prompt_tokens','completion_tokens')) for c in audit.calls)
    report.update(usage_complete=complete,
        prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,
        sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='Finite selected task domain with repeated draws. No population inference. Valid-only effects may have selection bias. Communication verdicts are assigned interventions, not natural peers. No DICE efficacy established; earlier negative results unchanged.')
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.rglob('*.json'))}
    return report, rows, contrasts, hashes


def score_and_save(root):
    s, r, c, h = summarize(root)
    for name, obj in [('summary',s),('cell_metrics',r),('paired_contrasts',c),('audit_input_hashes',h)]:
        save(root/(name+'.json'), obj)
    return s


def execute(d, cfg, root):
    root.mkdir(parents=True, exist_ok=False); bundle = sources()
    save(root/'pipeline.json', {'design': d, 'source_sha256': digest(bundle), 'status': 'running', 'stages': []})
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models', timeout=10) as response:
        metadata = json.load(response)
    check_server(cfg, metadata); completed = []
    for stage in STAGES:
        stage_root = root/stage; dr = stage_root/'shard-000'; dr.mkdir(parents=True)
        save(dr/'manifest.json', {'stage': stage, 'design': d, 'config': cfg, 'source_bundle': bundle,
             'source_sha256': digest(bundle), 'created_unix': time.time(), 'python': sys.version})
        save(dr/'server_metadata.json', metadata)
        client = Client(cfg, 'http://127.0.0.1:8000/v1', dr,
                        partial(evaluate, visible={'O0'} if stage == 'single' else {'O0','O1'}))
        for i in range(len(cells(d, stage))):
            run_cell(client, d, stage, i, dr); print(f'Completed stage={stage} cell={i}', flush=True)
        s = score_and_save(stage_root); completed.append(stage)
        passed = s.get('gate', {}).get('passed', True)
        state = {'design': d, 'source_sha256': digest(bundle), 'stages': completed,
                 'status': 'stopped_gate_not_passed' if not passed else 'complete' if stage == STAGES[-1] else 'running',
                 'last_gate_passed': passed}
        save(root/'pipeline.json', state)
        print(f'Stage finished: {stage}; proceed={passed}', flush=True)
        if not passed:
            return


def main():
    p = argparse.ArgumentParser(); p.add_argument('--design', default='configs/readiness_pipeline_v1.json')
    p.add_argument('--native-config', default='runtime/runs/native-smoke-config.json')
    p.add_argument('--output', default='runtime/runs/readiness-pipeline-001')
    p.add_argument('--execute', action='store_true'); p.add_argument('--audit-only', action='store_true')
    args = p.parse_args(); root = Path(args.output)
    check(not(args.execute and args.audit_only), 'Choose one action')
    if args.audit_only:
        print(json.dumps({s: diagnostics(root/s) for s in STAGES if (root/s).exists()}, indent=2)); return
    d = json.loads(Path(args.design).read_text(encoding='utf-8')); validate(d)
    print(json.dumps({'mode': 'execute' if args.execute else 'plan_only', 'maximum_calls': 504,
                      'phase_calls': {s: count(s) for s in STAGES}, 'design_sha256': digest(d)}, indent=2), flush=True)
    if args.execute:
        cfg = json.loads(Path(args.native_config).read_text(encoding='utf-8')); check(cfg.get('deployment')=='native','Native only')
        cfg.update({k: d[k] for k in ('temperature','top_p','max_tokens')}); execute(d, cfg, root)


if __name__ == '__main__':
    main()
