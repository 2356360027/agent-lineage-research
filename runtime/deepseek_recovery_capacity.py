"""Conditional reference-token bound for the full recovery-v2 horizon.

Condition: each accepted assistant text <=512 tokens, each full-or-withheld
observation <=512 framed tokens, and either allowed instruction concatenation
adds <=64 tokens beyond separately counted pieces. Check that last condition
when exposing EVERY observation, not for the first time during final recovery.
Violations are environment/admission failures, not intent-drift observations.
This bounds the pinned local reference, not an unverified provider tokenizer.
"""
import json

from runtime import cache_intent_recovery_preview as preview
from runtime.cache_intent_prompts import CONTRACT_FIELDS
from runtime.cache_intent_staged_loop import ACTION_INSTRUCTION, RECOVERY_INSTRUCTION, TERMINAL_INSTRUCTION
from runtime.cache_intent_observation_bound import ObservationBound, serialize
from runtime.deepseek_trajectory_adapter import make_payload
from runtime.bird_readonly_audit import digest

JOIN_ALLOWANCE = 64


class RecoveryCapacity:
    def __init__(self, envelope, counter):
        preview.validate_preview(envelope)
        self.counter = counter
        self.initial = make_payload(envelope['messages'])['messages']
        fixture = json.dumps(dict.fromkeys(CONTRACT_FIELDS,'fixture'))
        stage2 = preview.next_generation_messages(envelope,fixture)[-1]['content']+'\n'+ACTION_INSTRUCTION
        self.fixed = (stage2, RECOVERY_INSTRUCTION, TERMINAL_INSTRUCTION)
        self.base = counter.chat(self.initial)
        self.fixed_tokens = [counter.text(s) for s in self.fixed]
        # BOS/system/user/current-task/assistant-prefix included in self.base.
        # Each historical assistant adds role + </think> + EOS, each ordinary
        # observation adds at most one user marker. Merged markers only reduce it.
        self.bound = (self.base + sum(self.fixed_tokens) + 3 + 22*(512+3)
                      + 21*(512+1) + 2*JOIN_ALLOWANCE + 512)
        if self.bound > 32768:
            raise ValueError('Full conditional horizon exceeds local 32768 cap')

    def check_observation_text(self, text):
        framed = self.counter.chat([{'role':'system','content':''},{'role':'user','content':text}])
        plain = self.counter.text(text)
        if framed > 512 or plain > 512:
            raise ValueError('Observation exceeds admitted reference budget')
        for suffix in self.fixed[1:]:
            combined = self.counter.text(text+'\n\n'+suffix)
            if combined > plain+self.counter.text(suffix)+JOIN_ALLOWANCE:
                raise ValueError('Observation fails future recovery/terminal join bound')

    def input_bound(self, generation):
        if type(generation) is not int or not 1 <= generation <= 23:
            raise ValueError('Generation outside frozen horizon')
        bound = self.base
        if generation >= 2:
            bound += self.fixed_tokens[0]+1+(generation-1)*515+(generation-2)*513
        if generation >= 22: bound += self.fixed_tokens[1]+1+JOIN_ALLOWANCE
        if generation >= 23: bound += self.fixed_tokens[2]+1+JOIN_ALLOWANCE
        return bound

    def observation_bound(self):
        capacity = self
        class CheckedBound(ObservationBound):
            def apply(self, observation):
                exposure = super().apply(observation)
                capacity.check_observation_text(serialize(exposure['observation']))
                return exposure
        return CheckedBound(self.counter.chat)

    def __call__(self, payload):
        messages = payload['messages']
        if payload['max_tokens'] != 512 or messages[:2] != self.initial:
            raise ValueError('Frozen capacity input changed')
        assistants = observations = 0
        fixed_seen = set()
        previous_observation = False
        joins = 0
        for m in messages[2:]:
            if type(m) is not dict or set(m) != {'role','content'} or type(m['content']) is not str:
                raise ValueError('Unexpected history message')
            if m['role'] == 'assistant':
                assistants += 1
                if self.counter.text(m['content']) > 512:
                    raise ValueError('Assistant history exceeds admitted cap')
                previous_observation = False
            elif m['role'] == 'user' and m['content'] in self.fixed:
                text = m['content']
                if text in fixed_seen: raise ValueError('Repeated fixed instruction')
                fixed_seen.add(text)
                if previous_observation:
                    if text not in self.fixed[1:]: raise ValueError('Unexpected user merge')
                    joins += 1
                previous_observation = False
            elif m['role'] == 'user':
                value = json.loads(m['content'])
                if type(value) is not dict or set(value) != {'tool_observation'} or previous_observation:
                    raise ValueError('Unexpected tool observation sequence')
                self.check_observation_text(m['content'])
                observations += 1
                previous_observation = True
            else:
                raise ValueError('Unsupported role')
        if assistants > 22 or observations > 21 or joins > 2:
            raise ValueError('History horizon exceeded')
        generation = assistants+1
        required_fixed = set(self.fixed[:(3 if generation==23 else 2 if generation==22 else 1 if generation>=2 else 0)])
        if observations > max(0,generation-2) or fixed_seen != required_fixed:
            raise ValueError('History does not match generation stage')
        actual = self.counter.chat(messages)
        step_bound = self.input_bound(generation)
        if actual > step_bound or actual + 512 > self.bound:
            raise ValueError('Reference encoding violates conditional bound; no truncation')
        return {'payload_sha256':digest(payload), 'reference_input_tokens':actual,
            'generation_index':generation, 'generation_input_bound':step_bound,
            'full_history_bound_including_completion':self.bound, 'local_admission_cap':32768,
            'conditioned_on_runtime_checks':True, 'future_observation_joins_checked':True,
            'provider_tokenizer_identity_proven':False, 'truncated':False}

    def certificate(self):
        return {'initial_reference_tokens':self.base,'fixed_instruction_tokens':self.fixed_tokens,
            'input_bounds_by_generation':[self.input_bound(g) for g in range(1,24)],
            'total_input_bound':sum(self.input_bound(g) for g in range(1,24)),
            'total_output_bound':23*512,
            'max_historical_assistants':22,'max_observations':21,'max_generations':23,
            'full_history_bound_including_completion':self.bound,'window':32768,
            'conditioned_on_runtime_checks':True,'join_allowance_each':JOIN_ALLOWANCE,
            'provider_tokenizer_identity_proven':False,'inference_authorized':False}
