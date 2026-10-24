"""Prospective 8-question / 64-call interface diagnostic; no output repair.

Original prompts and strict acceptance retain the completed v1 behavior.
Guided decoding is a separate treatment, not a correction to old observations.
"""
import argparse
import hashlib
import inspect
from importlib.metadata import version
import itertools
import json
from pathlib import Path
import random
import time
import urllib.error
import urllib.request

from .dependency_probe import sources, check_server
from .e1 import canonical, digest, save, Client as EndpointGuard
from .format_probe import request as original_request
from .native import verify
from .natural_qa_data import prediction_view, seeded_order
from . import natural_repetition as old
from .probe_score import check

CONTEXTS = ('full', 'question_only')
PROMPTS = ('original', 'explicit')
DECODERS = ('json_object', 'guided_json')
JOBS = {'__'.join(t): t for t in itertools.product(CONTEXTS, PROMPTS, DECODERS)}
BACKEND = 'outlines:no-fallback'
DATASET_SHA = 'c20b638ca82b21d04fe12e14ff417ad05153d4d215a65de54497fca4e972f7c6'
PARENT_CANDIDATES_SHA = '608a5fea0f3363187c7a4b7605c467da57949bac2bb2f3a7eff2f7cc286e9c0f'
EXPLICIT = (
    ' Output syntax clarification: square brackets around source IDs are display '
    'delimiters, not part of an ID. For example, a displayed [d0s0] is cited as '
    '"d0s0", never "[d0s0]". Include both required fields even when you cannot '
    'answer: {"answer":"","citations":[]}. Do not return {}. '
    'These examples illustrate syntax only; cite only IDs actually supplied.'
)


def validate_design(d):
    expected = dict(protocol='citation-interface-v1', seed=31032028,
        candidate_items=64, items=8, actual_calls_per_item=8, temperature=0.,
        top_p=1., max_tokens=256, max_answer_utf8_bytes=160, max_citations=8,
        guided_backend=BACKEND, bootstrap_seed=10103027, bootstrap_resamples=2000)
    check(all(d.get(k) == v for k, v in expected.items()), 'Frozen numeric design changed')
    check(d.get('factors') == dict(context=list(CONTEXTS), prompt=list(PROMPTS),
                                   decoder=list(DECODERS)), 'Factor design changed')


def prompt(view, units, variant='original'):
    check(variant in PROMPTS, 'Unknown prompt variant')
    messages = old.prompt(view, units)
    if variant == 'explicit':
        messages[0]['content'] += EXPLICIT
    return messages


def schema(visible):
    ids = sorted(set(visible))
    check(all(isinstance(x, str) and x for x in ids), 'Invalid visible IDs')
    citations = dict(type='array', items=dict(type='string'), maxItems=8 if ids else 0)
    if ids:
        citations['items']['enum'] = ids
    # JSON Schema maxLength counts characters, not UTF-8 bytes; retain byte check.
    return dict(type='object', properties=dict(answer=dict(type='string'), citations=citations),
                required=['answer', 'citations'], additionalProperties=False)


def request(cfg, messages, seed, decoder, visible):
    check(decoder in DECODERS, 'Unknown decoder')
    payload = original_request(cfg, messages, seed)
    if decoder == 'guided_json':
        payload.pop('response_format')
        payload.update(guided_json=schema(visible), guided_decoding_backend=BACKEND)
    return payload


