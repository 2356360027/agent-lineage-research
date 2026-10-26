"""Pure event replay from original outputs, not semantic or billing approval.

Caller must separately authenticate archive/source/API/tool bindings. Replaying
these events establishes visible ordering, not successful SQL execution, human
intent, or authenticity of a self-authored hash. No model or database calls.
"""
from copy import deepcopy
from datetime import datetime
import re

from runtime.student_club_definition_history import DefinitionHistory
from runtime.student_club_diagnostic_design import canonical


class IncompleteHistory(ValueError):
    pass


def replay(records,common,job):
    if type(records) is not dict or not records:raise ValueError('Event map required')
    keys=sorted(records)
    if keys!=[f'e{i:04d}' for i in range(1,len(keys)+1)]:raise ValueError('Continuous exact event sequence required')
    original=[records[k] for k in keys]
    for key,record in zip(keys,original):
        if (type(record) is not dict or set(record)!={'event_id','kind','job_id','utc','value'}
                or record['event_id']!=key or record['job_id']!=job['id']
                or type(record['utc']) is not str):raise ValueError('Event envelope differs')
        stamp=datetime.fromisoformat(record['utc'])
        if stamp.utcoffset() is None:raise ValueError('Timezone-bearing timestamp required')
    generated=[]
    h=DefinitionHistory(common,job,lambda name,value:generated.append(value))
    checked=0
    def check():
        nonlocal checked
        if len(generated)>len(original):raise ValueError('Missing derived events')
        while checked<len(generated):
            a,b=generated[checked],original[checked]
            if canonical({k:v for k,v in a.items() if k!='utc'})!=canonical({k:v for k,v in b.items() if k!='utc'}):
                raise ValueError('Raw-output replay differs from recorded event')
            checked+=1
    check()
    while checked<len(original):
        record=original[checked];value=record['value'];kind=record['kind']
        if kind=='generation_requested':h.begin()
        elif kind=='raw_response':
            try:h.accept(value['raw'])
            except ValueError:
                if h.state!='INVALID_RESPONSE':raise
        elif kind=='tool_observation':h.observe(value['call_id'],value['content'])
        elif kind=='external_failure':h.fail(value['reason'])
        else:raise ValueError('Unexpected independently supplied derived event')
        check()
    if h.state not in ('TERMINAL','INVALID_RESPONSE'):
        raise IncompleteHistory('Incomplete/failed history retained, not scored as success')
    return dict(status='EVENT_REPLAY_ONLY',state=h.state,events=deepcopy(original),
        generations=h.generations,tool_observations=h.observations,
        semantic_labels_generated=False,archive_authentication_verified=False,
        sql_execution_verified=False,api_accounting_verified=False)


def event_records(journal):
    """Select events from a loop journal; does not authenticate other records."""
    return {k:v for k,v in journal.items() if re.fullmatch(r'e\d{4}',k)}
