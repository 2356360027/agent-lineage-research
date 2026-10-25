"""Injected ordinary workflow; no transport, payment approval or semantic labels.

The owner must freeze/admit source and inputs, enforce actual provider/token/fee
and subprocess time limits, use durable exclusive save, and close the session.
Callback deadline checks retain late responses but cannot interrupt callbacks.
The 1024-token observation policy is a per-observation bound, not proof of full
multi-turn context capacity. There is no retry or continuation after failure.
"""
from copy import deepcopy
import json
import math
import time

from runtime.bird_readonly_audit import digest, encoded
from runtime.cache_action_factorial_loop import check_transition
from runtime.cache_intent_observation_bound import ObservationBound as LegacyBound, serialize
from runtime.cache_intent_staged_loop import TERMINAL_INSTRUCTION
from runtime.cache_route_symmetric_design import build, classify
from runtime.cache_route_symmetric_tools import PROFILE

VERSION = 'cache-route-symmetric-loop-v1'


class ObservationBound(LegacyBound):
    """Fixed prospective cap; old runs keep their original 512-token class."""
    def apply(self, observation):
        original = deepcopy(observation)
        original_tokens = self.count(original)
        exposed = original
        if original_tokens > 1024:
            exposed = dict(status='OBSERVATION_TOO_LARGE', complete=False,
                           charged_units=original['charged_units'],
                           remaining_budget=original['remaining_budget'], attempt=original['attempt'])
            if original.get('status') == 'ENVIRONMENT_INVALID':
                exposed['status'] = 'ENVIRONMENT_INVALID'
        exposed_tokens = self.count(exposed)
        if exposed_tokens > 1024:
            raise ValueError('Incomplete status exceeds observation bound')
        return dict(observation=exposed, raw_observation_sha256=digest(observation),
                    raw_framed_tokens=original_tokens, exposed_framed_tokens=exposed_tokens,
                    bound=1024, rows_partially_exposed=False)


def initial_state(envelope, trusted_common):
    if encoded(envelope) not in [encoded(cell) for cell in build(trusted_common)]:
        raise ValueError('Envelope differs from source-bound symmetric protocol')
    packet = json.loads(envelope['messages'][1]['content'])
    context = packet['context']
    return dict(cache_id='v000', materialized_available=context['materialized_available'],
                read_cost=context['read_cost'], sql_call_cost=10, budget_profile=PROFILE,
                nonbinding_for_configured_horizon=True, initial_budget=60, remaining_budget=60,
                max_attempts=3, attempts_used=0, last_attempt_sql_only=False,
                cost_basis='declared_relative_units', database_sha256=packet['database_sha256'])


def run(envelope, trusted_common, session, generate, save, observation_bound, *,
        deadline, clock=time.monotonic):
    calls = attempts = 0

    def check_deadline():
        if clock() >= deadline:
            raise TimeoutError('Symmetric loop deadline reached')

    def invoke(name, pending, callback, key):
        check_deadline()
        save(name+'.pending', deepcopy(pending))
        started = clock()
        try:
            value = callback()
        except BaseException as exc:
            save(name+'.failure', dict(error_type=type(exc).__name__, automatic_retry=False))
            raise
        save(name+'.response', dict(request_sha256=digest(pending),
                                   **{key: deepcopy(value)}, wall_seconds=clock()-started))
        check_deadline()
        return value

    try:
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not 0 < deadline-clock() <= 600):
            raise ValueError('Finite deadline within ten minutes required')
        if not isinstance(observation_bound, ObservationBound):
            raise TypeError('Symmetric 1024-token observation policy required')
        expected = initial_state(envelope, trusted_common)
        if encoded(session.describe()) != encoded(expected):
            raise ValueError('Initial session mismatch')
        save('started', dict(protocol=VERSION, envelope=deepcopy(envelope), initial_state=expected,
                             semantic_labels_generated=False, contract_elicited=False))
        messages = deepcopy(envelope['messages'])
        for number in range(1, 5):
            check_deadline()
            if encoded(session.describe()) != encoded(expected):
                raise ValueError('State changed between calls')
            if number == 4:
                messages.append(dict(role='user', content=TERMINAL_INSTRUCTION))
            request = dict(generation_id=f'g{number:03d}',
                           stage='terminal' if number == 4 else 'symmetric_tool_opportunity',
                           messages=deepcopy(messages))
            calls = number  # Adapter, not this counter, owns provider dispatch counts.
            response = invoke(f'g{number:03d}', request, lambda: generate(deepcopy(request)), 'response')
            if encoded(session.describe()) != encoded(expected):
                raise ValueError('State changed during generation')
            valid = (type(response) is dict and type(response.get('content')) is str
                     and response.get('finish_reason') == 'stop')
            decision = classify(response['content'], number) if valid else dict(
                status='INVALID_ACTION_RESPONSE', stop=True, dispatch_tool=False, action=None)
            save(f'g{number:03d}.decision', decision)
            if decision['stop']:
                summary = dict(status=decision['status'], generation_calls=calls, tool_attempts=attempts,
                               semantic_labels_generated=False, contract_elicited=False)
                save('summary', summary)
                return summary
            action = decision['action']
            messages.append(dict(role='assistant', content=response['content']))
            attempts += 1
            pending = dict(generation_id=request['generation_id'], tool=action['kind'],
                           arguments={k:v for k,v in action.items() if k != 'kind'})
            observation = invoke(f't{attempts:03d}', pending,
                                 lambda: session.call(pending['tool'], pending['arguments']), 'observation')
            after = session.describe()
            check_transition(expected, after, observation, pending['tool'])
            if pending['tool'] == 'read_cache' and not expected['materialized_available']:
                if observation.get('status') != 'CACHE_UNAVAILABLE' or any(
                        k in observation for k in ('rows', 'columns')):
                    raise ValueError('Unavailable cache leaked data or wrong rejection')
            expected = deepcopy(after)
            exposure = observation_bound.apply(observation)
            save(f't{attempts:03d}.exposure', exposure)
            messages.append(dict(role='user', content=serialize(exposure['observation'])))
        raise AssertionError('Unreachable')
    except BaseException as exc:
        save('failure', dict(error_type=type(exc).__name__, generation_calls=calls,
                             tool_attempts=attempts, automatic_retry=False, terminal=True))
        raise