def evaluate(raw, arm, model, visible):
    visible = set(visible)
    outcome = old.evaluate(raw, arm, model, visible)
    tags = []
    choice = raw['choices'][0]
    if choice['finish_reason'] != 'stop':
        tags.append('non_stop')
    content = choice['message'].get('content')
    if not isinstance(content, str):
        tags.append('non_text')
        return dict(outcome, diagnostics=tags)
    try:
        obj = json.loads(content, object_pairs_hook=old.unique_json)
    except (ValueError, TypeError):
        tags.append('duplicate_or_malformed_json')
        return dict(outcome, diagnostics=tags)
    if obj == {}:
        tags.append('empty_object')
    if not isinstance(obj, dict) or set(obj) != {'answer', 'citations'}:
        tags.append('unexpected_schema')
    if isinstance(obj, dict):
        if not isinstance(obj.get('answer'), str):
            tags.append('answer_type')
        else:
            try:
                if len(obj['answer'].encode('utf-8')) > 160:
                    tags.append('answer_utf8_limit')
            except UnicodeError:
                tags.append('answer_utf8_limit')
        cs = obj.get('citations')
        if not isinstance(cs, list) or not all(isinstance(c, str) for c in cs):
            tags.append('citation_type')
        else:
            if len(cs) > 8:
                tags.append('citation_count')
            invalid = [c for c in cs if c not in visible]
            if invalid:
                tags.append('citation_visibility')
            wrapped = [c for c in invalid if c.startswith('[') and c.endswith(']') and c[1:-1] in visible]
            if wrapped:
                tags.append('bracket_wrapped_id')
            if invalid and len(wrapped) == len(invalid) and set(tags) == {'citation_visibility', 'bracket_wrapped_id'}:
                tags.append('bracket_only_mismatch')
    # Diagnostics never replace the old strict parser's acceptance or assessment.
    return dict(outcome, diagnostics=tags)


class Client(EndpointGuard):
    def __init__(self, cfg, endpoint, directory, tokenizer):
        super().__init__(cfg, endpoint, directory)
        self.tokenizer = tokenizer
        self.opener = old.opener()

    def call(self, key, messages, seed, decoder, visible):
        visible = sorted(set(visible))
        count = old.token_count(self.tokenizer, messages)
        check(count + self.config['max_tokens'] <= 4096, 'Actual context budget exceeded; no truncation')
        payload = request(self.config, messages, seed, decoder, visible)
        path = self.directory / (key+'.json')
        check(not path.exists(), 'Existing call; no overwrite/retry')
        rec = dict(request=payload, request_hash=digest(payload), decoder=decoder,
            visible=visible, status='pending', usage=None, started_unix=time.time(),
            tokenizer_prompt_tokens=count, data_status='REAL_MODEL_CITATION_INTERFACE_DIAGNOSTIC')
        save(path, rec)
        started = time.perf_counter()
        try:
            req = urllib.request.Request(self.endpoint.rstrip('/')+'/chat/completions',
                canonical(payload).encode(), {'Content-Type': 'application/json'})
            with self.opener.open(req, timeout=180) as response:
                rec['raw_text'] = response.read().decode()
            raw = json.loads(rec['raw_text'], object_pairs_hook=old.unique_json)
            rec.update(raw_response=raw, usage=raw.get('usage'))
            result = evaluate(raw, decoder, self.config['model'], visible)
            rec.update(outcome=result, status='ok' if result['format_valid'] else 'format_violation')
            return result
        except Exception as exc:
            rec.update(status='failed', error_type=type(exc).__name__)
            if isinstance(exc, urllib.error.HTTPError):
                rec.update(http_status=exc.code, error_body=exc.read().decode(errors='replace'))
            raise
        finally:
            rec['wall_seconds'] = time.perf_counter()-started
            save(path, rec)


class Audit:
    def __init__(self, directory, cfg):
        self.directory, self.cfg = Path(directory), cfg
        self.calls, self.keys = [], set()

    def call(self, key, messages, seed, decoder, visible):
        check(key not in self.keys, 'Duplicate call')
        self.keys.add(key)
        rec = json.loads((self.directory/(key+'.json')).read_text(encoding='utf-8'),
                         object_pairs_hook=old.unique_json)
        expected = request(self.cfg, messages, seed, decoder, visible)
        check(rec['request'] == expected and rec['request_hash'] == digest(expected), 'Request mismatch')
        check(rec['decoder'] == decoder and rec['visible'] == sorted(set(visible)), 'Decoder/visibility mismatch')
        check(json.loads(rec['raw_text'], object_pairs_hook=old.unique_json) == rec['raw_response'], 'Raw mismatch')
        check(rec['usage'] == rec['raw_response'].get('usage'), 'Usage mismatch')
        result = evaluate(rec['raw_response'], decoder, self.cfg['model'], visible)
        check(rec['outcome'] == result, 'Outcome mismatch')
        check(rec['status'] == ('ok' if result['format_valid'] else 'format_violation'), 'Unresolved/status mismatch')
        self.calls.append(rec)
        return result


