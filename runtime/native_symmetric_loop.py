"""Injected native symmetric workflow; no live client or payment authorization.

Owner supplies guarded model adapter, source-bound read-only session, exclusive
journal, deadline and observation counter; owner must close/verify session even
on failure. Callback deadline checks cannot terminate a blocking callback.
"""
from copy import deepcopy
import math
import json
import time

from runtime.bird_readonly_audit import digest, encoded
from runtime.cache_action_factorial_loop import check_transition
from runtime.cache_action_preview import payload
from runtime.cache_intent_observation_bound import serialize
from runtime.cache_route_symmetric_loop import initial_state, ObservationBound
from runtime.native_action_interface import build_candidate
from runtime.native_action_history import NativeHistory

VERSION='native-symmetric-loop-v3-canonical-wire-observation'


def run(envelope, trusted_common, session, generate, save, observation_bound, *,
        deadline, clock=time.monotonic):
    calls=attempts=0
    history=None

    def check_deadline():
        if clock()>=deadline: raise TimeoutError('Native loop deadline')

    def invoke(name, request, callback, field):
        check_deadline()
        save(name+'.pending',deepcopy(request))
        check_deadline()
        started=clock()
        try:
            value=callback()
        except BaseException as exc:
            save(name+'.failure',dict(error_type=type(exc).__name__,automatic_retry=False))
            raise
        save(name+'.response',dict(request_sha256=digest(request),
             **{field:deepcopy(value)},wall_seconds=clock()-started))
        check_deadline()
        return value

    try:
        if (type(deadline) not in (int,float) or not math.isfinite(deadline)
                or not 0<deadline-clock()<=600):
            raise ValueError('Finite deadline within ten minutes required')
        if not isinstance(observation_bound,ObservationBound):
            raise TypeError('Symmetric 1024-token observation policy required')
        expected=initial_state(envelope,trusted_common)
        if encoded(session.describe())!=encoded(expected):
            raise ValueError('Source-bound session mismatch')
        # Journal writes sort keys. Count and transmit that same nested schema
        # order, not a dict-order variant reconstructed only during auditing.
        initial=json.loads(encoded(build_candidate(payload(envelope['messages']))['payload']))
        history=NativeHistory(initial,
                              observation_byte_limit=1048576)
        save('started',dict(protocol=VERSION,envelope=deepcopy(envelope),initial_state=expected,
                            contract_elicited=False,semantic_labels_generated=False))
        for number in range(1,5):
            check_deadline()
            if encoded(session.describe())!=encoded(expected):
                raise ValueError('State changed between model calls')
            request=history.begin()
            calls=number
            raw=invoke(f'g{number:03d}',request,lambda:generate(deepcopy(request)),'raw_response_text')
            if encoded(session.describe())!=encoded(expected):
                raise ValueError('State changed during generation')
            try:
                decision=history.accept(raw)
            except (ValueError,TypeError,RecursionError):
                decision=dict(status='INVALID_ACTION_RESPONSE',stop=True,dispatch_tool=False,action=None)
            save(f'g{number:03d}.decision',decision)
            if decision['stop']:
                result=dict(status=decision['status'],generation_calls=calls,tool_attempts=attempts,
                            semantic_labels_generated=False,contract_elicited=False)
                save('summary',result)
                return result
            action=decision['action']; attempts+=1
            pending=dict(generation_id=request['generation_id'],call_id=decision['call_id'],
                         tool=action['kind'],arguments={k:v for k,v in action.items() if k!='kind'})
            observation=invoke(f't{attempts:03d}',pending,
                lambda:session.call(pending['tool'],pending['arguments']),'observation')
            after=session.describe()
            check_transition(expected,after,observation,pending['tool'])
            if pending['tool']=='read_cache' and not expected['materialized_available']:
                if observation.get('status')!='CACHE_UNAVAILABLE' or any(k in observation for k in ('rows','columns')):
                    raise ValueError('Unavailable cache leaked rows')
            expected=deepcopy(after)
            # Tool observations become a JSON string in the next message.
            # Canonicalize before counting AND after the bound policy so disk
            # reload reproduces the exact string, not only an equal mapping.
            exposure=json.loads(encoded(observation_bound.apply(json.loads(encoded(observation)))))
            save(f't{attempts:03d}.exposure',exposure)
            history.observe(decision['call_id'],serialize(exposure['observation']))
        raise AssertionError('Unreachable')
    except BaseException as exc:
        if history is not None: history.fail()
        save('failure',dict(error_type=type(exc).__name__,generation_calls=calls,
                           tool_attempts=attempts,automatic_retry=False,terminal=True))
        raise
