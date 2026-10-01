"""Frozen prompt-ID bridge v1: 12 fresh cells, 384 real calls.

Paired 2x2 instruction bundle (legacy/adapted vs current) x identifier
convention (sequential vs opaque), crossed with origin relation, label
complement, and independent selector/final calls. The user JSON schema,
message order, evidence, correct calculator and decoding are held fixed.
Legacy text ONLY normalizes the origin-map field name; both arms receive
the same current map schema. Thus not an exact historical replay: neither
schema changes nor individual words are causally isolated. IDs differ in
token length and naming/alignment cues, a bundled intervention.

Primary descriptive copy endpoints: correct origin-set selection and final
posterior MAE; report both main effects and difference-in-differences.
Independent-source retention, exact representative order, answer/probability
inconsistency are secondary. No p-values, optional stopping, retries, or
imputation. All final calls get a correct oracle calculator computed from
visible records plus experiment-known origins, not from the model selector.
No claim of internal reasoning, DICE efficacy or a new dedup algorithm.
"""
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
from . import origin_representation as origin
from . import lineage_utility as lu
from .dependency_probe import sources, check_server
from .e1 import canonical, digest, save
from .format_probe_v2 import Client, Audit
from .native import verify
from .probe_score import check, diagnostics
from .stage_diagnostic import metrics
from .scaffold_probe import stats

DESIGN = dict(protocol='prompt-id-bridge-v1', seed=115849726, cells=12, calls=384,
              temperature=0., top_p=1., max_tokens=512)
WORDS = ('legacy', 'current')
IDS = ('sequential', 'opaque')
RELATIONS = ('copy', 'independent')
STAGES = ('select', 'final')

def tasks(d):
    cells = origin.tasks(d)
    previous = {tuple(c['reliabilities']) for c in origin.tasks(origin.DESIGN)}
    check(not any(tuple(c['reliabilities']) in previous for c in cells), 'Prior cell collision')
    return cells

def world(d, i, flip, relation, naming):
    if naming == 'sequential':
        return lu.world(tasks(d)[i], i, flip, relation)
    check(naming == 'opaque', 'ID convention')
    return origin.world(d, i, flip, relation)

def trial(d, i, rel, flip, words, naming, stage):
    obs, roots = world(d, i, flip, rel, naming)
    unique, _ = lu.route(obs, roots, 'verified_root')
    payload = origin.encode(obs, roots, 'map')
    if stage == 'final':
        payload['external_calculator'] = lu.tool_for(unique)
    if words == 'legacy':
        system = lu.SYSTEM.replace('experiment_authenticated_origins', 'origin_registry')
        system += lu.SELECT if stage == 'select' else lu.FINAL
    else:
        check(words == 'current', 'Instruction bundle')
        system = origin.STRUCTURE + origin.GENERATIVE + (origin.SELECT if stage == 'select' else origin.FINAL)
    return [dict(role='system', content=system), dict(role='user', content=canonical(payload))], obs, roots, unique

def schedule(d, i):
    result = list(itertools.product(RELATIONS, (0, 1), WORDS, IDS, STAGES))
    random.Random(f'{d["seed"]}:{i}:schedule').shuffle(result)
    return result

def run_cell(client, d, i, directory=None):
    result = dict(cell=i, task=tasks(d)[i], outputs={})
    seed = int(digest([d['protocol'], d['seed'], i])[:7], 16)
    for rel, flip, words, naming, stage in schedule(d, i):
        prompt, obs, roots, unique = trial(d, i, rel, flip, words, naming, stage)
        client.evaluator = partial(lu.evaluate, visible={o['id'] for o in obs})
        key = f'i{i}-{rel}-f{flip}-{words}-{naming}-{stage}'
        result['outputs'][key] = dict(relation=rel, flip=flip, words=words, naming=naming,
            stage=stage, outcome=client.call(key, prompt, seed, stage))
    if directory is not None:
        save(directory / f'result-{i}.json', result)
    return result

