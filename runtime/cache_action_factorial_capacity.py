"""Conditional local token bounds for the new ordinary factorial envelopes.

Reuses unchanged short-loop payload/history/observation checks. This does not
authorize inference or attest the remote service's tokenizer or model weights.
"""
from runtime import cache_action_factorial_preview as draft
from runtime import cache_action_preview as old
from runtime.cache_intent_prompts import _expanded, _json
from runtime.cache_action_capacity import ActionCapacity, JOIN_ALLOWANCE
from runtime.cache_action_loop import RECOVERY, TERMINAL


def validate_cell(envelope):
    packet = _expanded(envelope)['messages'][1]['content']
    try:
        cells = draft.make_cells(packet['current_request'], packet['evidence'],
                                 packet['schema'], packet['history'],
                                 packet['context']['database_sha256'])
    except (KeyError, TypeError) as exc:
        raise ValueError('Missing factorial input') from exc
    if _json(envelope) not in [_json(c) for c in cells]:
        raise ValueError('Noncanonical factorial cell')
    return packet


class FactorialCapacity(ActionCapacity):
    def __init__(self, envelope, counter):
        validate_cell(envelope)
        self.counter = counter
        # Transport encoding/settings are deliberately unchanged; envelope
        # admission above replaces the old availability-only validator.
        self.initial = old.payload(envelope['messages'])['messages']
        self.base = counter.chat(self.initial)
        self.fixed = (RECOVERY, TERMINAL)
        self.fixed_tokens = [counter.text(s) for s in self.fixed]
        if type(self.base) is not int or self.base <= 0:
            raise ValueError('Invalid initial token count')
        if self.input_bound(4) + 512 > 32768:
            raise ValueError('Full conditional horizon exceeds local cap')

    def certificate(self):
        value = super().certificate()
        value.update(protocol=draft.VERSION, join_allowance_each=JOIN_ALLOWANCE,
                     inference_authorized=False)
        return value
