"""Offline construction of a paired first-action interface diagnostic.

No transport, SQL executor, expected action or substantive task grader.
The inputs are every first request from two already-used development pairs.
"""
from copy import deepcopy
import hashlib
import json

from runtime.action_encoding_calibration import SEPARATED
from runtime.cache_intent_staged_loop import ACTION_INSTRUCTION, parse_action
from runtime.deepseek_official_transport import validate_payload

VERSION = 'research-context-interface-draft-v1'
SEED = 'full-context-paired-format-20261025-v1'
CONTEXT_IDS = tuple(f'{task}-cell-{i}' for task in ('positive', 'strength') for i in range(4))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def build_plan(requests):
    if type(requests) is not dict or set(requests) != set(CONTEXT_IDS):
        raise ValueError('All eight prespecified contexts are required')
    jobs = []
    order = sorted(CONTEXT_IDS, key=lambda ident: hashlib.sha256((SEED+ident).encode()).hexdigest())
    for index, ident in enumerate(order):
        original = requests[ident]
        validate_payload(original)
        messages = original['messages']
        if (len(messages) != 2 or [m['role'] for m in messages] != ['system', 'user']
                or messages[0]['content'].count(ACTION_INSTRUCTION) != 1
                or ACTION_INSTRUCTION in messages[1]['content']
                or original['max_tokens'] != 512):
            raise ValueError('Expected one canonical instruction in a first-request system message')
        variant = deepcopy(original)
        variant['messages'][0]['content'] = messages[0]['content'].replace(ACTION_INSTRUCTION, SEPARATED)
        # A reverse substitution must recover the complete original payload.
        restored = deepcopy(variant)
        restored['messages'][0]['content'] = restored['messages'][0]['content'].replace(SEPARATED, ACTION_INSTRUCTION)
        if restored != original:
            raise ValueError('Non-local presentation change')
        arms = ('original', 'separated') if index % 2 == 0 else ('separated', 'original')
        for arm in arms:
            payload = deepcopy(original if arm == 'original' else variant)
            validate_payload(payload)
            jobs.append(dict(id=ident+'-'+arm, context_id=ident, arm=arm,
                             source_payload_sha256=digest(original), payload=payload))
    return dict(version=VERSION, seed=SEED, jobs=jobs, maximum_calls=16,
                development_task_pairs=2, dependent_contexts=8, SQL_calls=0,
                tool_calls=0, inference_authorized=False, executable_launcher_available=False)


def score_format(content, finish_reason):
    """No selected action is treated as the correct action for this diagnostic."""
    if type(content) is not str:
        raise ValueError('Preserved text required')
    try:
        json.loads(content)
        single = True
    except ValueError:
        single = False
    try:
        parse_action(content)
        schema = True
    except (ValueError, TypeError):
        schema = False
    return dict(single_json=single, action_schema_valid=schema,
                normal_finish=finish_reason == 'stop', admitted=schema and finish_reason == 'stop',
                task_correctness_assessed=False, C_type_drift_assessed=False)
