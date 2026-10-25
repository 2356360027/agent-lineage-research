"""Conditional reference-token bounds; not remote tokenizer attestation.

Every actual payload is checked before dispatch by the eventual adapter. These
bounds are conditional on accepted output/observation lengths and the pinned
plain-chat encoding. No clipping, tokenization fallback or automatic retry.
"""
from copy import deepcopy
import json

from runtime.bird_readonly_audit import digest, encoded
from runtime.cache_action_preview import payload
from runtime.cache_intent_observation_bound import serialize
from runtime.cache_intent_staged_loop import TERMINAL_INSTRUCTION
from runtime.cache_route_symmetric_design import classify
from runtime.cache_route_symmetric_loop import ObservationBound, initial_state

JOIN_ALLOWANCE = 64


class SymmetricCapacity:
    def __init__(self, envelope, trusted_common, counter):
        initial_state(envelope, trusted_common)
        self.counter = counter
        self.initial = deepcopy(envelope['messages'])
        self.base = counter.chat(self.initial)
        self.terminal_tokens = counter.text(TERMINAL_INSTRUCTION)
        if type(self.base) is not int or self.base <= 0:
            raise ValueError('Invalid initial reference count')
        if type(self.terminal_tokens) is not int or self.terminal_tokens <= 0:
            raise ValueError('Invalid instruction count')
        if self.input_bound(4)+512 > 32768:
            raise ValueError('Conditional horizon exceeds local window')

    def input_bound(self, generation):
        if type(generation) is not int or not 1 <= generation <= 4:
            raise ValueError('Outside four-generation horizon')
        # Same pinned reference format as earlier runs, but no recovery suffix.
        # Assistant content <=512 plus <=3 role/end tokens; observation <=1024
        # framed tokens plus a conservative user marker allowance.
        bound = self.base+(generation-1)*(515+1025)
        if generation == 4: bound += self.terminal_tokens+1+JOIN_ALLOWANCE
        return bound

    def check_observation_text(self, text):
        plain = self.counter.text(text)
        framed = self.counter.chat([dict(role='system',content=''),dict(role='user',content=text)])
        if (type(plain) is not int or type(framed) is not int
                or not 0 < plain <= 1024 or not 0 < framed <= 1024):
            raise ValueError('Observation outside reference cap')
        joined = self.counter.text(text+'\n\n'+TERMINAL_INSTRUCTION)
        if type(joined) is not int or joined > plain+self.terminal_tokens+JOIN_ALLOWANCE:
            raise ValueError('Terminal join violates conditional bound')

    def observation_bound(self):
        capacity = self
        class CheckedBound(ObservationBound):
            def apply(self, observation):
                exposed = super().apply(observation)
                capacity.check_observation_text(serialize(exposed['observation']))
                return exposed
        return CheckedBound(self.counter.chat)

    def __call__(self, value):
        messages = value.get('messages') if type(value) is dict else None
        if (type(messages) is not list or encoded(value) != encoded(payload(messages))
                or messages[:2] != self.initial):
            raise ValueError('Initial messages or payload settings changed')
        tail = messages[2:]
        pointer = assistants = 0
        while pointer < len(tail):
            if assistants >= 3 or pointer+1 >= len(tail):
                raise ValueError('Unexpected history length')
            action, observation = tail[pointer:pointer+2]
            for message, role in ((action,'assistant'),(observation,'user')):
                if (type(message) is not dict or set(message) != {'role','content'}
                        or message['role'] != role or type(message['content']) is not str):
                    raise ValueError('Unexpected message')
            count = self.counter.text(action['content'])
            if (type(count) is not int or not 0 < count <= 512
                    or not classify(action['content'], assistants+1)['dispatch_tool']):
                raise ValueError('Unbounded or non-tool prior action')
            parsed = json.loads(observation['content'])
            if (type(parsed) is not dict or set(parsed) != {'tool_observation'}
                    or type(parsed['tool_observation']) is not dict
                    or serialize(parsed['tool_observation']) != observation['content']):
                raise ValueError('Unexpected observation serialization')
            self.check_observation_text(observation['content'])
            assistants += 1
            pointer += 2
            if assistants == 3:
                terminal = dict(role='user', content=TERMINAL_INSTRUCTION)
                if pointer >= len(tail) or tail[pointer] != terminal:
                    raise ValueError('Missing or changed terminal instruction')
                pointer += 1
        generation = assistants+1
        actual = self.counter.chat(messages)
        if type(actual) is not int or not 0 < actual <= self.input_bound(generation):
            raise ValueError('Actual reference count violates bound')
        return dict(payload_sha256=digest(value), generation_index=generation,
                    reference_input_tokens=actual, input_bound=self.input_bound(generation),
                    conditional=True, truncated=False, provider_tokenizer_identity_proven=False)

    def certificate(self):
        bounds = [self.input_bound(g) for g in range(1,5)]
        return dict(initial_reference_tokens=self.base, input_bounds=bounds,
                    total_input_bound=sum(bounds), total_output_bound=4*512,
                    observation_bound=1024,max_generations=4,local_window=32768,
                    full_history_with_output_bound=bounds[-1]+512,
                    join_allowance_each=JOIN_ALLOWANCE,conditional_on_runtime_checks=True,
                    provider_tokenizer_identity_proven=False,inference_authorized=False)
