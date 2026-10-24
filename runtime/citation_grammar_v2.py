"""Bounded citation EBNF plus recorded V1 CPU admission/conformance checks.

No model generation. This constrains serialization and visible-ID membership,
not factual support or the separately enforced 160-byte answer limit.
"""
import hashlib
import inspect
from importlib.metadata import version
import json
from pathlib import Path
import re
from types import SimpleNamespace

from .e1 import digest
from .probe_score import check

BACKEND = 'xgrammar'
KIND = 'CPU_V1_ADMISSION_AND_TOKEN_CONFORMANCE_NO_INFERENCE'
PACKAGE_NAMES = ('vllm', 'xgrammar', 'outlines', 'outlines_core', 'transformers')


def visible_ids(visible):
    ids = sorted(set(visible))
    check(all(isinstance(x, str) and re.fullmatch(r'd\d+s\d+', x) for x in ids),
          'Unexpected citation identifier syntax')
    return ids


def grammar(visible):
    ids = visible_ids(visible)
    rules = [
        r'root ::= ws "{" ws (answer ws "," ws citations | citations ws "," ws answer) ws "}" ws',
        r'answer ::= "\"answer\"" ws ":" ws string',
        r'citations ::= "\"citations\"" ws ":" ws array',
        r'string ::= "\"" char* "\""',
        r'char ::= [^"\\\x00-\x1F] | "\\" escape',
        r'escape ::= ["\\/bfnrt] | "u" [0-9A-Fa-f] [0-9A-Fa-f] [0-9A-Fa-f] [0-9A-Fa-f]',
        r'ws ::= [ \t\n\r]*',
    ]
    if ids:
        # At most eight entries without relying on unsupported JSON maxItems.
        tail = '(ws "," ws id)?'
        rules.append('array ::= "[" ws (id '+ ' '.join([tail] * 7) + ')? ws "]"')
        rules.append('id ::= ' + ' | '.join(json.dumps(json.dumps(x)) for x in ids))
    else:
        rules.append('array ::= "[" ws "]"')
    return '\n'.join(rules) + '\n'


def fixtures(visible):
    """Synthetic protocol fixtures, never questions, gold answers or model data."""
    ids = visible_ids(visible)
    def encoded(answer='', citations=None, **kw):
        return json.dumps(dict(answer=answer, citations=citations or []), ensure_ascii=False, **kw)
    cases = [
        ('empty_pair', encoded(), True),
        ('reverse_keys', '{"citations":[],"answer":"x"}', True),
        ('json_whitespace', ' \n{ "answer"\t:\r"x", "citations" : [ ] }\n', True),
        ('unicode_answer', encoded('é研究'), True),
        ('escaped_answer', r'{"answer":"quote: \" slash: \\ newline: \n unicode: \u00e9","citations":[]}', True),
        # Grammar intentionally permits this; strict experiment parser rejects it.
        ('answer_over_byte_limit', encoded('x' * 161), True),
        ('empty_object', '{}', False),
        ('missing_answer', '{"citations":[]}', False),
        ('missing_citations', '{"answer":"x"}', False),
        ('extra_field', '{"answer":"x","citations":[],"extra":1}', False),
        ('duplicate_key', '{"answer":"x","answer":"y","citations":[]}', False),
        ('numeric_answer', '{"answer":4,"citations":[]}', False),
        ('string_citations', '{"answer":"x","citations":"d0s0"}', False),
        ('unknown_id', encoded('x', ['not-visible']), False),
        ('trailing_content', encoded() + ' x', False),
        ('trailing_comma', '{"answer":"x","citations":[],}', False),
        ('incomplete', '{"answer":"x","citations":', False),
        ('invalid_escape', r'{"answer":"\q","citations":[]}', False),
    ]
    if ids:
        cases += [(f'visible_{i}', encoded('x', [item]), True) for i, item in enumerate(ids)]
        cases += [('eight_repeated', encoded('x', [ids[0]] * 8), True),
                  ('nine_repeated', encoded('x', [ids[0]] * 9), False),
                  ('bracket_wrapped', encoded('x', ['[' + ids[0] + ']']), False)]
    else:
        cases.append(('empty_context_nonempty', encoded('x', ['d0s0']), False))
    return [dict(name=n, text=t, expected=e) for n, t, e in cases]


