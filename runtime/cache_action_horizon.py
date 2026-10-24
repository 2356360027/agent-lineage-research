"""Offline candidate protocol, not an authorized runner or inference client.

Two exploration tool opportunities, one reserved SQL, then terminal output.
No contract generation. A future runner must journal before dispatch, preserve
invalid responses, enforce the matching tool budget and stop on any failure.
"""
from runtime.cache_intent_staged_loop import parse_action

VERSION = 'action-first-short-recovery-v1'
MAX_GENERATIONS = 4
MAX_TOOL_ATTEMPTS = 3
EXPLORATION_UNITS = 20
RECOVERY_UNITS = 10

SCHEDULE_TEXT = '''You may answer or abstain at any turn. There are at most two
exploration tool attempts with 20 declared cost units in total. Then there is
one reserved opportunity for a read-only SQL query with 10 separate cost units;
cache reads are not allowed at that opportunity. After it, only answer or abstain
is allowed. Failed or rejected tool attempts also consume an opportunity.
No reference solution or task contract is supplied. Follow the original request.'''


def phase(generation):
    if type(generation) is not int or not 1 <= generation <= MAX_GENERATIONS:
        raise ValueError('Generation outside fixed horizon')
    return 'exploration' if generation < 3 else 'recovery' if generation == 3 else 'terminal'


def classify(raw, generation):
    """Syntactic/protocol disposition only, never task correctness or intent.

The caller stops on stop=True, and never dispatches a tool for such records.
Caller failures and non-stop provider finish reasons must stop before this
function. Invalid text must be retained separately, not repaired here.
"""
    stage = phase(generation)
    try:
        action = parse_action(raw)
    except (ValueError, TypeError):
        return dict(status='INVALID_ACTION', stop=True, dispatch_tool=False, action=None)
    kind = action['kind']
    if kind in ('answer', 'abstain'):
        return dict(status='ANSWERED' if kind == 'answer' else 'ABSTAINED',
                    stop=True, dispatch_tool=False, action=action)
    violation = (stage == 'terminal' or (stage == 'recovery' and kind != 'run_sql'))
    if violation:
        return dict(status='TOOL_HORIZON_VIOLATION' if stage == 'terminal' else 'RECOVERY_ACTION_VIOLATION',
                    stop=True, dispatch_tool=False, action=action)
    return dict(status='TOOL_PROPOSED', stop=False, dispatch_tool=True, action=action)
