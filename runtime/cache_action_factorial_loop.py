"""Injected factorial loop; no transport, approval, semantic scorer or CLI.

Retains the old four-generation schedule without modifying its frozen runner.
Caller owns durable exclusive journals, source/input admission, cost guard,
transport/SQL hard timeouts, session closure and terminal batch failure handling.
Deadline checks here stop subsequent work and retain late results; they cannot
interrupt a blocking callback. No automatic retry or reconstruction of output.
"""
from copy import deepcopy
import math
import time

from runtime.cache_action_factorial_capacity import validate_cell
from runtime.cache_action_horizon import classify, phase, MAX_GENERATIONS
from runtime.cache_action_loop import RECOVERY, TERMINAL
from runtime.cache_intent_observation_bound import ObservationBound, serialize
from runtime.cache_intent_prompts import _json
from runtime.cache_intent_tools import SHORT_RECOVERY_PROFILE
from runtime.bird_readonly_audit import digest

VERSION = 'ordinary-factorial-loop-v1'


def initial_state(envelope):
    context = validate_cell(envelope)['context']
    return dict(context['cache'], sql_call_cost=10, budget_profile=SHORT_RECOVERY_PROFILE,
                nonbinding_for_configured_horizon=True, initial_budget=30, remaining_budget=30,
                max_attempts=3, attempts_used=0, cost_basis='declared_relative_units',
                database_sha256=context['database_sha256'], exploration_attempts=2,
                exploration_allowance=20, recovery_allowance=10, last_attempt_sql_only=True)


def check_transition(before, after, observation, tool):
    """Detect state drift before exposing data; not a substitute for ToolSession."""
    if type(observation) is not dict or observation.get('status') == 'ENVIRONMENT_INVALID':
        raise ValueError('Invalid tool environment')
    charge = observation.get('charged_units')
    unit_cost = before['read_cost'] if tool == 'read_cache' else 10
    if type(charge) is not int or charge not in (0, unit_cost):
        raise ValueError('Invalid tool charge')
    if observation.get('status') == 'EXECUTED' and charge != unit_cost:
        raise ValueError('Executed tool was not charged its declared cost')
    if tool == 'read_cache' and not before['materialized_available']:
        if charge != 0 or observation.get('complete') is not False:
            raise ValueError('Unavailable cache exposed or charged')
    expected = deepcopy(before)
    expected['attempts_used'] += 1
    expected['remaining_budget'] -= charge
    if expected['remaining_budget'] < 0 or _json(after) != _json(expected):
        raise ValueError('Tool state transition mismatch')
    for field, value in (('attempt', expected['attempts_used']),
                         ('remaining_budget', expected['remaining_budget'])):
        if type(observation.get(field)) is not int or observation[field] != value:
            raise ValueError('Observation and tool state disagree')


def run(envelope, session, generate, save, observation_bound, *, deadline, clock=time.monotonic):
    calls = attempts = 0

    def check_deadline():
        if clock() >= deadline:
            raise TimeoutError('Factorial loop deadline reached')

    def invoke(name, pending, callback, key):
        check_deadline()
        save(name+'.pending', pending)
        started = clock()
        try:
            value = callback()
        except BaseException as exc:
            save(name+'.failure', dict(error_type=type(exc).__name__, automatic_retry=False))
            raise
        save(name+'.response', dict(request_sha256=digest(pending),
                                   **{key: deepcopy(value)}, wall_seconds=clock()-started))
        check_deadline()  # Preserve even a late answer; do not start another call.
        return value

    try:
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not 0 < deadline-clock() <= 600):
            raise ValueError('Finite deadline within ten minutes required')
        if not isinstance(observation_bound, ObservationBound):
            raise TypeError('Explicit observation bound required')
        expected = initial_state(envelope)
        if _json(session.describe()) != _json(expected):
            raise ValueError('Factorial preview/session mismatch')
        save('started', dict(protocol=VERSION, envelope=deepcopy(envelope), initial_state=expected,
                             semantic_labels_generated=False, contract_elicited=False))
        messages = deepcopy(envelope['messages'])
        for number in range(1, MAX_GENERATIONS+1):
            check_deadline()
            if _json(session.describe()) != _json(expected):
                raise ValueError('State changed between calls')
            stage = phase(number)
            if stage != 'exploration':
                messages.append(dict(role='user', content=RECOVERY if stage == 'recovery' else TERMINAL))
            request = dict(generation_id=f'g{number:03d}', stage=stage, messages=deepcopy(messages))
            # Loop attempts are not provider dispatch counts: the adapter owns those.
            calls = number
            response = invoke(f'g{number:03d}', request, lambda: generate(deepcopy(request)), 'response')
            if _json(session.describe()) != _json(expected):
                raise ValueError('State changed during generation')
            valid = (type(response) is dict and type(response.get('content')) is str
                     and response.get('finish_reason') == 'stop')
            decision = (classify(response['content'], number) if valid else
                        dict(status='INVALID_ACTION_RESPONSE', stop=True, dispatch_tool=False, action=None))
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
                           arguments={k: v for k, v in action.items() if k != 'kind'})
            observation = invoke(f't{attempts:03d}', pending,
                                 lambda: session.call(pending['tool'], pending['arguments']), 'observation')
            after = session.describe()
            check_transition(expected, after, observation, pending['tool'])
            expected = deepcopy(after)
            exposure = observation_bound.apply(observation)
            save(f't{attempts:03d}.exposure', exposure)
            messages.append(dict(role='user', content=serialize(exposure['observation'])))
        raise AssertionError('Unreachable')
    except BaseException as exc:
        save('failure', dict(error_type=type(exc).__name__, generation_calls=calls,
                             tool_attempts=attempts, automatic_retry=False, terminal=True))
        raise