def summarize(root):
    dr = root / 'shard-000'
    manifest = json.loads((dr / 'manifest.json').read_text())
    d, cfg = manifest['design'], manifest['config']
    check(d == DESIGN, 'Frozen design changed')
    check(digest(sources()) == manifest['source_sha256'] == digest(manifest['source_bundle']), 'Source mismatch')
    check(all(cfg[k] == d[k] for k in ('temperature', 'top_p', 'max_tokens')), 'Config mismatch')
    check_server(cfg, json.loads((dr / 'server_metadata.json').read_text()), False)
    check(len(list(dr.glob('i*.json'))) == 384 and len(list(dr.glob('result-*.json'))) == 12, 'Coverage mismatch')
    audit = Audit(dr, cfg)
    rows, index = [], {}
    for i in range(12):
        result = json.loads((dr / f'result-{i}.json').read_text())
        check(result == run_cell(audit, d, i), 'Replay mismatch')
        for entry in result['outputs'].values():
            rel, flip, words, naming, stage = [entry[k] for k in ('relation', 'flip', 'words', 'naming', 'stage')]
            _, obs, roots, unique = trial(d, i, rel, flip, words, naming, stage)
            out = entry['outcome']
            row = dict(cell=i, relation=rel, flip=flip, words=words, naming=naming, stage=stage)
            if stage == 'final':
                row.update(metrics(out, unique, 'direct', .0001))
                a = out['assessment'] if out['format_valid'] else None
                row['answer_probability_inconsistent'] = int(a['answer'] != int(a['p_state_1'] > .5)) if a and a['p_state_1'] != .5 else None
            else:
                ids = out['assessment']['selected_message_ids'] if out['format_valid'] else None
                expected = [o['id'] for o in unique]
                row.update(out)
                row.update(correct_origins=int(ids is not None and len(ids) == len(expected) and {roots[k] for k in ids} == {roots[k] for k in expected}),
                           exact_representatives=int(ids == expected))
            rows.append(row)
            index[i, rel, flip, words, naming, stage] = row
    groups = {}
    for rel, words, naming, stage in itertools.product(RELATIONS, WORDS, IDS, STAGES):
        rs = [r for r in rows if (r['relation'], r['words'], r['naming'], r['stage']) == (rel, words, naming, stage)]
        g = stats(rs)
        if stage == 'select':
            g.update(correct_origins=sum(r['correct_origins'] for r in rs), exact_representatives=sum(r['exact_representatives'] for r in rs))
        else:
            g['answer_probability_inconsistent'] = sum(r['answer_probability_inconsistent'] == 1 for r in rs)
            g['inconsistency_evaluable'] = sum(r['answer_probability_inconsistent'] is not None for r in rs)
        groups[f'{rel}:{words}:{naming}:{stage}'] = g
    pairs = []
    for i, rel, flip, stage in itertools.product(range(12), RELATIONS, (0, 1), STAGES):
        def value(w, n):
            row = index[i, rel, flip, w, n, stage]
            return row['correct_origins'] if stage == 'select' else row.get('mae') if row['format_valid'] else None
        a, b, c, e = value('legacy', 'sequential'), value('current', 'sequential'), value('legacy', 'opaque'), value('current', 'opaque')
        valid = all(v is not None for v in (a, b, c, e))
        effects = dict(wording_main=((b-a)+(e-c))/2, naming_main=((c-a)+(e-b))/2, interaction=(e-c)-(b-a)) if valid else dict.fromkeys(('wording_main', 'naming_main', 'interaction'))
        pairs.append(dict(cell=i, relation=rel, flip=flip, stage=stage, **effects))
    contrasts = {}
    for rel, stage, effect in itertools.product(RELATIONS, STAGES, ('wording_main', 'naming_main', 'interaction')):
        ps = [p for p in pairs if p['relation'] == rel and p['stage'] == stage]
        per_cell = {str(i): mean(p[effect] for p in ps if p['cell'] == i) for i in range(12) if all(p[effect] is not None for p in ps if p['cell'] == i)}
        contrasts[f'{rel}:{stage}:{effect}'] = dict(complete_mean=mean(per_cell.values()) if len(per_cell) == 12 else None,
            complete_cells=len(per_cell), per_cell=per_cell, unit='correct-origin rate' if stage == 'select' else 'posterior MAE')
    complete_usage = all(isinstance(c['usage'], dict) and all(type(c['usage'].get(k)) is int for k in ('prompt_tokens', 'completion_tokens')) for c in audit.calls)
    summary = dict(status='PROMPT_ID_BRIDGE_DIAGNOSTIC', calls=len(audit.calls), independent_cells=12, groups=groups,
        primary_copy_contrasts={k:v for k,v in contrasts.items() if k.startswith('copy:')}, secondary_independent_contrasts={k:v for k,v in contrasts.items() if k.startswith('independent:')},
        invalid_calls=sum(not c['outcome']['format_valid'] for c in audit.calls),
        prompt_tokens=sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete_usage else None,
        completion_tokens=sum(c['usage']['completion_tokens'] for c in audit.calls) if complete_usage else None,
        sum_call_seconds=sum(c['wall_seconds'] for c in audit.calls),
        warning='12 synthetic cells/one model; descriptive factorial bundles, not individual words or token-length matched effects. Legacy schema normalized, not exact historical replication. Correct tool supplied; selector/final independent calls. No DICE efficacy or internal mechanism claim.')
    hashes = {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dr.glob('*.json'))}
    return summary, rows, pairs, hashes

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--native-config', default='runtime/runs/native-smoke-config.json')
    parser.add_argument('--output', default='runtime/runs/prompt-id-bridge-001')
    for action in ('execute', 'score', 'audit-only'):
        parser.add_argument('--'+action, action='store_true')
    a = parser.parse_args(); root = Path(a.output)
    check(sum((a.execute, a.score, a.audit_only)) <= 1, 'Action')
    if a.audit_only: print(json.dumps(diagnostics(root), indent=2)); return
    if a.score:
        objects = summarize(root)
        for name, obj in zip(('summary', 'cell_metrics', 'paired_contrasts', 'audit_input_hashes'), objects): save(root/(name+'.json'), obj)
        print(json.dumps(objects[0], indent=2)); return
    d = dict(DESIGN); tasks(d)
    print(json.dumps(dict(design=d, design_sha256=digest(d), execute=a.execute)), flush=True)
    if not a.execute: return
    cfg = json.loads(Path(a.native_config).read_text()); check(cfg.get('deployment') == 'native', 'Native only')
    cfg.update({k:d[k] for k in ('temperature', 'top_p', 'max_tokens')})
    root.mkdir(parents=True, exist_ok=False); dr = root/'shard-000'; dr.mkdir(); bundle = sources()
    save(dr/'manifest.json', dict(design=d, config=cfg, source_bundle=bundle, source_sha256=digest(bundle), created_unix=time.time(), python=sys.version))
    verify(cfg)
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models', timeout=10) as response: metadata = json.load(response)
    check_server(cfg, metadata); save(dr/'server_metadata.json', metadata)
    client = Client(cfg, 'http://127.0.0.1:8000/v1', dr)
    for i in range(12): run_cell(client, d, i, dr); print(f'Completed cell {i}; total=12', flush=True)
    print('Worker completed.', flush=True)

if __name__ == '__main__': main()
