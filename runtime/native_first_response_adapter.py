"""Injected first-response diagnostic. No network, tools, SQL or launcher.

Transport/cost/identity failures stop the batch; malformed actions are observed
invalid responses, not repaired actions or scientific intent errors.
"""
from copy import deepcopy
import time

from runtime.native_action_interface import _load, parse_native_response
from runtime.native_action_counter import validate_candidate_payload


def score_native(raw):
    try:
        parsed = parse_native_response(raw)
        score = {'admitted': True, 'action_kind': parsed['action']['kind'], 'invalid_reason': None}
    except (ValueError, TypeError, RecursionError) as exc:
        score = {'admitted': False, 'action_kind': None, 'invalid_reason': type(exc).__name__}
    score.update(task_correctness_assessed=False, C_type_drift_assessed=False,
                 SQL_executions=0, tool_executions=0, retry=False)
    return score


class NativeFirstAdapter:
    def __init__(self, *, counter, transport, save, guard, deadline, clock=time.monotonic):
        self.counter, self.transport, self.save, self.guard = counter, transport, save, guard
        self.deadline, self.clock = deadline, clock
        self.used = False

    def __call__(self, payload):
        if self.used:
            raise RuntimeError('Single first-response slot; no retry')
        self.used = True
        try:
            return self._call(payload)
        except BaseException as exc:
            self.guard.terminal = True
            self.save('failure', {'error_type': type(exc).__name__, 'automatic_retry': False,
                                  'cost_guard': self.guard.snapshot()})
            raise

    def _call(self, payload):
        validate_candidate_payload(payload)
        receipt = self.counter.inspect(payload)
        n = receipt['reference_input_tokens']
        if type(n) is not int or not 0 < n <= 8192:
            raise ValueError('Reference input cap exceeded')
        remaining = self.deadline-self.clock()
        if remaining <= 0:
            raise TimeoutError('Deadline before dispatch')
        self.save('context', receipt)
        ticket = self.guard.reserve(n, 512)
        self.save('pending', {'payload': deepcopy(payload), 'ticket': ticket,
                             'timeout_seconds': min(90, remaining), 'automatic_retry': False})
        raw = self.transport(deepcopy(payload), min(90, remaining))
        self.save('provider', {'raw_response_text': raw})
        # Duplicate outer JSON keys also invalidate usage; retain the reservation.
        if type(raw) is not str or len(raw.encode('utf-8')) > 1024*1024:
            raise ValueError('Expected bounded raw response text')
        body = _load(raw)
        if type(body) is not dict or type(body.get('usage')) is not dict or 'error' in body:
            raise ValueError('Missing trustworthy usage; reserve retained')
        usage = body['usage']
        try:
            self.guard.settle(ticket, usage)
        finally:
            self.save('cost', self.guard.snapshot())
        if self.clock() >= self.deadline:
            raise TimeoutError('Late response retained')
        if (body.get('model') != 'deepseek-flash' or usage['prompt_tokens'] != n
                or usage['completion_tokens'] > 512):
            raise ValueError('Provider identity/reference count/output mismatch; stop')
        score = score_native(raw)
        self.save('score', score)
        return score