def fixture_match(ctx, tokenizer, text):
    import xgrammar as xgr
    tokens = tokenizer.encode(text, add_special_tokens=False) + [tokenizer.eos_token_id]
    matcher = xgr.GrammarMatcher(ctx)
    first_rejected = None
    for index, token in enumerate(tokens):
        if not matcher.accept_token(token):
            first_rejected = index
            break
    accepted = first_rejected is None and matcher.is_terminated()
    return dict(token_ids=tokens, first_rejected=first_rejected,
                terminated=matcher.is_terminated(), accepted=accepted)


def admission_payload(specification):
    return dict(model='Qwen/Qwen2.5-7B-Instruct',
        messages=[dict(role='user', content='CPU protocol fixture; no inference')],
        max_tokens=256, temperature=0., top_p=1., seed=1,
        guided_grammar=specification, guided_decoding_backend=BACKEND)


def grammar_preflight(plan, tokenizer):
    """Real installed V1 request validator + V1 compiler, without LLM calls.

    Processor/backend holders avoid engine/GPU initialization while invoking the
    installed production methods. Actual running-engine identity is checked and
    recorded separately by the runner against its startup log.
    """
    import xgrammar as xgr
    import vllm.entrypoints.openai.protocol as protocol
    import vllm.v1.engine.processor as processor
    import vllm.v1.structured_output.backend_xgrammar as backend_module
    import vllm.v1.structured_output.utils as engine_utils
    from vllm.v1.structured_output.backend_types import StructuredOutputOptions
    packages = {p: version(p) for p in PACKAGE_NAMES}
    check(packages['vllm'] == '0.8.3' and packages['xgrammar'] == '0.1.17',
          'Version change requires a new preflight protocol')
    model_config = json.loads((Path(tokenizer.name_or_path) / 'config.json').read_text())
    vocab_size = model_config['vocab_size']
    info = xgr.TokenizerInfo.from_huggingface(tokenizer, vocab_size=vocab_size)
    compiler = xgr.GrammarCompiler(info, max_threads=8)
    engine = processor.Processor.__new__(processor.Processor)
    engine.decoding_config = SimpleNamespace(guided_decoding_backend=BACKEND)
    backend = backend_module.XgrammarBackend.__new__(backend_module.XgrammarBackend)
    backend.compiler, backend.vocab_size = compiler, vocab_size
    backend.disable_any_whitespace = False
    reports = {}
    visible_sets = [set()] + [{u['id'] for u in v['units']} for v in plan['views']]
    for visible in visible_sets:
        specification = grammar(visible)
        key = digest(specification)
        if key in reports:
            continue
        payload = admission_payload(specification)
        parsed = protocol.ChatCompletionRequest(**payload)
        params = parsed.to_sampling_params(256, None)
        engine._validate_structured_output(params)
        check(params.guided_decoding.backend == BACKEND and
              params.guided_decoding.grammar == specification, 'V1 admission changed grammar/backend')
        compiled = backend.compile_grammar(StructuredOutputOptions.GRAMMAR, specification)
        tests = [dict(case, **fixture_match(compiled.ctx, tokenizer, case['text']))
                 for case in fixtures(visible)]
        reports[key] = dict(visible=visible_ids(visible), grammar=specification,
            admitted=True, backend_after=BACKEND, admission_request=payload,
            admission_request_sha256=digest(payload), fixtures=tests,
            all_passed=all(t['accepted'] == t['expected'] for t in tests))
    # Generic baseline must also pass this actual V1 path without backend override.
    baseline = protocol.ChatCompletionRequest(model='Qwen/Qwen2.5-7B-Instruct',
        messages=[dict(role='user', content='CPU protocol fixture; no inference')],
        response_format={'type': 'json_object'}, temperature=0., top_p=1., max_tokens=256)
    baseline_params = baseline.to_sampling_params(256, None)
    engine._validate_structured_output(baseline_params)
    check(baseline_params.guided_decoding.backend == BACKEND, 'Baseline V1 backend changed')
    engine_sources = {mod.__name__: hashlib.sha256(Path(inspect.getfile(mod)).read_bytes()).hexdigest()
                      for mod in (protocol, processor, backend_module, engine_utils)}
    report = dict(kind=KIND, backend=BACKEND, packages=packages, grammars=reports,
        engine_sources=engine_sources, tokenizer_vocab_size=vocab_size,
        tokenizer_eos_id=tokenizer.eos_token_id,
        baseline_admitted=True, baseline_backend_after=BACKEND,
        limitations=['CPU production-method checks do not launch the full server request path.',
            'Canonical JSON key/ID spellings only; two key orders and JSON whitespace allowed.',
            'No factual/evidence-support guarantee; answer UTF-8 byte limit remains post-parse.',
            'Finite positive/negative fixtures are conformance evidence, not a proof for all token paths.'])
    validate_preflight(report, visible_sets, packages)
    return report


