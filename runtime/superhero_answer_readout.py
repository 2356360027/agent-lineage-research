"""Conservative descriptive scoring for the fixed four-cell feasibility batch.

This is a pure analysis component, not trace verification or a semantic model.
Caller must first verify the sealed reference, exact task admission, full batch
and replayed records. Never pass an in-progress run or a successful prefix.
Non-integer prose is queued for review, not interpreted by a hidden heuristic.
"""
from collections import Counter
from copy import deepcopy
import re

from runtime.cache_intent_tools import valid_result

VERSION='superhero-scalar-readout-v1'
IDS={f'available{a}-price{p}' for a in (0,1) for p in (1,8)}
INVALID={'INVALID_ACTION','INVALID_ACTION_RESPONSE','RECOVERY_ACTION_VIOLATION','TOOL_HORIZON_VIOLATION'}
UNFINISHED={'NOT_STARTED','FAILED','INTERRUPTED','ENVIRONMENT_INVALID','FINALIZATION_FAILED'}
DISPOSITIONS=('MATCH','MISMATCH','UNCERTAIN','ABSTAINED','INVALID_OUTPUT','NOT_COMPLETED')


def reference_integer(result):
    """Shape/fingerprint check only; the caller verifies archive and query binding."""
    valid_result(result)
    rows=result['rows']
    if len(result['columns'])!=1 or len(rows)!=1 or len(rows[0])!=1 or rows[0][0][0]!='integer':
        raise ValueError('Expected one complete integer reference scalar')
    value=int(rows[0][0][1])
    if value<0: raise ValueError('A count reference cannot be negative')
    return value


def disposition(status, text, expected):
    if type(expected) is not int or expected<0:
        raise ValueError('Nonnegative integer reference required, not a boolean')
    if status in UNFINISHED:
        # A retained late answer must not be promoted to a completed result.
        return dict(rating='NOT_COMPLETED',reason='Trajectory not completed',human_review_required=False)
    if status in INVALID:
        return dict(rating='INVALID_OUTPUT',reason='Recorded protocol/output violation',human_review_required=False)
    if status=='ABSTAINED':
        return dict(rating='ABSTAINED',reason='Recorded abstention',human_review_required=False)
    if status!='ANSWERED': raise ValueError('Unknown trajectory status')
    if type(text) is not str or not text.strip():
        raise ValueError('Answered trajectory is missing its original answer text')
    # ASCII scalar only, surrounding whitespace allowed. No extraction from
    # explanations, math, lists, markdown, units or a second contradictory answer.
    stripped=text.strip()
    if re.fullmatch(r'[+-]?(?:0|[1-9][0-9]{0,18})',stripped) is None:
        return dict(rating='UNCERTAIN',reason='Not a plain scalar integer; requires semantic review',human_review_required=True)
    actual=int(stripped)
    return dict(rating='MATCH' if actual==expected else 'MISMATCH',
                reason='Exact scalar integer comparison',human_review_required=False)


def summarize(verified_rows, expected):
    """All four dependent slots, no success-only denominator or C-type labels.

Rows may carry extra audit fields, which are retained verbatim in this private
output. This function must not be mistaken for an integrity or provenance gate.
"""
    if type(verified_rows) is not list or len(verified_rows)!=4:
        raise ValueError('All four planned cells required, including failures')
    if any(type(row) is not dict or type(row.get('id')) is not str for row in verified_rows):
        raise ValueError('Invalid row identity')
    if {row['id'] for row in verified_rows}!=IDS:
        raise ValueError('Missing, duplicate or unknown condition')
    result=[]
    for row in verified_rows:
        if 'final_status' not in row or 'final_answer' not in row:
            raise ValueError('Audit must supply explicit status and answer-or-None')
        rated=deepcopy(row)
        rated['automated_disposition']=disposition(row['final_status'],row['final_answer'],expected)
        result.append(rated)
    counts=Counter(row['automated_disposition']['rating'] for row in result)
    return dict(version=VERSION,status='DESCRIPTIVE_ONE_PAIR_AUTOMATED_READOUT',rows=result,
                planned_cells=4,independent_task_pairs=1,
                counts={key:counts[key] for key in DISPOSITIONS},
                human_reviews_added=0,model_calls=0,C_drift_assessed=False,
                inferential_statistics_computed=False,
                warning='UNCERTAIN is not incorrect; failures are not imputed. Exact integer matching alone does not establish intent, reliance, or method superiority.')
