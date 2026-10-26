"""Injected full diagnostic workflow; no live client, fee or batch admission.

The owner must authenticate public inputs/cache against sealed reference files,
freeze sources, supply guarded callbacks and real token counting, enforce a hard
subprocess deadline, then close and seal both journals even on failure. This
loop checks callback deadlines but cannot terminate a blocked callback.
"""
from copy import deepcopy
import json
import math
import time

from runtime.bird_readonly_audit import digest, encoded
from runtime.cache_action_factorial_loop import check_transition
from runtime.cache_intent_observation_bound import serialize
from runtime.cache_intent_tools import valid_result
from runtime.cache_route_symmetric_loop import ObservationBound
from runtime.cache_route_symmetric_tools import PROFILE
from runtime.student_club_definition_history import DefinitionHistory
from runtime.student_club_diagnostic_design import build
from runtime import student_club_readonly as scoped

VERSION = 'student-club-injected-diagnostic-loop-v1'


def initial_state(common, job, session):
    if (type(job) is not dict or encoded(job) not in [encoded(j) for j in build(common)['jobs']]
            or type(session) is not scoped.ToolSession or session.closed or session.invalidated):
        raise ValueError('Exact prospective job and open scoped session required')
    # Shape/hash validation is not proof of authentic execution; batch admission
    # must additionally bind this object to the independently sealed reference.
    cached = session.cache
    valid_result(cached)
    if (cached.get('sql_sha256') != digest(common['history_sql'])
            or len(cached['columns']) != 1 or len(cached['rows']) != 1
            or cached['rows'][0][0][0] != 'integer' or int(cached['rows'][0][0][1]) < 0):
        raise ValueError('Only historical nonnegative scalar count cache admitted')
    expected = dict(cache_id='v000', materialized_available=job['available'], read_cost=job['price'],
        sql_call_cost=10, budget_profile=PROFILE, nonbinding_for_configured_horizon=True,
        initial_budget=60, remaining_budget=60, max_attempts=3, attempts_used=0,
        last_attempt_sql_only=False, cost_basis='declared_relative_units',
        database_sha256=common['database_sha256'], column_policy_version=scoped.VERSION,
        column_policy_sha256=digest(scoped.policy()))
    if encoded(session.describe()) != encoded(expected):
        raise ValueError('Scoped session differs from public design')
    session._check_database()
    return expected


def run(common, job, session, generate, save, observation_bound, *, deadline, clock=time.monotonic):
    calls = attempts = 0
    history = None

    def check_deadline():
        if clock() >= deadline:
            raise TimeoutError('Diagnostic deadline reached')

    def invoke(name, request, callback, field):
        check_deadline()
        save(name+'.pending', deepcopy(request))
        check_deadline()
        started = clock()
        try:
            value = callback()
        except BaseException as error:
            save(name+'.failure', dict(error_type=type(error).__name__, automatic_retry=False))
            raise
        save(name+'.response', dict(request_sha256=digest(request),
            **{field:deepcopy(value)}, wall_seconds=clock()-started))
        check_deadline()  # Retain late response, but do not dispatch a next action.
        return value

    def summary(status):
        result = dict(status=status, generation_callback_attempts=calls, tool_attempts=attempts,
            contract_elicited=job['protocol'] != 'ordinary', semantic_labels_generated=False,
            provider_dispatch_count_verified=False, inference_authorized_here=False,
            final_state=deepcopy(session.describe()))
        save('summary', result)
        return result

    try:
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not 0 < deadline-clock() <= 600):
            raise ValueError('Finite deadline within ten minutes required')
        if not isinstance(observation_bound, ObservationBound):
            raise TypeError('1024-token complete-or-oversize policy required')
        expected = initial_state(common, job, session)
        save('started', dict(version=VERSION, job=deepcopy(job), initial_state=expected,
            cache_sequence_sha256=session.cache['sequence_sha256'], semantic_labels_generated=False))
        history = DefinitionHistory(common, job, save)
        for number in range(1, job['max_model_calls']+1):
            check_deadline()
            if encoded(session.describe()) != encoded(expected):
                raise ValueError('Tool state changed before generation')
            session._check_database()
            request = history.begin()
            calls += 1
            raw = invoke(f'g{number:03d}', request, lambda:generate(deepcopy(request)), 'raw_response_text')
            if encoded(session.describe()) != encoded(expected):
                raise ValueError('Tool state changed during generation')
            session._check_database()
            try:
                decision = history.accept(raw)
            except ValueError:
                if history.state != 'INVALID_RESPONSE':
                    raise
                return summary('INVALID_ACTION_RESPONSE')
            if decision['kind'] == 'definition_recorded':
                continue
            action = decision['action']
            if history.state == 'TERMINAL':
                return summary('ANSWERED' if action['kind'] == 'answer' else 'ABSTAINED')
            attempts += 1
            if attempts > job['max_tool_attempts']:
                raise ValueError('Tool horizon exceeded')
            pending = dict(action_event=decision['event_id'], call_id=decision['call_id'],
                tool=action['kind'], arguments={k:v for k,v in action.items() if k != 'kind'})
            observation = invoke(f't{attempts:03d}', pending,
                lambda:session.call(pending['tool'], pending['arguments']), 'observation')
            after = session.describe()
            check_transition(expected, after, observation, pending['tool'])
            if pending['tool'] == 'read_cache' and not expected['materialized_available']:
                if (observation.get('status') != 'CACHE_UNAVAILABLE'
                        or any(k in observation for k in ('rows', 'columns'))):
                    raise ValueError('Unavailable cache exposed data')
            expected = deepcopy(after)
            # Canonical nested order is identical before token counting and wire
            # serialization; no row prefix or silent truncation is exposed.
            exposure = json.loads(encoded(observation_bound.apply(json.loads(encoded(observation)))))
            save(f't{attempts:03d}.exposure', exposure)
            history.observe(decision['call_id'], serialize(exposure['observation']))
        raise AssertionError('Generation horizon ended without terminal outcome')
    except BaseException as error:
        if history is not None and history.state not in ('TERMINAL','INVALID_RESPONSE','JOURNAL_FAILED','FAILED'):
            history.fail(type(error).__name__)
        save('failure', dict(error_type=type(error).__name__, generation_callback_attempts=calls,
            tool_attempts=attempts, automatic_retry=False, terminal=True))
        raise
