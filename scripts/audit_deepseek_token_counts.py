"""Offline audit of two existing probes and proposed INITIAL prompts only.

This is not a full trajectory counter or a provider-capacity certificate. No API
calls, downloaded Python execution, credentials, output repair, or extrapolated
constant offsets. The supported shape is deliberately system + user, plain text.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

TOKENIZER_SHA = 'c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b'
PROBE_MANIFEST_SHA = '08c4feb60c7e1ecbc660f6c722f302fb778799b3451aafa711c04eba3c08508f'
SOURCE_URL = ('https://raw.githubusercontent.com/deepseek-ai/deepseek-recipe/'
              '935817c66f5d6e64838f419692545f06787e485f/'
              'deepseek-recipe-encoding/src/v4/dsv41.rs')
SOURCE_SHA = '33f7b0e0c3dde66020a4746ffc6c35df8dd30933e653aaf9071c1beb21439a90'
FORMAT_INSTRUCTION = ('\n\n## Response Format:\n\n'
    'You MUST strictly adhere to the following schema to reply:\n'
    '{"type": "json_object"}')
VARIANTS = ('v4_without_json_format', 'v4_with_json_format', 'v41_with_json_format')


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_probe(directory):
    directory = Path(directory)
    manifest = directory / 'artifact_hashes.json'
    if digest_file(manifest) != PROBE_MANIFEST_SHA:
        raise ValueError('Unexpected probe manifest; no substitution permitted')
    hashes = json.loads(manifest.read_text(encoding='utf-8'))
    for name, expected in hashes.items():
        if Path(name).name != name or digest_file(directory / name) != expected:
            raise ValueError('Probe artifact verification failed')
    return len(hashes)


def initial_prompt(payload, variant, reserved_strings):
    """Source-derived restricted renderer; never insert an empirical +23 offset."""
    if variant not in VARIANTS:
        raise ValueError('Unknown reference variant')
    allowed = {'model', 'messages', 'thinking', 'temperature', 'max_tokens',
               'stream', 'response_format'}
    if (type(payload) is not dict or set(payload) != allowed
            or payload['model'] != 'deepseek-flash'
            or payload['thinking'] != {'type': 'disabled'}
            or payload['response_format'] != {'type': 'json_object'}
            or payload['stream'] is not False):
        raise ValueError('Unsupported request shape')
    messages = payload['messages']
    if type(messages) is not list or len(messages) != 2:
        raise ValueError('Only initial system/user requests are audited')
    for message, role in zip(messages, ('system', 'user')):
        if (type(message) is not dict or set(message) != {'role', 'content'}
                or message['role'] != role or type(message['content']) is not str):
            raise ValueError('Unsupported message')
        if any(s and s in message['content'] for s in reserved_strings):
            # A rendered-string tokenizer cannot distinguish literal control-token
            # spellings from inserted controls. Reject, do not silently reinterpret.
            raise ValueError('Literal reserved spelling needs segment-aware encoding')
    system, user = (m['content'] for m in messages)
    if variant != 'v4_without_json_format':
        system += FORMAT_INSTRUCTION
    if variant == 'v41_with_json_format':
        system = '<｜System｜>' + system
    return ('<｜begin▁of▁sentence｜>' + system + '<｜User｜>' + user
            + '<｜Assistant｜></think>')


def audit(probe, reference, dependencies, proposals, output):
    count = verify_probe(probe)
    reference = Path(reference)
    if digest_file(reference / 'tokenizer.json') != TOKENIZER_SHA:
        raise ValueError('Unrecognized tokenizer content')
    sys.path.insert(0, str(Path(dependencies).resolve()))
    import tokenizers
    if tokenizers.__version__ != '0.22.1':
        raise ValueError('Expected tokenizers 0.22.1')
    tokenizer = tokenizers.Tokenizer.from_file(str(reference / 'tokenizer.json'))
    metadata = json.loads((reference / 'tokenizer.json').read_text(encoding='utf-8'))
    reserved = tuple(t['content'] for t in metadata['added_tokens'])
    def tokens(text):
        return len(tokenizer.encode(text, add_special_tokens=False).ids)
    plan = json.loads((Path(probe) / 'plan.json').read_text(encoding='utf-8'))
    comparisons = []
    for index, payload in enumerate(plan['request_payloads'], 1):
        saved = json.loads((Path(probe) / f'call-{index}.response.json').read_text(encoding='utf-8'))
        response = json.loads(saved['response_text'])
        usage = response['usage']
        content = response['choices'][0]['message']['content']
        if any(s and s in content for s in reserved):
            raise ValueError('Completion contains a reserved spelling')
        local = {v: tokens(initial_prompt(payload, v, reserved)) for v in VARIANTS}
        comparisons.append({'call': index, 'reference_prompt_tokens': local,
            'reported_prompt_tokens': usage['prompt_tokens'],
            'content_retokenized': tokens(content),
            'reported_completion_tokens': usage['completion_tokens'],
            'v41_prompt_matches': local['v41_with_json_format'] == usage['prompt_tokens'],
            'content_count_matches': tokens(content) == usage['completion_tokens']})
    rows = json.loads(Path(proposals).read_text(encoding='utf-8'))
    initial_counts = [tokens(initial_prompt(r['proposed_payload'],
                      'v41_with_json_format', reserved)) for r in rows]
    result = {'status': 'OFFLINE_INITIAL_PROMPT_REFERENCE_AUDIT',
        'probe_artifacts_verified': count, 'probe_manifest_sha256': PROBE_MANIFEST_SHA,
        'tokenizer_sha256': TOKENIZER_SHA, 'tokenizers_version': tokenizers.__version__,
        'recipe_reference_url': SOURCE_URL, 'recipe_reference_sha256': SOURCE_SHA,
        'comparisons': comparisons, 'proposed_initial_prompt_count': len(initial_counts),
        'proposed_initial_prompt_tokens': initial_counts,
        'private_proposals_sha256': digest_file(proposals),
        'new_api_calls': 0, 'research_samples': 0, 'live_batch_ready': False,
        'limitations': ['Two short observed requests only; no long-history parity proof',
            'Reference tokenizer is not verified identity of served model weights',
            'Initial system/user plain-text shape only; not the full trajectory adapter',
            'Literal reserved-token spellings rejected, not silently reinterpreted',
            'Content retokenization is not generally equal to generated token usage',
            'No new paid experiment authorization or provider spending cap']}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'audit.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    with (output / 'audit_source.py').open('xb') as stream:
        stream.write(Path(__file__).read_bytes())
    hashes = {p.name: digest_file(p) for p in output.iterdir() if p.is_file()}
    with (output / 'artifact_hashes.json').open('x', encoding='utf-8') as stream:
        json.dump(hashes, stream, indent=2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('probe', 'reference', 'dependencies', 'proposals', 'output'):
        parser.add_argument('--' + argument, required=True)
    print(json.dumps(audit(**vars(parser.parse_args())), indent=2))
