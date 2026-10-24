"""Fresh fixed-prompt repetition diagnostic with position and length controls.

One frozen 32-question / 544-call batch. Punctuation padding is a matched-length
control, not an assumption of semantic neutrality. No peer debate or DICE claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import time
import urllib.error
import urllib.request

from . import natural_repetition as old
from . import citation_interface_v2 as interface
from .dependency_probe import sources, check_server
from .e1 import canonical, digest, save, Client as EndpointGuard
from .native import verify
from .natural_qa_data import prediction_view, seeded_order
from .probe_score import check

SYSTEM = old.SYSTEM + interface.EXPLICIT
GROUPS = ('once', 'replay', 'position', 'padding')
JOBS = tuple(f'{g}_a{a}' for g in GROUPS for a in range(3)) + ('single_union', 'question_only')
DATASET_SHA = interface.DATASET_SHA
NATURAL_CANDIDATES_SHA = interface.PARENT_CANDIDATES_SHA
CITATION_CANDIDATES_SHA = '88ea379601922af674c22ab1bbf0bfe6fbf51c63f2dc4af5c425ddbb74fd4598'
DATA_STATUS = 'REAL_MODEL_NATURAL_CONTEXT_REPETITION_V2_DIAGNOSTIC'
APPENDIX_HEADER = '\nSupplementary display block (punctuation-only rows are non-evidence):\n'


def validate_design(d):
    required = dict(protocol='natural-context-repetition-v2', seed=31032029,
        candidate_items=200, items=32, agents=3, actual_calls_per_item=17,
        temperature=0., top_p=1., max_tokens=256, max_answer_utf8_bytes=160,
        max_citations=8, prior_allowance_tokens=512, replay_top_units=3,
        extra_copies=2, bootstrap_seed=10103028, bootstrap_resamples=2000)
    check(all(d.get(k) == v for k, v in required.items()), 'Frozen numeric design changed')


def target_units(view, d):
    return old.replay_units(view, d)[len(view['units']):len(view['units']) + d['replay_top_units']]


def position_units(view, d):
    chosen = target_units(view, d)
    ids = {u['id'] for u in chosen}
    return [u for u in view['units'] if u['id'] not in ids] + chosen


def replay_appendix(view, d):
    repeated = target_units(view, d) * d['extra_copies']
    return APPENDIX_HEADER + '\n'.join(f"[{u['id']}] {u['title']} | {u['text']}" for u in repeated)


def prompt(view, units, agent=None, previous=None, appendix=None):
    messages = old.prompt(view, units, agent, previous)
    messages[0]['content'] = SYSTEM
    if appendix is not None:
        messages[1]['content'] += appendix
    return messages


def _padding(repeats, atom, tail=''):
    # Exactly six punctuation-only rows. No invented evidence IDs or words.
    return APPENDIX_HEADER + '\n'.join([' '.join([atom] * repeats) + tail] + ['.'] * 5)


def _preflight(view, replay, padding, tokenizer):
    checks = []
    for a in (None, 0, 1, 2):
        left = prompt(view, view['units'], a, appendix=replay)
        right = prompt(view, view['units'], a, appendix=padding)
        checks.append(dict(agent=a, replay_tokens=old.token_count(tokenizer, left),
            padding_tokens=old.token_count(tokenizer, right),
            replay_prompt_sha256=digest(left), padding_prompt_sha256=digest(right)))
    return checks


def prepare_control(view, d, tokenizer):
    """Deterministic, label-free bounded tokenizer search; never use S0 outputs."""
    replay = replay_appendix(view, d)
    target = old.token_count(tokenizer, prompt(view, view['units'], 0, appendix=replay))
    counts = {}
    def count(n, atom, tail=''):
        key = (n, atom, tail)
        if key not in counts:
            counts[key] = old.token_count(tokenizer, prompt(view, view['units'], 0,
                                                           appendix=_padding(n, atom, tail)))
        return counts[key]
    # Binary search is only a speed aid. Every proposed match is checked exactly;
    # a tokenizer with non-monotone counts can yield a recorded ineligible item.
    for atom in ('.', '!', ';', '?', ':', '-'):
        lo, hi = 1, 4096
        probes = set()
        while lo <= hi:
            mid = (lo + hi) // 2
            probes.add(mid)
            size = count(mid, atom)
            if size < target:
                lo = mid + 1
            elif size > target:
                hi = mid - 1
            else:
                lo = mid
                break
        probes.update(range(max(1, lo - 8), min(4096, lo + 8) + 1))
        for n in sorted(probes, key=lambda n: (abs(n - lo), n)):
            for tail in ('', '!', ':', ';', '?', '-', '/', '_', '=', '~'):
                if count(n, atom, tail) != target:
                    continue
                padding = _padding(n, atom, tail)
                checks = _preflight(view, replay, padding, tokenizer)
                if not all(r['replay_tokens'] == r['padding_tokens'] for r in checks):
                    continue
                control = dict(target_ids=[u['id'] for u in target_units(view, d)],
                    replay_appendix=replay, padding_appendix=padding, preflight_checks=checks)
                control['control_sha256'] = digest(control)
                return control
    return None


def validate_control(view, control, d):
    check(set(control) == {'target_ids', 'replay_appendix', 'padding_appendix',
                          'preflight_checks', 'control_sha256'}, 'Control fields changed')
    check(control['control_sha256'] == digest({k: v for k, v in control.items()
          if k != 'control_sha256'}), 'Control checksum mismatch')
    check(control['target_ids'] == [u['id'] for u in target_units(view, d)], 'Target selection changed')
    check(control['replay_appendix'] == replay_appendix(view, d), 'Replay appendix changed')
    padding = control['padding_appendix']
    check(isinstance(padding, str) and padding.startswith(APPENDIX_HEADER), 'Padding header changed')
    rows = padding[len(APPENDIX_HEADER):].split('\n')
    check(len(rows) == 6 and all(row and any(c != ' ' for c in row) and
          all(c in '.!;?:-/_=~ ' for c in row) for row in rows), 'Padding must be six punctuation-only rows')
    checks = control['preflight_checks']
    check(isinstance(checks, list) and [r['agent'] for r in checks] == [None, 0, 1, 2], 'Preflight recipients changed')
    for r in checks:
        check(set(r) == {'agent', 'replay_tokens', 'padding_tokens',
                        'replay_prompt_sha256', 'padding_prompt_sha256'}, 'Preflight fields')
        check(type(r['replay_tokens']) is int and r['replay_tokens'] > 0 and
              type(r['padding_tokens']) is int and r['replay_tokens'] == r['padding_tokens'], 'Preflight count mismatch')
        for arm in ('replay', 'padding'):
            expected = prompt(view, view['units'], r['agent'], appendix=control[arm+'_appendix'])
            check(r[arm+'_prompt_sha256'] == digest(expected), 'Preflight prompt mismatch')


class Client(interface.Client):
    def call(self, key, messages, seed, decoder, visible):
        check(decoder == 'json_object', 'Only frozen generic JSON decoder permitted')
        visible = sorted(set(visible))
        count = old.token_count(self.tokenizer, messages)
        check(count + self.config['max_tokens'] <= 4096, 'Actual context budget exceeded; no truncation')
        payload = interface.request(self.config, messages, seed, decoder, visible)
        path = self.directory / (key + '.json')
        check(not path.exists(), 'Existing call; no overwrite/retry')
        rec = dict(request=payload, request_hash=digest(payload), decoder=decoder,
            visible=visible, status='pending', usage=None, started_unix=time.time(),
            tokenizer_prompt_tokens=count, data_status=DATA_STATUS)
        save(path, rec)
        started = time.perf_counter()
        try:
            req = urllib.request.Request(self.endpoint.rstrip('/') + '/chat/completions',
                canonical(payload).encode(), {'Content-Type': 'application/json'})
            with self.opener.open(req, timeout=180) as response:
                rec['raw_text'] = response.read().decode()
            raw = json.loads(rec['raw_text'], object_pairs_hook=old.unique_json)
            rec.update(raw_response=raw, usage=raw.get('usage'))
            result = interface.evaluate(raw, decoder, self.config['model'], visible)
            rec.update(outcome=result, status='ok' if result['format_valid'] else 'format_violation')
            return result
        except Exception as exc:
            rec.update(status='failed', error_type=type(exc).__name__)
            if isinstance(exc, urllib.error.HTTPError):
                rec.update(http_status=exc.code, error_body=exc.read().decode(errors='replace'))
            raise
        finally:
            rec['wall_seconds'] = time.perf_counter() - started
            save(path, rec)


class Audit(interface.Audit):
    """Offline raw-record reconstruction, not an independent tokenization run."""
    def recorded_token_count(self, key, messages):
        rec = json.loads((self.directory / (key + '.json')).read_text(encoding='utf-8'),
                         object_pairs_hook=old.unique_json)
        check(rec['request']['messages'] == messages, 'Token-check prompt mismatch')
        count = rec['tokenizer_prompt_tokens']
        check(type(count) is int and count > 0, 'Recorded token count invalid')
        return count

    def call(self, key, messages, seed, decoder, visible):
        result = super().call(key, messages, seed, decoder, visible)
        check(self.calls[-1]['data_status'] == DATA_STATUS, 'Unexpected data status')
        return result


def _actual_count(client, key, messages):
    if hasattr(client, 'tokenizer'):
        return old.token_count(client.tokenizer, messages)
    check(isinstance(client, Audit), 'Live execution requires actual tokenizer')
    return client.recorded_token_count(key, messages)


def run_item(client, d, view, item, control, directory=None):
    validate_control(view, control, d)
    if directory is not None:
        check(not (Path(directory) / f'result-{item}.json').exists(), 'Result exists; no overwrite')
        check(not (Path(directory) / f'control-check-failure-{item}.json').exists(),
              'Control failure record exists; no retry or overwrite')
    initial = []
    for a in range(3):
        units = old.private_units(view, a)
        initial.append(client.call(f'i{item}-initial-a{a}', prompt(view, units, a),
            old.call_seed(d, view, f'initial-a{a}'), 'json_object', {u['id'] for u in units}))
    checks = {}
    try:
        # Full prompts include the actual S0. Do not retune padding if it fails.
        for a in range(3):
            record = {}
            for arm in ('replay', 'padding'):
                messages = prompt(view, view['units'], a, initial[a], control[arm + '_appendix'])
                record[arm + '_tokens'] = _actual_count(client, f'i{item}-{arm}_a{a}', messages)
                record[arm + '_prompt_sha256'] = digest(messages)
            checks[f'a{a}'] = record
            check(record['replay_tokens'] == record['padding_tokens'], 'Frozen padding no longer token-matched after actual S0; do not retune')
            check(record['replay_tokens'] + d['max_tokens'] <= 4096, 'Actual prior exceeds reserved context; no truncation')
    except Exception as exc:
        if directory is not None:
            failed = Path(directory) / f'control-check-failure-{item}.json'
            check(not failed.exists(), 'Control failure record exists; no overwrite')
            save(failed, dict(item=item, id=view['id'], control_sha256=control['control_sha256'],
                control_checks=checks, error_type=type(exc).__name__, error=str(exc)))
        raise
    order = list(JOBS)
    random.Random(f"{d['protocol']}:{d['seed']}:{view['id']}:order").shuffle(order)
    outputs = {}
    for job in order:
        if job in ('single_union', 'question_only'):
            units = view['units'] if job == 'single_union' else []
            messages = prompt(view, units)
            seed = old.call_seed(d, view, job)
        else:
            arm, agent = job.split('_a')
            a = int(agent)
            units = position_units(view, d) if arm == 'position' else view['units']
            appendix = control[arm + '_appendix'] if arm in ('replay', 'padding') else None
            messages = prompt(view, units, a, initial[a], appendix)
            seed = old.call_seed(d, view, f'paired-a{a}')
        outputs[job] = client.call(f'i{item}-{job}', messages, seed, 'json_object', {u['id'] for u in units})
    result = dict(item=item, id=view['id'], initial=initial, order=order, outputs=outputs,
        control_checks=checks, control_sha256=control['control_sha256'],
        target_ids=control['target_ids'], unique_evidence_sha256=digest(view['units']),
        unique_sentences=len(view['units']), replay_extra_occurrences=6)
    if directory is not None:
        save(Path(directory) / f'result-{item}.json', result)
    return result


def prepare(candidates, d, tokenizer):
    validate_design(d)
    md = candidates['metadata']
    check(md['design_sha256'] == digest(d), 'Candidate design changed')
    check(md['file_sha256'] == DATASET_SHA and md['mirror_revision'] == '1908d6afbbead072334abe2965f91bd2709910ab', 'Dataset provenance changed')
    check(md['natural_candidates_file_sha256'] == NATURAL_CANDIDATES_SHA and
          md['citation_candidates_file_sha256'] == CITATION_CANDIDATES_SHA, 'Exclusion packet changed')
    excluded = md['excluded_ids']
    check(len(excluded) == len(set(excluded)) == 264, 'Exclusion count')
    all_ids = md['all_ordered_ids']
    check(len(all_ids) == len(set(all_ids)) == 7141 and not set(all_ids) & set(excluded), 'Sampling frame changed')
    check([r['id'] for r in seeded_order([dict(id=i) for i in all_ids], d['seed'])] == all_ids, 'Sampling order changed')
    check([v['id'] for v in candidates['views']] == all_ids[:200], 'Candidate order changed')
    script = Path(__file__).resolve().parents[1] / 'scripts/prepare_natural_candidates_v2.py'
    check(hashlib.sha256(script.read_bytes()).hexdigest() == md['preparation_script_sha256'], 'Preparation script changed')
    selected, eligibility, controls = [], [], {}
    for v in candidates['views']:
        check(set(v) == {'id', 'question', 'units'}, 'Labels in input projection')
        docs = {}
        for u in v['units']:
            docs.setdefault(u['title'], []).append(u)
        check(all([u['sentence_index'] for u in us] == list(range(len(us))) for us in docs.values()), 'Source sentence order changed')
        context = [(t, [u['text'] for u in us]) for t, us in docs.items()]
        check(prediction_view(dict(_id=v['id'], question=v['question'], context=context)) == v, 'Source projection changed')
        check(all(len(u['id'].encode()) <= 16 for u in v['units']), 'Source ID exceeds prior bound')
        structural = len(docs) == 10 and all(old.private_units(v, a) for a in range(3))
        rep = replay_appendix(v, d)
        lengths = [old.token_count(tokenizer, prompt(v, old.private_units(v, a), a)) for a in range(3)]
        for a in range(3):
            for units, appendix in ((v['units'], None), (v['units'], rep), (position_units(v, d), None)):
                lengths.append(old.token_count(tokenizer, prompt(v, units, a, appendix=appendix)) + d['prior_allowance_tokens'] + 64)
        lengths.extend(old.token_count(tokenizer, prompt(v, us)) for us in (v['units'], []))
        worst = max(lengths) + d['max_tokens']
        reason = 'structure' if not structural else ('context_limit' if worst > 4096 else None)
        control = prepare_control(v, d, tokenizer) if reason is None else None
        if reason is None and control is None:
            reason = 'padding_token_match_unavailable'
        eligible = reason is None
        take = eligible and len(selected) < d['items']
        eligibility.append(dict(id=v['id'], eligible=eligible, selected=take, reason=reason,
            worst_reserved_tokens=worst, source_sentences=len(v['units'])))
        if take:
            validate_control(v, control, d)
            selected.append(v)
            controls[v['id']] = control
    check(len(selected) == d['items'], 'Too few eligible candidates; no automatic design change')
    return dict(design=d, design_sha256=digest(d), views=selected, controls=controls,
        metadata=md, eligibility=eligibility, candidate_sha256=digest(candidates['views']),
        tokenizer_class=type(tokenizer).__name__, chat_template_sha256=digest(tokenizer.chat_template),
        actual_calls=544, source_sha256=digest(sources()), source_bundle=sources())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native-config', required=True)
    p.add_argument('--server-log', required=True)
    p.add_argument('--design', default='configs/natural_repetition_v2.json')
    p.add_argument('--candidates', required=True)
    p.add_argument('--plan', required=True)
    p.add_argument('--prepare', action='store_true')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--output')
    p.add_argument('--endpoint', default='http://127.0.0.1:8000/v1')
    p.add_argument('--expected-plan-sha256')
    a = p.parse_args()
    check(a.prepare != a.execute, 'Choose prepare or execute')
    cfg = json.loads(Path(a.native_config).read_text())
    EndpointGuard(cfg, a.endpoint, Path('.'))
    check(cfg['packages']['vllm'] == '0.8.3', 'Backend version changed')
    for name, detail in cfg['model_files'].items():
        if name.endswith(('.json', '.txt', '.model')):
            check(hashlib.sha256((Path(cfg['model_path']) / name).read_bytes()).hexdigest() == detail['sha256'], 'Model/tokenizer asset changed')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(cfg['model_path'], local_files_only=True, trust_remote_code=False)
    d = json.loads(Path(a.design).read_text())
    plan = prepare(json.loads(Path(a.candidates).read_text()), d, tokenizer)
    plan.update(model_content_sha256=cfg['model_content_sha256'], decoder_packages=interface.decoder_packages(),
                server_engine=interface.server_engine_evidence(a.server_log))
    path = Path(a.plan)
    if a.prepare:
        check(not path.exists(), 'Plan exists; no overwrite')
        path.parent.mkdir(parents=True, exist_ok=True)
        save(path, plan)
        print(canonical(dict(mode='PREPARED_NO_INFERENCE', items=32, calls=544,
            eligible=sum(r['eligible'] for r in plan['eligibility']), plan_sha256=digest(plan))), flush=True)
        return
    frozen = json.loads(path.read_text())
    check(a.expected_plan_sha256 and digest(frozen) == a.expected_plan_sha256, 'Externally frozen plan hash required')
    check(plan == frozen, 'Plan does not reproduce from frozen inputs, source, model and decoder versions')
    cfg = dict(cfg, endpoint=a.endpoint, **{k: d[k] for k in ('temperature', 'top_p', 'max_tokens')})
    check(cfg['server_settings_reported']['max_model_len'] == 4096, 'Context changed')
    check(a.output, 'Execution output required')
    root = Path(a.output)
    check(not root.exists(), 'Run exists; no overwrite/retry')
    verify(cfg)
    with old.opener().open(a.endpoint.rstrip('/') + '/models', timeout=20) as response:
        metadata = json.load(response)
    check_server(cfg, metadata)
    dr = root / 'shard-000'
    dr.mkdir(parents=True)
    save(dr / 'manifest.json', dict(plan=plan, plan_sha256=digest(plan), config=cfg,
        started_unix=time.time(), planned_calls=544, no_gold_in_runtime=True))
    save(dr / 'server_metadata.json', metadata)
    client = Client(cfg, a.endpoint, dr, tokenizer)
    for i, view in enumerate(plan['views']):
        run_item(client, d, view, i, plan['controls'][view['id']], dr)
        print(f'Completed question {i+1}/32 ({17*(i+1)}/544 calls)', flush=True)
    save(dr / 'completion.json', dict(completed_items=32, calls=544, completed_unix=time.time()))
    print('COMPLETED_ALL_544_CALLS; offline scoring required', flush=True)


if __name__ == '__main__':
    main()
