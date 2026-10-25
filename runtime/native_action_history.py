"""Bounded native-message state machine, with NO network or tool execution.

Caller owns authorization, durable pending/raw journals, usage/cost/deadline
checks and read-only ToolSession. A returned action is a proposal, not permission.
Four generations / two exploration tools / one SQL recovery / terminal response.
Existing frozen first-response runners are not changed or resumed by this code.
"""
from copy import deepcopy
import json

from runtime.native_action_counter import validate_candidate_payload
from runtime.native_action_interface import _load, parse_native_response_v2, ENVELOPE_POLICY_V2
from runtime.cache_action_horizon import phase, classify, MAX_GENERATIONS
from runtime.cache_action_loop import RECOVERY, TERMINAL

VERSION = 'native-action-history-offline-v1'


class NativeHistory:
    def __init__(self, initial_payload, *, observation_byte_limit):
        validate_candidate_payload(initial_payload)
        if type(observation_byte_limit) is not int or not 1 <= observation_byte_limit <= 1048576:
            raise ValueError('Explicit positive observation byte bound required')
        self._payload = deepcopy(initial_payload)
        self._limit = observation_byte_limit
        self._state = 'READY'
        self._generation = 0
        self._ids = set()
        self._pending_id = None

    def snapshot(self):
        return dict(version=VERSION, state=self._state, generation=self._generation,
                    pending_call_id=self._pending_id, envelope_policy=ENVELOPE_POLICY_V2,
                    network_authorized=False, execution_authorized=False)

    def begin(self):
        """Reserve exactly one response slot; caller journals before dispatch.

        This is NOT a token/fee reservation. Caller must not send until its
        separate native trajectory counter and budget/deadline checks pass.
        """
        if self._state != 'READY' or self._generation >= MAX_GENERATIONS:
            raise ValueError('No next generation available')
        self._generation += 1
        stage = phase(self._generation)
        if stage != 'exploration':
            self._payload['messages'].append(dict(role='user', content=RECOVERY if stage == 'recovery' else TERMINAL))
        self._state = 'AWAITING_RESPONSE'
        return dict(generation_id=f'g{self._generation:03d}', stage=stage,
                    payload=deepcopy(self._payload), execution_authorized=False)

    def fail(self):
        """Transport, accounting, persistence or environment failure: no resume."""
        self._state = 'FAILED'

    def accept(self, raw):
        """Caller must retain raw and validate accounting before this transition."""
        if self._state != 'AWAITING_RESPONSE':
            raise ValueError('No reserved response slot')
        try:
            parsed = parse_native_response_v2(raw)
            call_id = parsed['call_id']
            if call_id in self._ids:
                raise ValueError('Tool-call ID reused across turns')
            action = parsed['action']
            decision = classify(json.dumps(action, ensure_ascii=False, allow_nan=False), self._generation)
            # Preserve the original argument string exactly. Index is observed
            # response metadata, deliberately not a field of the outgoing call.
            original = _load(raw)['choices'][0]['message']['tool_calls'][0]
            call = {key:deepcopy(original[key]) for key in ('id', 'type', 'function')}
            self._payload['messages'].append(dict(role='assistant', content=None, tool_calls=[call]))
            self._ids.add(call_id)
            if decision['stop']:
                self._state = decision['status']
            else:
                self._pending_id = call_id
                self._state = 'AWAITING_OBSERVATION'
            return dict(decision, call_id=call_id, execution_authorized=False,
                        semantic_correctness_assessed=False)
        except (ValueError, TypeError, RecursionError):
            self._state = 'INVALID_RESPONSE'
            raise

    def observe(self, call_id, content):
        """Bind already validated/bounded tool exposure to its exact call.

        Content is tool data, not a system/user message. Never truncates silently.
        Byte cap is an additional transport bound, NOT a model-token guarantee;
        caller retains complete raw observation and applies its exposure policy.
        """
        if self._state != 'AWAITING_OBSERVATION':
            raise ValueError('No pending tool observation')
        if (type(call_id) is not str or call_id != self._pending_id
                or type(content) is not str or len(content.encode('utf-8')) > self._limit):
            self._state = 'FAILED'
            raise ValueError('Wrong call ID, nontext or oversized observation')
        self._payload['messages'].append(dict(role='tool', tool_call_id=call_id, content=content))
        self._pending_id = None
        self._state = 'READY'
