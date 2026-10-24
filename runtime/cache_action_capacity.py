"""Conditional reference-token certificate for the four-call development loop.

Not a remote tokenizer attestation. Each actual payload and each observation
join is checked; a violation stops rather than truncating or retrying.
"""
import json

from runtime import cache_action_preview as preview
from runtime.cache_action_loop import RECOVERY, TERMINAL
from runtime.cache_action_horizon import classify, MAX_GENERATIONS
from runtime.cache_intent_observation_bound import ObservationBound, serialize
from runtime.bird_readonly_audit import digest

JOIN_ALLOWANCE = 64


class ActionCapacity:
    def __init__(self, envelope, counter):
        preview.validate(envelope)
        self.counter = counter
        self.initial = preview.payload(envelope['messages'])['messages']
        self.base = counter.chat(self.initial)
        self.fixed = (RECOVERY, TERMINAL)
        self.fixed_tokens = [counter.text(s) for s in self.fixed]
        if type(self.base) is not int or self.base <= 0:
            raise ValueError('Invalid initial token count')
        if self.input_bound(4)+512 > 32768:
            raise ValueError('Full conditional horizon exceeds local cap')

    def input_bound(self, generation):
        if type(generation) is not int or not 1 <= generation <= MAX_GENERATIONS:
            raise ValueError('Generation outside fixed horizon')
        # Pinned plain-chat encoding: assistant role/think/end adds <=3 tokens;
        # observation user marker <=1. Framed observation cap includes overhead.
        bound = self.base + (generation-1)*(515+513)
        if generation >= 3: bound += self.fixed_tokens[0]+1+JOIN_ALLOWANCE
        if generation >= 4: bound += self.fixed_tokens[1]+1+JOIN_ALLOWANCE
        return bound

    def check_observation_text(self, text):
        plain = self.counter.text(text)
        framed = self.counter.chat([dict(role='system',content=''),dict(role='user',content=text)])
        if plain > 512 or framed > 512:
            raise ValueError('Observation exceeds conditional cap')
        for suffix in self.fixed:
            if self.counter.text(text+'\n\n'+suffix) > plain+self.counter.text(suffix)+JOIN_ALLOWANCE:
                raise ValueError('Observation join violates future stage bound')

    def observation_bound(self):
        capacity = self
        class CheckedBound(ObservationBound):
            def apply(self, observation):
                exposure = super().apply(observation)
                capacity.check_observation_text(serialize(exposure['observation']))
                return exposure
        return CheckedBound(self.counter.chat)

    def __call__(self, payload):
        messages = payload.get('messages')
        if (type(messages) is not list or payload != preview.payload(messages)
                or messages[:2] != self.initial):
            raise ValueError('Initial payload or generation settings changed')
        tail = messages[2:]
        pointer = assistants = 0
        while pointer < len(tail):
            if assistants >= 3 or pointer+1 >= len(tail):
                raise ValueError('Unexpected history length')
            a, o = tail[pointer:pointer+2]
            for m, role in ((a,'assistant'), (o,'user')):
                if (type(m) is not dict or set(m) != {'role','content'}
                        or m['role'] != role or type(m['content']) is not str):
                    raise ValueError('Unexpected history message')
            if self.counter.text(a['content']) > 512 or not classify(a['content'], assistants+1)['dispatch_tool']:
                raise ValueError('Prior action was not an admissible bounded tool proposal')
            value = json.loads(o['content'])
            if (type(value) is not dict or set(value) != {'tool_observation'}
                    or type(value['tool_observation']) is not dict
                    or serialize(value['tool_observation']) != o['content']):
                raise ValueError('Unexpected observation serialization')
            self.check_observation_text(o['content'])
            assistants += 1
            pointer += 2
            if assistants in (2,3):
                expected = dict(role='user',content=self.fixed[assistants-2])
                if pointer >= len(tail) or tail[pointer] != expected:
                    raise ValueError('Missing or changed stage instruction')
                pointer += 1
        generation = assistants+1
        actual = self.counter.chat(messages)
        if type(actual) is not int or not 0 < actual <= self.input_bound(generation):
            raise ValueError('Reference token bound violated; never truncate')
        return dict(payload_sha256=digest(payload), generation_index=generation,
                    reference_input_tokens=actual, input_bound=self.input_bound(generation),
                    conditional=True, truncated=False, provider_tokenizer_identity_proven=False)

    def certificate(self):
        inputs = [self.input_bound(g) for g in range(1,5)]
        return dict(initial_reference_tokens=self.base, input_bounds=inputs,
                    total_input_bound=sum(inputs), total_output_bound=4*512,
                    max_generations=4, join_allowance_each=JOIN_ALLOWANCE,
                    local_window=32768, full_history_with_output_bound=inputs[-1]+512,
                    conditional_on_runtime_checks=True, provider_tokenizer_identity_proven=False,
                    inference_authorized=False)
