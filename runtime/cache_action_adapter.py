"""Injected official-provider protocol checks; no network or credential code.

Caller supplies a verified counter, shared approved CostGuard and transport.
This is not authorization to dispatch or a provider-enforced spending cap.
"""
from copy import deepcopy
import time

from runtime import cache_action_preview as preview
from runtime.cache_action_capacity import ActionCapacity
from runtime.cache_action_horizon import phase, classify
from runtime.bird_readonly_audit import digest
from runtime.deepseek_trajectory_adapter import MODEL


class ActionAdapter:
    def __init__(self, *, counter, capacity, transport, save, guard, deadline, clock=time.monotonic):
        if not isinstance(capacity, ActionCapacity) or capacity.counter is not counter:
            raise ValueError('Matching capacity and reference counter required')
        remaining = deadline-clock()
        if not 0 < remaining <= 600:
            raise ValueError('Batch deadline must be within ten minutes')
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
        if (type(request) is not dict or set(request) != {'generation_id','stage','messages'}
                or request['generation_id'] != f'g{index:03d}' or request['stage'] != phase(index)):
            raise ValueError('Generation identity or stage changed')
        payload = preview.payload(request['messages'])
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
            raise TimeoutError('Late result preserved; stop batch')
        if (raw.get('model') != MODEL or usage['prompt_tokens'] != n or usage['completion_tokens'] > 512):
            raise ValueError('Provider alias, input count or output cap mismatch')
        choices = raw.get('choices')
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ValueError('Expected exactly one provider choice')
        message = choices[0].get('message')
        if (type(message) is not dict or type(message.get('content')) is not str
                or any(message.get(k) for k in ('reasoning_content','tool_calls','function_call','refusal'))):
            raise ValueError('Unsupported provider output')
        content = message['content']
        if self.counter.text(content) > 512:
            raise ValueError('Actual response exceeds local history cap')
        finish = choices[0].get('finish_reason')
        if finish != 'stop' or classify(content,index)['stop']:
            self.terminal = True
        self.previous_messages = deepcopy(payload['messages'])
        self.previous_content = content
        return dict(content=content, finish_reason=finish, usage=deepcopy(usage),
                    provider_record=name+'.provider', request_sha256=digest(payload))
