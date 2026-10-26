"""Offline cross-replay of a completed trajectory; never executes SQL or HTTP.

Outer caller must authenticate complete archive inventory and frozen sources.
The replay uses retained backend results, not a second query. It establishes
cross-record consistency, not independent attestation of remote execution or
semantic correctness. Invalid model actions are retained as terminal outcomes.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import math
import re

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_tools import valid_result
from runtime.cache_route_symmetric_tools import PROFILE
from runtime.cache_route_symmetric_loop import ObservationBound
from runtime.student_club_diagnostic_loop import run as run_loop
from runtime.student_club_native_adapter import Adapter
from runtime import student_club_readonly as scoped
from runtime.native_trajectory_replay import equal,IncompleteTrajectory


def duration(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:
        raise ValueError('Invalid measured duration')


class CompareJournal:
    def __init__(self,records):self.records,self.used=records,set()
    def __call__(self,name,value):
        if name in self.used or name not in self.records:raise ValueError('Missing/duplicate replay journal entry')
        self.used.add(name);actual=deepcopy(self.records[name]);expected=deepcopy(value)
        if re.fullmatch(r'(owned\.)?e\d{4}',name):
            stamp=datetime.fromisoformat(actual['utc'])
            if stamp.utcoffset() is None:raise ValueError('Timestamp lacks timezone')
            expected['utc']=actual['utc']
        if re.fullmatch(r'[gt]\d{3}\.response',name):
            duration(actual['wall_seconds']);expected['wall_seconds']=actual['wall_seconds']
        if re.fullmatch(r'api-g\d{3}\.pending',name):
            timeout=actual['timeout_seconds']
            if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=90:
                raise ValueError('Invalid recorded timeout')
            expected['timeout_seconds']=timeout
        equal(actual,expected,'Replayed journal '+name)
    def complete(self):
        if self.used!=set(self.records):raise ValueError('Unconsumed extra journal entries')


def virtual_session(common,job,cache,tools,input_receipt):
    """Exact class used for state validation; instance cannot open a database."""
    s=object.__new__(scoped.ToolSession)
    s.available,s.cache_cost=job['available'],job['price']
    s.cache=deepcopy(cache);s.expected_db=common['database_sha256']
    s.budget_profile=PROFILE;s.initial_budget=s.balance=60;s.max_attempts=3
    s.attempts=s.sql_calls=0;s.closed=s.invalidated=False;s.nonbinding_for_configured_horizon=True
    s._check_database=lambda:None
    start=tools['started'];equal(start['configuration'],s.describe(),'Backend initial state')
    equal(start['input_receipt'],input_receipt,'Backend input receipt')
    equal(start['history_sql_sha256'],audit.digest(common['history_sql']),'Historical SQL binding')
    equal(start['cache_rows_sha256'],cache['sequence_sha256'],'Historical result binding')
    equal(tools['column_policy'],dict(version=scoped.VERSION,allowed_columns=scoped.policy(),
        scope='Same fixed allowlist across all cells',semantic_correctness_assessed=False),'Column policy')
    used={'started','summary','column_policy'}
    def call(name,args):
        if s.attempts>=3 or s.closed or s.invalidated:raise ValueError('Tool horizon/state violation')
        request=dict(tool=name,arguments=args)
        raw=audit.encoded(request)
        if len(raw)>audit.LIMITS['max_sql_bytes']*6+1024:
            request=dict(oversized_request_sha256=hashlib.sha256(raw).hexdigest());status,cost='INVALID_REQUEST',0
        else:status,cost=s._classify(name,args)
        n=s.attempts+1
        pending=dict(attempt=n,request=request,admission=status,charge=cost,
            balance_before=s.balance,balance_after=s.balance-cost,automatic_retry=False)
        prefix=f'attempt-{n:03d}';p=tools[prefix+'.pending'];r=tools[prefix+'.completed']
        used.update((prefix+'.pending',prefix+'.completed'))
        equal(p,pending,'Backend admission and charge')
        if set(r)!=set(pending)|{'response','private','environment_invalidated','measured_action_wall_seconds'}:
            raise ValueError('Unexpected backend completion schema')
        equal({k:r[k] for k in pending},pending,'Backend pending/completed link')
        duration(r['measured_action_wall_seconds'])
        equal(r['environment_invalidated'],False,'Backend invalidation')
        s.attempts=n;s.balance-=cost
        observation=dict(status=status,complete=False,charged_units=cost,remaining_budget=s.balance,attempt=n)
        if status=='ADMITTED':
            equal(sorted(r['private']),['backend_result'],'Admitted backend receipt')
            backend=r['private']['backend_result']
            if name=='read_cache':equal(backend,cache,'Cache not recomputed or replaced')
            else:
                s.sql_calls+=1
                if backend.get('status') not in ('HARD_TIMEOUT','WORKER_ERROR'):
                    equal(backend.get('column_policy_version'),scoped.VERSION,'Scoped SQL worker version')
                    equal(backend.get('column_policy_sha256'),audit.digest(scoped.policy()),'Scoped SQL policy hash')
            if backend.get('complete') is True:
                valid_result(backend)
                observation.update(status='EXECUTED',complete=True,columns=backend['columns'],rows=backend['rows'])
            else:
                observation['status']=backend.get('status','BACKEND_ERROR')
                if backend.get('status') in ('EXECUTION_ERROR','DENIED') and type(backend.get('error')) is str:
                    observation['error']=backend['error'][:1024]
        else:equal(r['private'],{},'No backend for rejected tool')
        equal(r['response'],observation,'Backend result/observation binding')
        return deepcopy(observation)
    s.call=call
    def finish():
        if used!=set(tools):raise ValueError('Unexpected tool records')
        close=tools['summary']
        expected=dict(status='TOOL_SESSION_CLOSED',unresolved_attempts=[],attempts=s.attempts,
            sql_calls=s.sql_calls,budget_profile=PROFILE,initial_budget=60,
            nonbinding_for_configured_horizon=True,spent_declared_units=60-s.balance,remaining_budget=s.balance,
            model_calls_by_this_component=0,semantic_approvals=0,inference_eligible=False)
        equal({k:close.get(k) for k in expected},expected,'Backend final accounting')
    return s,finish


def replay(common,job,cache,*,loop_records,api_records,tool_records,input_receipt,
           native_counter,plain_counter,guard,mode):
    if ('failure' in loop_records or 'summary' not in loop_records or 'adapter_failure' in api_records):
        raise IncompleteTrajectory('Partial/failed trajectory retained; do not extract success prefix')
    if mode not in ('SCRIPTED_NOT_MODEL','OFFICIAL_NATIVE_DEVELOPMENT'):raise ValueError('Explicit mode required')
    loop=CompareJournal(loop_records);api=CompareJournal(api_records)
    s,finish=virtual_session(common,job,cache,tool_records,input_receipt)
    calls=0
    def transport(wire,timeout):
        nonlocal calls
        calls+=1;prefix=f'api-g{calls:03d}'
        raw=api_records[prefix+'.provider']['raw_response_text']
        name=f'transport-{calls:03d}'
        if mode=='OFFICIAL_NATIVE_DEVELOPMENT' or name in api_records:
            api(name,dict(transport_status='RESPONSE_RECEIVED',response_text=raw))
        return raw
    adapter=Adapter(common=common,job=job,counter=native_counter,transport=transport,
        save=api,guard=guard,deadline=500,clock=lambda:0)
    result=run_loop(common,job,s,adapter,loop,ObservationBound(plain_counter.chat),deadline=500,clock=lambda:0)
    finish();loop.complete();api.complete()
    return dict(status='TRAJECTORY_CROSS_RECORD_REPLAY_VERIFIED',loop_status=result['status'],
        calls=calls,tool_attempts=s.attempts,cumulative_guard=guard.snapshot(),
        database_reexecuted=False,provider_invoice_verified=False,semantic_correctness_assessed=False)
