"""Restricted four-turn source-derived counter, not verified server accounting.

Pinned dsv41.rs render_message/render_tool_calls/normalize_messages semantics.
Single string-valued action argument, one bound result per call, no thinking,
and only the fixed recovery/terminal reminders are supported. No network.
Live use must compare each provider usage and stop on mismatch, not fit offsets.
"""
from copy import deepcopy
import hashlib
import json

from runtime.native_action_counter import (
    NativeFirstCounter, REVISION, render_first_request, validate_candidate_payload)
from runtime.native_action_history import NativeHistory
from runtime.native_action_interface import _load
from runtime.deepseek_text_counter import ASSISTANT, END_THINK, EOS, USER

VERSION = 'native-trajectory-reference-v1'
PREFIX = ASSISTANT + END_THINK + '\n\n<｜DSML｜ calls>\n'


def validate_trajectory_payload(payload):
    """Structural replay only: synthetic envelopes are never research outputs."""
    if type(payload) is not dict or type(payload.get('messages')) is not list:
        raise ValueError('Expected native payload')
    initial = deepcopy(payload)
    initial['messages'] = initial['messages'][:2]
    validate_candidate_payload(initial)
    history = NativeHistory(initial, observation_byte_limit=1048576)
    current = history.begin()['payload']
    while current != payload:
        pos = len(current['messages'])
        messages = payload['messages']
        if (messages[:pos] != current['messages'] or pos+1 >= len(messages)
                or type(messages[pos]) is not dict or type(messages[pos+1]) is not dict):
            raise ValueError('History does not extend a supported request')
        assistant, observation = messages[pos:pos+2]
        if (set(assistant) != {'role', 'content', 'tool_calls'}
                or set(observation) != {'role', 'tool_call_id', 'content'}
                or observation['role'] != 'tool'):
            raise ValueError('Native assistant/tool pair required')
        # Reuse the same parser/horizon checks; no generated data is saved here.
        body = {'choices':[{'index':0, 'finish_reason':'tool_calls', 'message':assistant}]}
        decision = history.accept(json.dumps(body, ensure_ascii=False, allow_nan=False))
        if decision['stop']:
            raise ValueError('Cannot generate after terminal or forbidden action')
        history.observe(observation['tool_call_id'], observation['content'])
        current = history.begin()['payload']
    return initial


def render_trajectory(payload, preamble, reserved):
    initial = validate_trajectory_payload(payload)
    first = render_first_request(initial, preamble, reserved)
    if not first.endswith(PREFIX):
        raise ValueError('Reference first prefix mismatch')
    result = first[:-len(PREFIX)]
    previous = 'user'
    for message in payload['messages'][2:]:
        role = message['role']
        if role == 'assistant':
            call = message['tool_calls'][0]
            name = call['function']['name']
            arguments = _load(call['function']['arguments'])
            key, value = next(iter(arguments.items()))
            # Every schema in this profile has exactly one string argument.
            if any(s and s in value for s in reserved):
                raise ValueError('Reserved spelling in argument requires segment-aware encoding')
            result += (ASSISTANT + END_THINK + '\n\n<｜DSML｜ calls>\n'
                       + f'<｜DSML｜ invoke name="{name}">\n'
                       + f'<｜DSML｜ parameter name="{key}" string="true">{value}</｜DSML｜ parameter>\n'
                       + '</｜DSML｜ invoke>\n</｜DSML｜ calls>' + EOS)
        else:
            content = message['content']
            if any(s and s in content for s in reserved):
                raise ValueError('Reserved spelling in observation/reminder')
            result += '\n\n' if previous in ('user', 'tool') else USER
            result += '<tool_result>'+content+'</tool_result>' if role == 'tool' else content
        previous = role
    return result + PREFIX


class NativeTrajectoryCounter(NativeFirstCounter):
    def inspect(self, payload):
        prompt = render_trajectory(payload, self.preamble, self.reference.reserved)
        n = len(self.reference.tokenizer.encode(prompt, add_special_tokens=False).ids)
        return dict(version=VERSION, reference_input_tokens=n,
                    rendered_prompt_sha256=hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                    recipe_revision=REVISION, max_requested_output_tokens=payload['max_tokens'],
                    provider_prompt_parity_verified=False, executable_upstream_parity_tested=False,
                    provider_cost_cap=False, inference_authorized=False)