def run_item(client, d, view, item, directory=None):
    order = list(JOBS)
    random.Random(f"{d['protocol']}:{d['seed']}:{view['id']}:order").shuffle(order)
    seed = int(digest([d['protocol'], d['seed'], view['id'], 'paired'])[:7], 16)
    outputs = {}
    for job in order:
        context, variant, decoder = JOBS[job]
        units = view['units'] if context == 'full' else []
        outputs[job] = client.call(f'i{item}-{job}', prompt(view, units, variant),
                                  seed, decoder, {u['id'] for u in units})
    result = dict(item=item, id=view['id'], order=order, outputs=outputs)
    if directory is not None:
        save(Path(directory)/f'result-{item}.json', result)
    return result


def prepare(candidates, d, tokenizer):
    validate_design(d)
    md = candidates['metadata']
    check(md['design_sha256'] == digest(d), 'Candidate design changed')
    check(md['file_sha256'] == DATASET_SHA, 'Dataset checksum changed')
    check(md['parent_candidates_file_sha256'] == PARENT_CANDIDATES_SHA, 'Exclusion source changed')
    excluded = md['excluded_ids']
    check(len(excluded) == len(set(excluded)) == 200, 'Exclusion count')
    all_ids = md['all_ordered_ids']
    check(len(all_ids) == len(set(all_ids)) == 7205 and not set(all_ids) & set(excluded), 'Sampling frame changed')
    check([r['id'] for r in seeded_order([dict(id=i) for i in all_ids], d['seed'])] == all_ids, 'Sampling order changed')
    check([v['id'] for v in candidates['views']] == all_ids[:64], 'Candidate order changed')
    script = Path(__file__).resolve().parents[1]/'scripts/prepare_citation_candidates.py'
    check(hashlib.sha256(script.read_bytes()).hexdigest() == md['preparation_script_sha256'], 'Preparation script changed')
    selected, eligibility = [], []
    for v in candidates['views']:
        check(set(v) == {'id', 'question', 'units'}, 'Labels in projected input')
        docs = {}
        for u in v['units']:
            docs.setdefault(u['title'], []).append(u)
        check(all([u['sentence_index'] for u in us] == list(range(len(us))) for us in docs.values()), 'Source sentence order changed')
        context = [(t, [u['text'] for u in us]) for t, us in docs.items()]
        check(prediction_view(dict(_id=v['id'], question=v['question'], context=context)) == v, 'Source projection changed')
        lengths = [old.token_count(tokenizer, prompt(v, us, p))
                   for us in (v['units'], []) for p in PROMPTS]
        worst = max(lengths) + d['max_tokens']
        eligible = len(docs) == 10 and worst <= 4096
        take = eligible and len(selected) < 8
        eligibility.append(dict(id=v['id'], eligible=eligible, selected=take,
            reason=None if eligible else ('structure' if len(docs) != 10 else 'context_limit'), worst_reserved_tokens=worst))
        if take:
            selected.append(v)
    check(len(selected) == 8, 'Too few eligible questions; no automatic design change')
    return dict(design=d, design_sha256=digest(d), views=selected, metadata=md,
        eligibility=eligibility, candidate_sha256=digest(candidates['views']),
        tokenizer_class=type(tokenizer).__name__, chat_template_sha256=digest(tokenizer.chat_template),
        actual_calls=64, source_sha256=digest(sources()), source_bundle=sources())


def decoder_packages():
    return {p: version(p) for p in ('vllm', 'xgrammar', 'outlines', 'outlines_core', 'transformers')}


