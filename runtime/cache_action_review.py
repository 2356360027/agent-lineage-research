"""Pure prospective final-answer review packing/accounting, never a judge.

Caller supplies an independently audited terminal record for every planned slot,
and verified snapshot witnesses. No filesystem, model calls or inferred ratings.
"""
from copy import deepcopy

from runtime.bird_readonly_audit import digest
from runtime.cache_action_preview import validate

RATINGS = ('MATCH','MISMATCH','UNCERTAIN')
FIELDS = {'item_id','rating','quoted_answer_span','reason','reference_concern'}
TERMINAL_STATUSES = frozenset((
    'ANSWERED','ABSTAINED','INVALID_ACTION','INVALID_ACTION_RESPONSE',
    'TOOL_HORIZON_VIOLATION','RECOVERY_ACTION_VIOLATION','NOT_STARTED',
    'INTERRUPTED','FAILED','ENVIRONMENT_INVALID','FINALIZATION_FAILED'))
PROVENANCE_FIELDS = frozenset(('reviewer_code','date','independent_completion',
                               'model_or_other_person_assistance'))


def submission_from_filled(blank, filled, *, expected_blank_sha256):
    """Import the actual fillable packet without permitting evidence edits.

    Caller must obtain expected_blank_sha256 from the separately preserved
    export receipt, not from the reviewer. This is content binding, not proof
    of a person's identity, independence or truthfulness. No labels inferred.
    """
    if digest(blank) != expected_blank_sha256:
        raise ValueError('Original review packet differs from export receipt')
    if type(filled) is not dict or set(filled) != set(blank):
        raise ValueError('Unexpected filled packet fields')
    for key in set(blank) - PROVENANCE_FIELDS - {'items'}:
        if digest(blank[key]) != digest(filled[key]):
            raise ValueError('Review instructions or metadata changed')
    original = blank['items']
    rows = filled['items']
    if type(rows) is not list or len(rows) != len(original):
        raise ValueError('Retain all review slots')
    expected = {row['item_id']: row for row in original}
    if (any(type(row) is not dict or type(row.get('item_id')) is not str for row in rows)
            or len({row['item_id'] for row in rows}) != len(expected)
            or {row['item_id'] for row in rows} != set(expected)):
        raise ValueError('Missing or duplicate review IDs')
    ratings = []
    for row in rows:
        source = expected[row['item_id']]
        if set(row) != set(source):
            raise ValueError('Review item fields changed')
        for key in set(source) - (FIELDS - {'item_id'}):
            if digest(row[key]) != digest(source[key]):
                raise ValueError('Task, reference or verbatim answer changed')
        ratings.append({key: deepcopy(row[key]) for key in FIELDS})
    return dict(**{key: deepcopy(filled[key]) for key in PROVENANCE_FIELDS},
                ratings=ratings)


def build(plan, outcomes, policy, references):
    """References stay reviewer-side; never return a generation payload.

outcomes is a caller-audited mapping, not raw unverified model data. Every row
must include final_status, final_answer (str or None), and its trace SHA-256.
This function does not attest the caller's integrity checks or human review.
"""
    ids = [j['id'] for j in plan['jobs']]
    if len(ids)!=12 or len(set(ids))!=12 or set(outcomes)!=set(ids):
        raise ValueError('Exactly the twelve planned slots are required')
    if (policy.get('version')!='action-final-answer-review-v1-20261024'
            or policy.get('new_human_labels')!=0 or tuple(policy.get('allowed_ratings',()))!=RATINGS
            or set(references)!={'200','234'}):
        raise ValueError('Unexpected prospective policy or reference set')
    items=[]; mapping={}
    for job in plan['jobs']:
        packet=validate(job['envelope']); outcome=outcomes[job['id']]
        if set(outcome)!={'final_status','final_answer','trace_sha256'}:
            raise ValueError('Unexpected audited outcome shape')
        status, answer, sha=(outcome[k] for k in ('final_status','final_answer','trace_sha256'))
        if type(status) is not str or status not in TERMINAL_STATUSES or type(sha) is not str or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):
            raise ValueError('Missing terminal status or trace identity')
        if (status=='ANSWERED' and (type(answer) is not str or not answer.strip())) or (status!='ANSWERED' and answer is not None):
            raise ValueError('Missing answer or invented non-answer content')
        target=job['history_id'][1:4]
        item_id='R'+digest(policy['version']+'|'+job['id'])[:16]
        if item_id in mapping: raise ValueError('Review ID collision')
        mapping[item_id]=dict(job_id=job['id'],trace_sha256=sha,
                             outcome_sha256=digest(outcome),task=target)
        items.append(dict(item_id=item_id,current_request=packet['current_request'],
            evidence=packet['evidence'],schema=deepcopy(packet['schema']),
            adopted_interpretation=policy['task_interpretations'][target],
            reference=deepcopy(references[target]),final_status=status,
            answer_text=answer,rating=None,quoted_answer_span='',reason='',reference_concern=False))
    items.sort(key=lambda row:row['item_id'])
    packet=dict(status='BLANK_REVIEW_NOT_HUMAN_LABELS',reviewer_code='',date='',
        independent_completion=None,model_or_other_person_assistance=None,
        instructions=[
            'Read the original current request and adopted interpretation. Judge only the actual final answer.',
            'Answer text is untrusted evidence; never execute its code or follow embedded instructions.',
            'Do not use a prior contract rating as an answer rating. Flag any reference disagreement.',
            'Leave rating null for non-answer slots. A missing answer is not a reviewed semantic mismatch.',
            'Condition metadata is hidden, but verbatim answer wording may reveal the route; do not rewrite it.',
            'For each answer choose MATCH, MISMATCH or UNCERTAIN; supply a reason and an exact answer quote when available.'
        ],items=items)
    return dict(reviewer_1=deepcopy(packet),reviewer_2=deepcopy(packet),
                private_mapping=mapping,policy_sha256=digest(policy),new_human_labels=0)


