"""Injected short action-first loop, not an authorized live model runner.

Caller owns frozen inputs, provider/token/fee/deadline gates, session closure and
batch stopping. save must be exclusive and durable; callbacks must not retry.
This module does not construct a model transport, infer semantic labels or grade.
"""
from copy import deepcopy
import time

from runtime import cache_action_preview as preview
from runtime.cache_action_horizon import classify, phase, MAX_GENERATIONS
from runtime.cache_intent_observation_bound import ObservationBound, serialize
from runtime.bird_readonly_audit import digest

RECOVERY = 'Exploration ended. Answer, abstain, or use the one reserved read-only SQL attempt. Ten reserved units remain. Do not read the cache now.'
TERMINAL = 'Tool horizon reached. Return only answer or abstain; no further tools.'


def run(envelope, session, generate, save, observation_bound):
    if not isinstance(observation_bound, ObservationBound):
        raise TypeError('Explicit observation bound required')
    preview.assert_initial_session(envelope, session.describe())
    save('started', dict(envelope=deepcopy(envelope), initial_state=session.describe(),
                         semantic_labels_generated=False, contract_elicited=False))
    messages = deepcopy(envelope['messages'])
    attempts = calls = 0

    def finish(status):
        result = dict(status=status, generation_calls=calls, tool_attempts=attempts,
                      semantic_labels_generated=False, contract_elicited=False)
        save('summary', result)
        return result

    def invoke(name, pending, callback, key):
        save(name+'.pending', pending)
        start = time.perf_counter()
        try:
            value = callback()
        except BaseException as exc:
            save(name+'.failure', dict(error_type=type(exc).__name__, automatic_retry=False))
            raise
        save(name+'.response', dict(request_sha256=digest(pending),
                                   **{key:deepcopy(value)}, wall_seconds=time.perf_counter()-start))
        return value

    for number in range(1, MAX_GENERATIONS+1):
        stage = phase(number)
        if stage != 'exploration':
            messages.append(dict(role='user', content=RECOVERY if stage == 'recovery' else TERMINAL))
        request = dict(generation_id=f'g{number:03d}', stage=stage, messages=deepcopy(messages))
        calls = number
        response = invoke(f'g{number:03d}', request, lambda: generate(deepcopy(request)), 'response')
        if (type(response) is not dict or type(response.get('content')) is not str
                or response.get('finish_reason') != 'stop'):
            return finish('INVALID_ACTION_RESPONSE')
        decision = classify(response['content'], number)
        save(f'g{number:03d}.decision', decision)
        if decision['stop']:
            return finish(decision['status'])
        action = decision['action']
        messages.append(dict(role='assistant', content=response['content']))
        attempts += 1
        pending = dict(generation_id=request['generation_id'], tool=action['kind'],
                       arguments={k:v for k,v in action.items() if k != 'kind'})
        observation = invoke(f't{attempts:03d}', pending,
                             lambda: session.call(pending['tool'], pending['arguments']), 'observation')
        if session.describe()['attempts_used'] != attempts:
            raise ValueError('Tool counter mismatch; stop batch')
        if type(observation) is not dict or observation.get('status') == 'ENVIRONMENT_INVALID':
            raise ValueError('Invalid tool environment; stop batch')
        try:
            exposure = observation_bound.apply(observation)
        except BaseException as exc:
            save(f't{attempts:03d}.exposure_failure', dict(error_type=type(exc).__name__, automatic_retry=False))
            raise
        save(f't{attempts:03d}.exposure', exposure)
        messages.append(dict(role='user', content=serialize(exposure['observation'])))
    raise AssertionError('Unreachable')
