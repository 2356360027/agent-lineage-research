"""Pinned-source restricted native rendering; NOT proven provider accounting.

Extends the already pinned dsv41 single-string-argument recipe to definition,
revision and terminal schemas. No downloaded code execution or remote calls.
Every live response must still match the reference count before continuation.
"""
import hashlib
import json

from runtime.native_action_counter import NativeFirstCounter, REVISION
from runtime.native_action_interface import _load
from runtime.deepseek_text_counter import BOS, SYSTEM, USER, ASSISTANT, END_THINK, EOS
from runtime.bird_readonly_audit import digest
from runtime.student_club_native_wire import payload

VERSION = 'student-club-native-reference-counter-v1'
PREFIX = ASSISTANT + END_THINK + '\n\n<｜DSML｜ calls>\n'


def render(request, preamble, reserved):
    wire = payload(request)
    def ordinary(text):
        if any(s and s in text for s in reserved):
            raise ValueError('Literal reserved control spelling; no token-offset repair')
        return text
    schemas = []
    for tool in wire['tools']:
        f = tool['function']
        schemas.append(ordinary(json.dumps({k:f[k] for k in ('name','description','parameters')},
            ensure_ascii=False,allow_nan=False)))
    system, user = wire['messages'][:2]
    tools = preamble + '\n'.join(schemas) + '\n\nYou MUST strictly follow the above defined tool name and parameter schemas to invoke tool calls.\n'
    result = BOS + SYSTEM + ordinary(system['content']) + '\n\n' + tools + USER + ordinary(user['content'])
    for message in wire['messages'][2:]:
        if message['role'] == 'assistant':
            f = message['tool_calls'][0]['function']
            key, value = next(iter(_load(f['arguments']).items()))
            result += (PREFIX + f'<｜DSML｜ invoke name="{f["name"]}">\n'
                + f'<｜DSML｜ parameter name="{key}" string="true">{ordinary(value)}</｜DSML｜ parameter>\n'
                + '</｜DSML｜ invoke>\n</｜DSML｜ calls>' + EOS)
        else:
            # Wire validator enforces one tool result immediately after each
            # assistant call; no consecutive user/tool normalization is needed.
            result += USER + '<tool_result>' + ordinary(message['content']) + '</tool_result>'
    return result + PREFIX


class Counter(NativeFirstCounter):
    def inspect(self, request):
        wire = payload(request)
        prompt = render(request,self.preamble,self.reference.reserved)
        count = len(self.reference.tokenizer.encode(prompt,add_special_tokens=False).ids)
        return dict(version=VERSION,reference_input_tokens=count,
            wire_payload_sha256=digest(wire),rendered_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            recipe_revision=REVISION,max_requested_output_tokens=wire['max_tokens'],
            provider_prompt_parity_verified=False,executable_upstream_parity_tested=False,
            provider_cost_cap=False,inference_authorized=False)