def validate_preflight(report, visible_sets, packages):
    """Portable deterministic audit; does not falsely claim a local compiler rerun."""
    check(report['kind'] == KIND and report['backend'] == BACKEND and
          report['packages'] == packages, 'Preflight kind/backend/packages changed')
    check(report['baseline_admitted'] is True and report['baseline_backend_after'] == BACKEND,
          'Generic baseline was not V1 admitted')
    vocab, eos = report['tokenizer_vocab_size'], report['tokenizer_eos_id']
    check(type(vocab) is int and vocab > 0 and type(eos) is int and 0 <= eos < vocab,
          'Invalid tokenizer vocabulary/EOS record')
    required_sources = {'vllm.entrypoints.openai.protocol', 'vllm.v1.engine.processor',
        'vllm.v1.structured_output.backend_xgrammar', 'vllm.v1.structured_output.utils'}
    check(set(report['engine_sources']) == required_sources and all(
        re.fullmatch('[0-9a-f]{64}', x) for x in report['engine_sources'].values()),
        'Missing actual V1 source hashes')
    expected = {digest(grammar(ids)): visible_ids(ids) for ids in visible_sets}
    check(set(report['grammars']) == set(expected), 'Missing/extra compiled grammar')
    for key, ids in expected.items():
        entry = report['grammars'][key]
        check(entry['visible'] == ids and entry['grammar'] == grammar(ids) and
              entry['admitted'] is True and entry['backend_after'] == BACKEND,
              'Compiled/admitted grammar changed')
        req = entry['admission_request']
        check(req == admission_payload(entry['grammar']) and
              digest(req) == digest(admission_payload(entry['grammar']))
              and digest(req) == entry['admission_request_sha256'], 'CPU admission payload mismatch')
        cases = fixtures(ids)
        check(len(entry['fixtures']) == len(cases), 'Missing conformance fixture')
        for observed, case in zip(entry['fixtures'], cases, strict=True):
            check(type(observed.get('expected')) is bool and
                  all(observed.get(k) == v for k, v in case.items()), 'Conformance fixture changed')
            check(type(observed['accepted']) is bool and observed['accepted'] == case['expected'],
                  'Grammar conformance failed: ' + case['name'])
            tokens = observed['token_ids']
            rejected = observed['first_rejected']
            check(isinstance(tokens, list) and tokens and tokens[-1] == eos and
                  all(type(t) is int and 0 <= t < vocab for t in tokens),
                  'Missing CPU fixture tokens')
            check(rejected is None or type(rejected) is int and 0 <= rejected < len(tokens),
                  'Invalid rejection token index')
            check(type(observed['terminated']) is bool and
                  observed['accepted'] == (rejected is None and observed['terminated'] is True),
                  'Inconsistent fixture acceptance')
        check(entry['all_passed'] is True, 'Failed fixture suite')