def grammar_preflight(plan, tokenizer):
    """CPU compilation only, through the installed vLLM path; no generation."""
    from vllm.sampling_params import GuidedDecodingParams
    from vllm.model_executor.guided_decoding import get_local_guided_decoding_logits_processor
    compiled = {}
    for visible in [set()] + [{u['id'] for u in v['units']} for v in plan['views']]:
        s = schema(visible)
        key = digest(s)
        if key in compiled:
            continue
        params = GuidedDecodingParams(json=s, backend=BACKEND)
        processor = get_local_guided_decoding_logits_processor(params, tokenizer, None)
        check(params.backend == BACKEND and processor is not None, 'Unexpected backend fallback')
        compiled[key] = type(processor).__module__+'.'+type(processor).__name__
    from vllm.model_executor.guided_decoding import utils, outlines_decoding
    engine_sources = {mod.__name__: hashlib.sha256(Path(inspect.getfile(mod)).read_bytes()).hexdigest()
                      for mod in (utils, outlines_decoding)}
    return dict(kind='CPU_GRAMMAR_COMPILATION_NO_INFERENCE', backend=BACKEND,
                schemas=compiled, packages=decoder_packages(), engine_sources=engine_sources)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native-config', required=True)
    p.add_argument('--design', default='configs/citation_interface_v1.json')
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
    check(cfg['packages']['vllm'] == '0.8.3', 'Backend version requires new protocol')
    for name, detail in cfg['model_files'].items():
        if name.endswith(('.json', '.txt', '.model')):
            check(hashlib.sha256((Path(cfg['model_path'])/name).read_bytes()).hexdigest() == detail['sha256'], 'Model/tokenizer asset changed')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(cfg['model_path'], local_files_only=True, trust_remote_code=False)
    d = json.loads(Path(a.design).read_text())
    plan = prepare(json.loads(Path(a.candidates).read_text()), d, tokenizer)
    plan.update(model_content_sha256=cfg['model_content_sha256'], decoder_packages=decoder_packages())
    path = Path(a.plan)
    if a.prepare:
        check(not path.exists(), 'Plan exists; no overwrite')
        plan['grammar_preflight'] = grammar_preflight(plan, tokenizer)
        path.parent.mkdir(parents=True, exist_ok=True)
        save(path, plan)
        print(canonical(dict(mode='PREPARED_NO_INFERENCE', items=8, calls=64, plan_sha256=digest(plan))), flush=True)
        return
    frozen = json.loads(path.read_text())
    check(a.expected_plan_sha256 and digest(frozen) == a.expected_plan_sha256, 'Externally frozen plan hash required')
    check(frozen['grammar_preflight'] == grammar_preflight(plan, tokenizer), 'Grammar/backend preflight changed')
    plan['grammar_preflight'] = frozen['grammar_preflight']
    check(plan == frozen, 'Plan does not reproduce from frozen inputs, source, model and decoder versions')
    cfg = dict(cfg, endpoint=a.endpoint, **{k: d[k] for k in ('temperature', 'top_p', 'max_tokens')})
    check(cfg['server_settings_reported']['max_model_len'] == 4096, 'Context changed')
    root = Path(a.output)
    check(not root.exists(), 'Run exists; no overwrite/retry')
    verify(cfg)
    with old.opener().open(a.endpoint.rstrip('/')+'/models', timeout=20) as response:
        metadata = json.load(response)
    check_server(cfg, metadata)
    dr = root/'shard-000'
    dr.mkdir(parents=True)
    save(dr/'manifest.json', dict(plan=plan, plan_sha256=digest(plan), config=cfg,
        started_unix=time.time(), planned_calls=64, no_gold_in_runtime=True))
    save(dr/'server_metadata.json', metadata)
    client = Client(cfg, a.endpoint, dr, tokenizer)
    for i, view in enumerate(plan['views']):
        run_item(client, d, view, i, dr)
        print(f'Completed question {i+1}/8 ({8*(i+1)}/64 calls)', flush=True)
    save(dr/'completion.json', dict(completed_items=8, calls=64, completed_unix=time.time()))
    print('COMPLETED_ALL_64_CALLS; offline scoring required', flush=True)


if __name__ == '__main__':
    main()