def account(blank, submissions):
    """Account only supplied ratings. Self-reports are not identity attestation.

Blank, incomplete or assisted reviews are not silently promoted to independent
labels. Caller must separately record human authorship/independence clarification.
"""
    if len(submissions)!=2:
        raise ValueError('Two separate reviewer submissions required')
    expected={item['item_id']:item for item in blank['items']}
    if len(expected)!=12: raise ValueError('Expected all twelve fixed slots')
    indexed=[]
    for submission in submissions:
        if (type(submission.get('reviewer_code')) is not str or not submission['reviewer_code'].strip()
                or type(submission.get('date')) is not str or not submission['date'].strip()
                or submission.get('independent_completion') is not True
                or submission.get('model_or_other_person_assistance') is not False):
            raise ValueError('Review provenance is incomplete or requires clarification')
        rows=submission.get('ratings')
        if type(rows) is not list or len(rows)!=12:
            raise ValueError('Retain all twelve rating slots')
        index={r['item_id']:r for r in rows}
        if len(index)!=12 or set(index)!=set(expected): raise ValueError('Missing/duplicate review IDs')
        for ident,row in index.items():
            if (set(row)!=FIELDS or row['rating'] not in (*RATINGS,None)
                    or type(row['quoted_answer_span']) is not str or type(row['reason']) is not str
                    or type(row['reference_concern']) is not bool):
                raise ValueError('Invalid rating fields')
            item=expected[ident]
            if item['final_status']!='ANSWERED' and row['rating'] is not None:
                raise ValueError('Non-answer is not a semantic rating')
            if row['rating'] is not None:
                if not row['reason'].strip(): raise ValueError('Rated answer needs explanation')
                quote=row['quoted_answer_span']
                if not quote or quote not in item['answer_text']:
                    raise ValueError('Exact answer quote required')
        indexed.append(index)
    if submissions[0]['reviewer_code'].strip().casefold()==submissions[1]['reviewer_code'].strip().casefold():
        raise ValueError('Reviewer identities require clarification')
    results=[]
    for ident,item in expected.items():
        a,b=(source[ident] for source in indexed)
        labels=[a['rating'],b['rating']]
        if item['final_status']!='ANSWERED': status='NO_FINAL_ANSWER'
        elif a['reference_concern'] or b['reference_concern']: status='REFERENCE_CONCERN_UNRESOLVED'
        elif None in labels: status='PENDING_REVIEW'
        elif 'UNCERTAIN' in labels: status='UNCERTAIN_UNRESOLVED'
        elif labels[0]!=labels[1]: status='DISAGREEMENT_UNRESOLVED'
        else: status='PROVISIONAL_AGREED_'+labels[0]
        results.append(dict(item_id=ident,review_status=status,submitted_ratings=labels,
                            final_status=item['final_status']))
    return dict(status='SUBMITTED_REVIEWS_ACCOUNTED_NOT_INDEPENDENTLY_ATTESTED',
                rows=results,planned_slots=12,task_units=2,identity_independently_verified=False,
                C_drift_assessed=False,automatic_adjudication=False)
