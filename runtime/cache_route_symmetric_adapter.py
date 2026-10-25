"""Injected provider adapter for the symmetric workflow; no HTTP/key handling.

The launcher owns approval, source/input freeze, verified prices and hard worker
timeout. This adapter retains raw responses before validation, checks reference
counts against reported usage, and makes every exceptional outcome terminal for
the shared batch guard. No repair, retry, token fallback or response trimming.
"""
from copy import deepcopy
import math
import time

from runtime.cache_action_preview import payload as make_payload
from runtime.cache_route_symmetric_capacity import SymmetricCapacity
from runtime.cache_route_symmetric_design import classify
from runtime.bird_readonly_audit import digest
from runtime.deepseek_trajectory_adapter import CostGuard, MODEL


class SymmetricAdapter:
    def __init__(self, *, counter, capacity, transport, save, guard, deadline, clock=time.monotonic):
        if not isinstance(capacity, SymmetricCapacity) or capacity.counter is not counter:
            raise ValueError('Matching symmetric capacity and reference counter required')
        if not isinstance(guard, CostGuard):
            raise TypeError('Explicit shared cost guard required')
        if (type(deadline) not in (int,float) or not math.isfinite(deadline)
                or not 0 < deadline-clock() <= 600):
            raise ValueError('Finite deadline within ten minutes required')
        self.counter, self.capacity, self.transport = counter, capacity, transport
        self.save, self.guard, self.deadline, self.clock = save, guard, deadline, clock
        self.count, self.terminal = 0, False
        self.previous_messages, self.previous_content = None, None

    def __call__(self, request):
        if self.terminal or self.count >= 4:
            raise RuntimeError('Terminal adapter; no retry')
        try:
            return self._call(request)
        except BaseException:
            self.terminal = True
            self.guard.terminal = True
            raise

    def _call(self, request):
        index = self.count+1
        stage = 'terminal' if index == 4 else 'symmetric_tool_opportunity'
        if (type(request) is not dict or set(request) != {'generation_id','stage','messages'}
                or request['generation_id'] != f'g{index:03d}' or request['stage'] != stage):
            raise ValueError('Generation identity or stage changed')
        payload = make_payload(request['messages'])
        if self.previous_messages is not None:
            size = len(self.previous_messages)
            if (payload['messages'][:size] != self.previous_messages
                    or len(payload['messages']) <= size
                    or payload['messages'][size] != dict(role='assistant',content=self.previous_content)):
                raise ValueError('Previous messages or raw assistant output changed')
        receipt = self.capacity(payload)
        if receipt['generation_index'] != index:
            raise ValueError('History does not match generation counter')
        name = f'api-g{index:03d}'
        self.save(name+'.capacity', receipt)
        n = receipt['reference_input_tokens']
        remaining = self.deadline-self.clock()
        if remaining <= 0: raise TimeoutError('Batch deadline before dispatch')
        ticket = self.guard.reserve(n, 512)
        self.save(name+'.pending', dict(payload=payload, payload_sha256=digest(payload),
            ticket=ticket, timeout_seconds=min(90,remaining), automatic_retry=False))
        self.count = index
        started = self.clock()
        try:
            raw = self.transport(deepcopy(payload), min(90,remaining))
        except BaseException as exc:
            self.save(name+'.failure', dict(error_type=type(exc).__name__, billing_unknown=True,
                                            automatic_retry=False, guard=self.guard.snapshot()))
            raise
        self.save(name+'.provider', dict(raw=deepcopy(raw), wall_seconds=self.clock()-started))
        usage = raw.get('usage') if type(raw) is dict else None
        if type(usage) is not dict:
            raise ValueError('Missing usage; billing remains uncertain')
        try:
            self.guard.settle(ticket, usage)
        finally:
            self.save(name+'.cost', self.guard.snapshot())
        if self.clock() >= self.deadline:
            raise TimeoutError('Late result retained; stop batch')
        if raw.get('model') != MODEL or usage['prompt_tokens'] != n or usage['completion_tokens'] > 512:
            raise ValueError('Provider alias, input count or output cap mismatch')
        choices = raw.get('choices')
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ValueError('Expected exactly one provider choice')
        message = choices[0].get('message')
        if (type(message) is not dict or type(message.get('content')) is not str
                or any(message.get(k) for k in ('reasoning_content','tool_calls','function_call','refusal'))):
            raise ValueError('Unsupported provider output')
        content = message['content']
        count = self.counter.text(content)
        if type(count) is not int or not 0 <= count <= 512:
            raise ValueError('Actual response exceeds local history cap')
        finish = choices[0].get('finish_reason')
        if finish != 'stop' or classify(content,index)['stop']:
            self.terminal = True
        self.previous_messages = deepcopy(payload['messages'])
        self.previous_content = content
        return dict(content=content, finish_reason=finish, usage=deepcopy(usage),
                    provider_record=name+'.provider', request_sha256=digest(payload))
