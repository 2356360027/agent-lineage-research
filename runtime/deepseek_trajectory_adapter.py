"""Injected DeepSeek trajectory adapter. NO HTTP, credentials, or live launcher.

Preflight uses a candidate reference counter; every observed provider count must
match before continuing. Limits are local guards, not a provider-side spending
cap or proof that the full 23-call history always fits. Failed calls are terminal
and uncertainty in billing remains reserved. Scripted tests are not experiments.
"""
from copy import deepcopy
import json
import time

from runtime.bird_readonly_audit import digest
from runtime.cache_intent_prompts import _DESCRIPTIONS

MODEL = 'deepseek-flash'
SCHEMA_SUFFIX = ('\nProvider adaptation v2: For Stage 1 task-contract output ONLY, '
    'return exactly the following eight keys, each with a nonempty string value. '
    'These are field descriptions, not task answers. In subsequent stages follow '
    'the requested action schema instead.\n' +
    json.dumps(_DESCRIPTIONS, ensure_ascii=False, sort_keys=True))


def make_payload(messages):
    copied = deepcopy(messages)
    if not copied or copied[0].get('role') != 'system':
        raise ValueError('Missing system instruction')
    copied[0]['content'] += SCHEMA_SUFFIX
    return {'model': MODEL, 'messages': copied, 'thinking': {'type':'disabled'},
        'temperature':0, 'max_tokens':512, 'stream':False,
        'response_format':{'type':'json_object'}}


class CostGuard:
    """Shared integer micro-CNY ledger at explicitly supplied conservative rates.

Rates are micro-CNY/token (numerically CNY/million tokens). Never assume that
cache hits or off-peak discounts apply. A transport failure retains its reserve.
Not a provider invoice; prices and accounting must be approved before live use.
"""
    def __init__(self, *, ceiling_micro_cny, max_calls, input_rate, output_rate):
        if any(type(x) is not int or x <= 0 for x in
               (ceiling_micro_cny, max_calls, input_rate, output_rate)):
            raise ValueError('Explicit positive integer budget and rates required')
        self.ceiling, self.max_calls = ceiling_micro_cny, max_calls
        self.input_rate, self.output_rate = input_rate, output_rate
        self.calls, self.accounted, self.uncertain, self.terminal = 0, 0, {}, False

    def reserve(self, input_tokens, output_cap):
        if any(type(x) is not int or x <= 0 for x in (input_tokens, output_cap)):
            raise ValueError('Invalid token reservation')
        amount = input_tokens*self.input_rate + output_cap*self.output_rate
        if (self.terminal or self.calls >= self.max_calls
                or self.accounted + sum(self.uncertain.values()) + amount > self.ceiling):
            raise RuntimeError('Cost/call guard prevents dispatch')
        self.calls += 1
        self.uncertain[self.calls] = amount
        return self.calls

    def settle(self, ticket, usage):
        if ticket not in self.uncertain:
            raise ValueError('Unknown or settled ticket')
        for key in ('prompt_tokens', 'completion_tokens'):
            if type(usage.get(key)) is not int or usage[key] < 0:
                raise ValueError('Invalid usage; keep billing uncertain')
        amount = usage['prompt_tokens']*self.input_rate + usage['completion_tokens']*self.output_rate
        reserved = self.uncertain.pop(ticket)
        self.accounted += amount
        if amount > reserved or self.accounted > self.ceiling:
            self.terminal = True
            raise ValueError('Usage exceeded reference reservation; stop after recording')

    def snapshot(self):
        return {'attempts':self.calls, 'accounted_micro_cny':self.accounted,
            'uncertain_reserved_micro_cny':sum(self.uncertain.values()),
            'ceiling_micro_cny':self.ceiling, 'provider_invoice_verified':False,
            'provider_spending_cap_installed':False, 'terminal':self.terminal}


