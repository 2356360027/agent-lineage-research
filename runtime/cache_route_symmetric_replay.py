"""Offline replay of a completed symmetric trajectory, not a batch admission.

Caller MUST verify full batch termination, seals, frozen sources and inputs
before loading effects. Never cherry-pick a successful prefix of a stopped run.
Failure inventories and full-batch reconciliation are separate requirements.
Replay checks local consistency, not independent proof of provider/SQL truth.
"""
from copy import deepcopy
import json
import math

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_tools as base
from runtime.cache_action_trace_audit import SavedTools, same, require, elapsed
from runtime.cache_route_symmetric_tools import ToolSession, VERSION, PROFILE
from runtime.cache_route_symmetric_loop import initial_state, run
from runtime.cache_route_symmetric_capacity import SymmetricCapacity
from runtime.cache_route_symmetric_adapter import SymmetricAdapter


class SavedSymmetricTools(SavedTools):
    def __init__(self, job, common, records, cache):
        self.records, self.cache = records, cache
        expected = initial_state(job['envelope'], common)
        base.valid_result(cache)
        same(cache['sql_sha256'], audit.digest(common['history_sql']), 'History/cache binding differs')
        # Never initialize, call or close a live session. Only pure inherited
        # admission/description and reconstruction from saved responses are used.
        self.state = object.__new__(ToolSession)
        for key, value in dict(available=expected['materialized_available'], cache_cost=expected['read_cost'],
                initial_budget=60, balance=60, attempts=0, sql_calls=0, budget_profile=PROFILE,
                max_attempts=3, nonbinding_for_configured_horizon=True,
                expected_db=expected['database_sha256']).items():
            setattr(self.state, key, value)
        same(self.describe(), expected, 'Replay initial state differs')
        start = records['started']
        same(start['version'], VERSION, 'Wrong tool version')
        same(start['base_version'], base.VERSION, 'Wrong base tool version')
        same(start['configuration'], expected, 'Recorded initial state differs')
        same(start['history_sql_sha256'], cache['sql_sha256'], 'Recorded historical SQL differs')
        same(start['cache_rows_sha256'], cache['sequence_sha256'], 'Recorded cache rows differ')
        self.used = {'started', 'summary'}
        self.counts = dict(successful_cache_reads=0, failed_cache_reads=0,
                           SQL_attempts=0, successful_SQL_results=0)

    def finish(self):
        for key, value in dict(status='TOOL_SESSION_CLOSED', unresolved_attempts=[],
                attempts=self.state.attempts, sql_calls=self.state.sql_calls,
                budget_profile=PROFILE, initial_budget=60, nonbinding_for_configured_horizon=True,
                spent_declared_units=60-self.state.balance, remaining_budget=self.state.balance,
                model_calls_by_this_component=0, semantic_approvals=0, inference_eligible=False).items():
            same(self.records['summary'].get(key), value, 'Tool closure differs: '+key)
        require(set(self.records) == self.used, 'Extra or missing tool records')


def replay(job, common, records, tools, cache, counter, guard, *, mode):
    require(mode in ('OFFICIAL_DEVELOPMENT', 'SCRIPTED_NOT_MODEL'), 'Explicit backend mode required')
    same(records['backend'], dict(kind=mode, provider_invoice_verified=False), 'Backend mode differs')
    session = SavedSymmetricTools(job, common, tools, cache)
    used = {'backend'}
    capacity = SymmetricCapacity(job['envelope'], common, counter)
    transport_count = 0
    wall = 0

    def save(name, value):
        require(name in records, 'Missing replay record: '+name)
        expected, actual = deepcopy(records[name]), deepcopy(value)
        if 'wall_seconds' in actual:
            elapsed(expected['wall_seconds'])
            expected.pop('wall_seconds'); actual.pop('wall_seconds')
        if name.startswith('api-') and name.endswith('.pending'):
            timeout = expected['timeout_seconds']
            require(type(timeout) in (int,float) and math.isfinite(timeout) and 0 < timeout <= 90,
                    'Invalid recorded transport timeout')
            expected.pop('timeout_seconds'); actual.pop('timeout_seconds')
        same(expected, actual, 'Replay record differs: '+name)
        require(name not in used, 'Duplicate replay emission')
        used.add(name)

    def transport(payload, timeout):
        nonlocal transport_count, wall
        transport_count += 1
        provider = records[f'api-g{transport_count:03d}.provider']
        elapsed(provider['wall_seconds']); wall += provider['wall_seconds']
        raw = provider['raw']
        if mode == 'OFFICIAL_DEVELOPMENT':
            name = f'transport-{transport_count:03d}'
            record = records[name]
            same(sorted(record), ['response_text','transport_status'], 'Unexpected transport record')
            require(record['transport_status'] == 'RESPONSE_RECEIVED', 'Failure inventory required')
            same(json.loads(record['response_text']), raw, 'Transport/provider copies differ')
            used.add(name)
        return deepcopy(raw)

    adapter = SymmetricAdapter(counter=counter, capacity=capacity, transport=transport,
        save=save, guard=guard, deadline=600, clock=lambda:0)
    result = run(job['envelope'], common, session, adapter, save, capacity.observation_bound(),
                 deadline=600, clock=lambda:0)
    session.finish()
    require(set(records) == used, 'Extra or missing trajectory records')
    actions = [dict(generation=n, **deepcopy(records[f'g{n:03d}.decision']))
               for n in range(1, result['generation_calls']+1)]
    answer = actions[-1]['action']['text'] if result['status'] == 'ANSWERED' else None
    return dict(id=job['id'], final_status=result['status'], final_answer=answer,
                first_decision=deepcopy(records['g001.decision']), actions=actions,
                generation_calls=result['generation_calls'], tool_attempts=result['tool_attempts'],
                dispatch_attempts=transport_count, summed_provider_wall_seconds=wall,
                spent_declared_units=60-session.state.balance, **session.counts,
                semantic_labels=0, C_drift_assessed=False, new_API_calls=0, new_SQL_executions=0)
