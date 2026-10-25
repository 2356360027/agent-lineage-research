"""Restricted first-request reference counter, not a provider spending cap.

Derived from DeepSeek's pinned MIT-licensed recipe. Source text and LICENSE
must remain together in the reference directory; no downloaded code is run.
Only the fixed native-action profile is supported. No trajectory accounting.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from runtime.deepseek_text_counter import ReferenceCounter
from runtime.native_action_interface import INSTRUCTION, build_candidate, tool_definitions
from runtime.cache_intent_staged_loop import ACTION_INSTRUCTION

REVISION = '935817c66f5d6e64838f419692545f06787e485f'
SOURCE_HASHES = {
    'LICENSE': 'ebb4f09972aee8608be255debaf78451a68e95c290f55c240dec2ecfa16ea6be',
    'Cargo.toml': '790d9820e34cfb0555aba0d64e753591cffb786ee66c73b6c7d2925972ba0510',
    'deepseek-recipe-encoding/src/v4/dsv41.rs': '33f7b0e0c3dde66020a4746ffc6c35df8dd30933e653aaf9071c1beb21439a90',
    'deepseek-recipe-encoding/src/v4/mod.rs': 'b48c8b1b725eac84c8d9bf1aadb715c36922e027516974d5b33a315da9fcdd63',
    'deepseek-recipe-core/src/json.rs': '5f870d05a986009f57fd2452f0eb7800476ca5be5a3da722263ec696a743211c',
    'deepseek-recipe-core/src/special_tokens.rs': 'e38f095ca3841855234a009ad913c250befb179240d32e5cf22fc1ef140569b3',
    'deepseek-recipe-core/src/tools.rs': '5bd7bad3fc17ac50cdbee85d16c80ae7b9a46a3478d329cdc99592b572720b29',
}


def reference_preamble(directory):
    sources = {}
    for name, expected in SOURCE_HASHES.items():
        raw = (Path(directory) / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Reference source fingerprint mismatch: ' + name)
        sources[name] = raw.decode('utf-8')
    source = sources['deepseek-recipe-encoding/src/v4/mod.rs']
    start = source.index('r#"## Tools') + 3
    end = source.index('"#', start)
    template = source[start:end]
    for name, value in {'dsml': '｜DSML｜', 'tool_calls_block_name': ' calls',
                        'tool_call_tag_name': ' invoke', 'tool_parameter_tag_name': ' parameter',
                        'thinking_start': '<think>', 'thinking_end': '</think>'}.items():
        template = template.replace('{' + name + '}', value)
    if '{' in template or '}' in template:
        raise ValueError('Unexpected source template placeholder')
    return template


def validate_candidate_payload(payload):
    expected = {'model', 'messages', 'thinking', 'temperature', 'max_tokens', 'stream',
                'tools', 'tool_choice'}
    if (type(payload) is not dict or set(payload) != expected
            or payload['tools'] != tool_definitions() or payload['tool_choice'] != 'required'):
        raise ValueError('Unsupported native payload')
    messages = payload['messages']
    if (type(messages) is not list or len(messages) != 2
            or any(type(m) is not dict or set(m) != {'role', 'content'}
                   or type(m['content']) is not str for m in messages)
            or messages[0]['content'].count(INSTRUCTION) != 1
            or INSTRUCTION in messages[1]['content']
            or any(ACTION_INSTRUCTION in m['content'] for m in messages)):
        raise ValueError('Only canonical first native requests supported')
    original = deepcopy(payload)
    del original['tools'], original['tool_choice']
    original['response_format'] = {'type': 'json_object'}
    original['messages'][0]['content'] = messages[0]['content'].replace(INSTRUCTION, ACTION_INSTRUCTION)
    if build_candidate(original)['payload'] != payload:
        raise ValueError('Native profile round-trip failed')


def render_first_request(payload, preamble, reserved):
    validate_candidate_payload(payload)
    # All untrusted text must be ordinary text; inserted reference controls only.
    for text in [m['content'] for m in payload['messages']] + [json.dumps(payload['tools'], ensure_ascii=False)]:
        if any(spelling and spelling in text for spelling in reserved):
            raise ValueError('Literal reserved spelling needs segment-aware encoding')
    # mod.rs tool_definition_json omits strict; Cargo enables preserve_order.
    schemas = []
    for tool in payload['tools']:
        function = tool['function']
        schemas.append(json.dumps({k: function[k] for k in ('name', 'description', 'parameters')},
                                  ensure_ascii=False, allow_nan=False))
    tools = (preamble + '\n'.join(schemas)
             + '\n\nYou MUST strictly follow the above defined tool name and parameter schemas to invoke tool calls.\n')
    system, user = (m['content'] for m in payload['messages'])
    return ('<｜begin▁of▁sentence｜><｜System｜>' + system + '\n\n' + tools
            + '<｜User｜>' + user + '<｜Assistant｜></think>\n\n<｜DSML｜ calls>\n')


class NativeFirstCounter:
    def __init__(self, source_directory, tokenizer_path):
        self.preamble = reference_preamble(source_directory)
        self.reference = ReferenceCounter(tokenizer_path)

    def inspect(self, payload):
        prompt = render_first_request(payload, self.preamble, self.reference.reserved)
        count = len(self.reference.tokenizer.encode(prompt, add_special_tokens=False).ids)
        return {'reference_input_tokens': count,
                'rendered_prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                'recipe_revision': REVISION, 'max_requested_output_tokens': payload['max_tokens'],
                'provider_prompt_parity_verified': False,
                'beta_strict_server_overhead_verified': False,
                'executable_upstream_parity_tested': False,
                'provider_cost_cap': False, 'inference_authorized': False}
