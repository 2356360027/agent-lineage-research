"""Owned-history accounting adapter with injected transport; no live launcher.

Caller owns authentic inputs, approved transport, shared cost guard, hard
deadline and durable journal. Tool observations supplied by the audited loop
are bound here, not independently executed/attested. No SQL, network or secrets.
"""
from copy import deepcopy
import math
import time

from runtime.bird_readonly_audit import digest
from runtime.native_action_interface import _load
from runtime.student_club_definition_history import DefinitionHistory
from runtime.student_club_diagnostic_design import canonical
from runtime.student_club_native_wire import payload, MODEL, MAX_OUTPUT

VERSION = 'student-club-native-accounting-adapter-v1'
MAX_INPUT = 8192


class Adapter:
    def __init__(self, *, common, job, counter, transport, save, guard, deadline, clock=time.monotonic):
        if type(deadline) not in (int,float) or not math.isfinite(deadline):
            raise ValueError('Finite deadline required')
        self.counter,self.transport,self.save,self.guard = counter,transport,save,guard
        self.deadline,self.clock = deadline,clock
        self.count,self.closed = 0,False
        self.history = DefinitionHistory(common,job,lambda name,value:save('owned.'+name,value))
        self.maximum = job['max_model_calls']

    def __call__(self, request):
        if self.closed or self.count >= self.maximum or self.guard.terminal:
            raise RuntimeError('Terminal adapter; no retry')
        try:
            return self._call(request)
        except BaseException as error:
            self.closed = True
            self.guard.terminal = True
            self.save('adapter_failure',dict(error_type=type(error).__name__,automatic_retry=False,
                guard=self.guard.snapshot(),dispatch_attempts=self.count))
            raise

    def _call(self, request):
        wire = payload(request)  # Structural checks before touching history.
        if self.history.state == 'AWAIT_OBSERVATION':
            observation = request['messages'][-1]
            self.history.observe(observation['tool_call_id'],observation['content'])
        expected = self.history.begin()
        if canonical(request) != canonical(expected):
            raise ValueError('Request does not preserve this adapter\'s own received history')
        receipt = self.counter.inspect(request)
        n = receipt['reference_input_tokens']
        if (type(n) is not int or not 0 < n <= MAX_INPUT
                or receipt['wire_payload_sha256'] != digest(wire)):
            raise ValueError('Context cap or counted-wire binding mismatch; no truncation')
        remaining = self.deadline-self.clock()
        if remaining <= 0:raise TimeoutError('Deadline before reservation')
        name=f'api-g{self.count+1:03d}'
        self.save(name+'.context',receipt)
        ticket=self.guard.reserve(n,MAX_OUTPUT)
        self.save(name+'.pending',dict(payload=deepcopy(wire),ticket=ticket,
            timeout_seconds=min(90,remaining),automatic_retry=False))
        remaining=self.deadline-self.clock()
        if remaining <= 0:raise TimeoutError('Deadline after pending write; not dispatched')
        self.count+=1
        raw=self.transport(deepcopy(wire),min(90,remaining))
        self.save(name+'.provider',dict(raw_response_text=raw))
        if type(raw) is not str or len(raw.encode()) > 1048576:
            raise ValueError('Bounded raw response required')
        body=_load(raw)
        if type(body) is not dict or 'error' in body or type(body.get('usage')) is not dict:
            raise ValueError('Trustworthy usage missing; retain uncertain reservation')
        usage=body['usage']
        try:
            self.guard.settle(ticket,usage)
        finally:
            self.save(name+'.cost',self.guard.snapshot())
        if self.clock() >= self.deadline:raise TimeoutError('Late response accounted; stop')
        if (body.get('model') != MODEL or usage['prompt_tokens'] != n
                or usage['completion_tokens'] > MAX_OUTPUT):
            raise ValueError('Provider identity/count/output mismatch; no offset fitting')
        try:
            self.history.accept(raw)
            admitted=True
        except ValueError:
            if self.history.state != 'INVALID_RESPONSE':raise
            admitted=False
        self.closed = self.history.state in ('TERMINAL','INVALID_RESPONSE')
        self.save(name+'.admission',dict(format_admitted=admitted,history_state=self.history.state,
            semantic_correctness_assessed=False,execution_authorized=False))
        return raw