class Adapter:
    def __init__(self, *, counter, transport, save, guard, capacity=None, max_seconds=1800,
                 clock=time.monotonic):
        if type(max_seconds) is not int or not 1 <= max_seconds <= 1800:
            raise ValueError('Deadline must be within 1800 seconds')
        self.counter, self.transport, self.save, self.guard = counter, transport, save, guard
        self.capacity = capacity
        self.clock, self.start, self.max_seconds = clock, clock(), max_seconds
        self.count, self.failed = 0, False

    def __call__(self, request):
        if self.failed or self.count >= 23:
            raise RuntimeError('Terminal adapter; no retry')
        try:
            return self._call(request)
        except BaseException:
            self.failed = True
            self.guard.terminal = True
            raise

    def _call(self, request):
        index = self.count + 1
        stage = 'contract' if index == 1 else 'recovery' if index == 22 else 'terminal' if index == 23 else 'action'
        if (type(request) is not dict or set(request) != {'generation_id','stage','messages','contract_event'}
                or request['generation_id'] != f'g{index:03d}' or request['stage'] != stage
                or request['contract_event'] != (None if index == 1 else 'g001')):
            raise ValueError('Generation identity/stage mismatch')
        payload = make_payload(request['messages'])
        n = self.counter.chat(payload['messages'])
        if type(n) is not int or n <= 0 or n + 512 > 32768:
            raise ValueError('Full prompt does not fit local admission cap; never truncate')
        remaining = self.max_seconds - (self.clock() - self.start)
        if remaining <= 0:
            raise TimeoutError('Deadline before dispatch')
        name = f'api-g{index:03d}'
        self.save(name+'.context', {'payload_sha256':digest(payload),
            'reference_input_tokens':n, 'reserved_output_tokens':512,
            'local_admission_cap':32768, 'truncated':False,
            'full_horizon_capacity_proven':False})
        if self.capacity is not None:
            receipt = self.capacity(payload)
            self.save(name+'.capacity', receipt)
            if receipt['generation_index'] != index:
                raise ValueError('Capacity stage does not match call index')
        # Reserve only after pure validation; no transport occurs if save fails.
        ticket = self.guard.reserve(n, 512)
        self.save(name+'.pending', {'payload':payload, 'ticket':ticket,
            'automatic_retry':False, 'timeout_seconds':min(90, remaining)})
        self.count = index
        try:
            raw = self.transport(deepcopy(payload), min(90, remaining))
        except BaseException as exc:
            self.save(name+'.failure', {'error_type':type(exc).__name__,
                'billing_unknown':True, 'automatic_retry':False, 'guard':self.guard.snapshot()})
            raise
        self.save(name+'.provider', {'raw':deepcopy(raw)})
        usage = raw.get('usage') if type(raw) is dict else None
        if type(usage) is not dict:
            raise ValueError('Missing usage; billing remains uncertain')
        try:
            self.guard.settle(ticket, usage)
        finally:
            self.save(name+'.cost', self.guard.snapshot())
        if self.clock() - self.start >= self.max_seconds:
            raise TimeoutError('Late result retained; stop')
        if raw.get('model') != MODEL or usage['prompt_tokens'] != n:
            raise ValueError('Provider model/count mismatch; preserve raw result and stop')
        if usage['completion_tokens'] > 512:
            raise ValueError('Provider output exceeds fixed cap')
        choices = raw.get('choices')
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ValueError('Expected one choice')
        message = choices[0].get('message')
        if (type(message) is not dict or type(message.get('content')) is not str
                or any(message.get(k) for k in ('reasoning_content','tool_calls','function_call','refusal'))):
            raise ValueError('Unsupported output')
        content = message['content']
        if self.counter.text(content) > 512:
            raise ValueError('Retokenized output exceeds local history cap; no repair')
        finish = choices[0].get('finish_reason')
        if finish != 'stop':
            self.failed = True
        return {'content':content, 'finish_reason':finish, 'usage':deepcopy(usage),
            'provider_model':raw['model'], 'provider_record':name+'.provider',
            'request_sha256':digest(payload)}
