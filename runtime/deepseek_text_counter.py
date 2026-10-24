"""Pinned reference counter for plain non-thinking JSON chat; no network.

Source: deepseek-recipe 935817c66f5d6e64838f419692545f06787e485f,
deepseek-recipe-encoding/src/v4/dsv41.rs. This is a restricted implementation,
not proof that the remote service uses these weights/template. Literal added-
token spellings are refused, never reinterpreted as role/control instructions.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

TOKENIZER_SHA = 'c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b'
JSON_INSTRUCTION = ('\n\n## Response Format:\n\n'
    'You MUST strictly adhere to the following schema to reply:\n'
    '{"type": "json_object"}')
BOS, EOS = '<｜begin▁of▁sentence｜>', '<｜end▁of▁sentence｜>'
SYSTEM, USER, ASSISTANT = '<｜System｜>', '<｜User｜>', '<｜Assistant｜>'
END_THINK = '</think>'


def render(messages, reserved=()):
    if (type(messages) is not list or len(messages) < 2
            or messages[0].get('role') != 'system'
            or messages[-1].get('role') != 'user'):
        raise ValueError('Expected system-first user-last ordinary chat')
    normalized = []
    for i, m in enumerate(messages):
        if (type(m) is not dict or set(m) != {'role', 'content'}
                or m['role'] not in ('system', 'user', 'assistant')
                or type(m['content']) is not str
                or (i > 0 and m['role'] == 'system')):
            raise ValueError('Unsupported message shape')
        if any(s and s in m['content'] for s in reserved):
            raise ValueError('Literal control spelling unsupported; preserve failure')
        if m['role'] == 'user' and normalized and normalized[-1]['role'] == 'user':
            normalized[-1]['content'] += '\n\n' + m['content']
        else:
            normalized.append(deepcopy(m))
    result = BOS + SYSTEM + normalized[0]['content'] + JSON_INSTRUCTION
    for m in normalized[1:]:
        if m['role'] == 'user':
            result += USER + m['content']
        else:
            result += ASSISTANT + END_THINK + m['content'] + EOS
    return result + ASSISTANT + END_THINK


class ReferenceCounter:
    def __init__(self, tokenizer_path):
        raw = Path(tokenizer_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != TOKENIZER_SHA:
            raise ValueError('Tokenizer fingerprint mismatch')
        import tokenizers
        if tokenizers.__version__ != '0.22.1':
            raise ValueError('Pinned tokenizers 0.22.1 required')
        self.tokenizer = tokenizers.Tokenizer.from_str(raw.decode('utf-8'))
        self.reserved = tuple(t['content'] for t in json.loads(raw)['added_tokens'])

    def text(self, value):
        if type(value) is not str or any(s and s in value for s in self.reserved):
            raise ValueError('Unsupported raw text')
        return len(self.tokenizer.encode(value, add_special_tokens=False).ids)

    def chat(self, messages):
        value = render(messages, self.reserved)
        return len(self.tokenizer.encode(value, add_special_tokens=False).ids)
