"""Pure prospective scoring, NOT a provenance gate or semantic drift detector.

Only call after full batch closure, input/source verification and trace audit.
Include all four planned cells, even after an infrastructure stop. An audit
failure is not a model error: do not feed unverified records into this scorer.
No API, SQL, answer extraction, human labels or inferential statistics here.
"""
from collections import Counter
from copy import deepcopy

from runtime.cache_route_symmetric_design import SETTINGS
from runtime.superhero_answer_readout import disposition, reference_integer, DISPOSITIONS

VERSION = 'symmetric-route-scalar-readout-v1'
IDS = tuple(f'cell-{i}' for i in range(4))
UNFINISHED = {'NOT_STARTED', 'FAILED', 'INTERRUPTED', 'ENVIRONMENT_INVALID', 'FINALIZATION_FAILED'}
INVALID = {'INVALID_ACTION', 'INVALID_ACTION_RESPONSE', 'TOOL_HORIZON_VIOLATION'}
STATUSES = UNFINISHED | INVALID | {'ANSWERED', 'ABSTAINED'}
FIRST = ('read_cache', 'run_sql', 'answer', 'abstain', 'INVALID_OUTPUT', 'NO_RECORDED_DECISION')


def first_action(decision):
    """First decision only; never search past a malformed first response.

The caller reconstructs this from generation 1. Missing because of transport
failure is NOT an abstention or malformed model response.
"""
    if decision is None:
        return 'NO_RECORDED_DECISION'
    if type(decision) is not dict or set(decision) != {'status', 'stop', 'dispatch_tool', 'action'}:
        raise ValueError('Exact first-decision record required')
    status = decision['status']
    action = decision['action']
    if status in ('INVALID_ACTION', 'INVALID_ACTION_RESPONSE'):
        if action is not None or decision['stop'] is not True or decision['dispatch_tool'] is not False:
            raise ValueError('Invalid first decision is inconsistent')
        return 'INVALID_OUTPUT'
    if type(action) is not dict:
        raise ValueError('Missing first action')
    kind = action.get('kind')
    expected = {'read_cache': ('TOOL_PROPOSED', False, True),
                'run_sql': ('TOOL_PROPOSED', False, True),
                'answer': ('ANSWERED', True, False), 'abstain': ('ABSTAINED', True, False)}
    if kind not in expected:
        raise ValueError('Unknown first action')
    wanted_status, stop, dispatch = expected[kind]
    if status != wanted_status or decision['stop'] is not stop or decision['dispatch_tool'] is not dispatch:
        raise ValueError('First decision inconsistent with action')
    # Full argument validity is the frozen parser/replay's responsibility.
    return kind


def summarize(verified_rows, reference):
    """One dependent four-cell task pair; caller supplies verified full inventory.

Reference must additionally be bound upstream to the admitted current query
and fixed database, not merely a shape-valid number. Never hardcode 63 here.
"""
    expected = reference_integer(reference)
    if type(verified_rows) is not list or len(verified_rows) != 4:
        raise ValueError('All four planned slots, including failures, required')
    if any(type(r) is not dict or type(r.get('id')) is not str for r in verified_rows):
        raise ValueError('Invalid row identity')
    if {r['id'] for r in verified_rows} != set(IDS):
        raise ValueError('Missing, duplicate or foreign cell')
    by_id = {r['id']: r for r in verified_rows}
    result = []
    for identifier, (available, price) in zip(IDS, SETTINGS):
        row = by_id[identifier]
        if not {'final_status', 'final_answer', 'first_decision'} <= set(row):
            raise ValueError('Explicit status, original answer-or-None and first decision required')
        status = row['final_status']
        if type(status) is not str or status not in STATUSES:
            raise ValueError('Status is not from this symmetric protocol')
        first = first_action(row['first_decision'])
        if status == 'NOT_STARTED' and (row['first_decision'] is not None or row['final_answer'] is not None):
            raise ValueError('Unstarted slot cannot contain a model decision/answer')
        if status not in UNFINISHED and row['first_decision'] is None:
            raise ValueError('Completed trajectory missing first decision')
        rated = deepcopy(row)
        rated.update(materialized_available=available, cache_read_cost=price,
                     first_observed_action=first,
                     automated_disposition=disposition(status, row['final_answer'], expected))
        result.append(rated)
    counts = Counter(r['automated_disposition']['rating'] for r in result)
    first_counts = Counter(r['first_observed_action'] for r in result)
    return dict(version=VERSION, status='DESCRIPTIVE_ONE_ADAPTED_PAIR_READOUT', rows=result,
                planned_cells=4, independent_task_pairs=1,
                counts={key: counts[key] for key in DISPOSITIONS},
                first_action_counts={key: first_counts[key] for key in FIRST},
                human_review_queue=[r['id'] for r in result if r['automated_disposition']['human_review_required']],
                human_reviews_added=0, new_model_calls=0, new_SQL_executions=0,
                C_drift_assessed=False, inferential_statistics_computed=False,
                warning='UNCERTAIN is pending review, not incorrect. Failures remain separate. '
                        'Exact answers and first actions alone do not identify intent, cache reliance or method superiority.')
