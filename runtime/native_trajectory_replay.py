"""Offline loop/tool provenance replay; never executes SQL or model calls.

This verifies completed protocol traces, including observed invalid answers,
not semantic correctness, provider billing or authentic human provenance.
Incomplete/failing traces raise IncompleteTrajectory and must remain in the
batch denominator. Caller must separately verify frozen-source, API-accounting
and outer archive manifests. A self-generated hash is not external attestation.
"""
from copy import deepcopy
import math
from pathlib import Path
import re

from runtime.bird_readonly_audit import digest, encoded, hash_file, LIMITS
from runtime.cache_action_factorial_loop import check_transition
from runtime.cache_action_preview import payload
from runtime.cache_intent_observation_bound import serialize
from runtime.cache_intent_tools import valid_result
from runtime.cache_route_symmetric_loop import initial_state, ObservationBound
from runtime.native_action_history import NativeHistory
from runtime.native_action_interface import build_candidate, _load
from runtime.native_symmetric_loop import VERSION as LOOP_VERSION
from scripts.fetch_bird_fixed import no_link_ancestors


class IncompleteTrajectory(ValueError):
    pass


def equal(actual, expected, context):
    if encoded(actual) != encoded(expected):
        raise ValueError(context + ' mismatch')


def duration(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid measured duration')


def read_sealed_tools(directory, *, manifest_sha256, source_hashes):
    """Require an independently supplied seal and exact artifact inventory."""
    root = Path(directory)
    no_link_ancestors(root)
    if not root.is_dir() or (root / 'finalization_failure.json').exists():
        raise IncompleteTrajectory('Missing or failed tool finalization; retain records')
    manifest_path = root / 'artifact_hashes.json'
    no_link_ancestors(manifest_path)
    if not manifest_path.is_file():
        raise IncompleteTrajectory('Tool manifest missing; do not reconstruct')
    if hash_file(manifest_path) != manifest_sha256:
        raise ValueError('Outer-bound tool manifest hash differs')
    manifest = _load(manifest_path.read_text(encoding='utf-8'))
    if type(manifest) is not dict:
        raise ValueError('Expected artifact manifest')
    sources = {'tool_source.py', 'base_tool_source.py', 'readonly_source.py'}
    if type(source_hashes) is not dict or set(source_hashes) != sources:
        raise ValueError('Three frozen source fingerprints required')
    if not sources | {'started.json','summary.json'} <= set(manifest):
        raise IncompleteTrajectory('Tool closure/source artifacts missing')
    actual = set()
    for path in root.iterdir():
        no_link_ancestors(path)
        if not path.is_file():
            raise ValueError('Unexpected directory in sealed tools')
        if path.name != 'artifact_hashes.json': actual.add(path.name)
    if actual != set(manifest):
        raise ValueError('Sealed artifact inventory differs')
    data = {}
    for name, sha in manifest.items():
        if (type(name) is not str or '/' in name or '\\' in name or ':' in name
                or name in ('.', '..') or type(sha) is not str
                or re.fullmatch('[0-9a-f]{64}', sha) is None):
            raise ValueError('Unsafe manifest entry')
        path = root / name
        if hash_file(path) != sha: raise ValueError('Tool artifact hash differs')
        if name in sources:
            if sha != source_hashes[name]: raise ValueError('Frozen source differs')
        elif name.endswith('.json'):
            data[name] = _load(path.read_text(encoding='utf-8'))
        else:
            raise ValueError('Unexpected sealed artifact')
    start = data['started.json']
    if type(start) is not dict: raise ValueError('Tool start schema')
    for field, name in (('source_sha256','tool_source.py'),
                        ('base_source_sha256','base_tool_source.py'),
                        ('readonly_source_sha256','readonly_source.py')):
        equal(start.get(field), source_hashes[name], 'Recorded source fingerprint')
    return data


def replay(records, *, envelope, trusted_common, cached_result, input_receipt,
           tool_records, observation_bound):
    """Reconstruct all requests/decisions/exposures from retained raw outputs.

records is the loop journal only; tool_records is the externally hash-verified
JSON inventory returned by read_sealed_tools. API records are separate and may
not be smuggled into this map and silently ignored.
"""
    if not isinstance(observation_bound, ObservationBound):
        raise TypeError('Pinned symmetric observation counter required')
    if type(records) is not dict or type(tool_records) is not dict:
        raise ValueError('Journal maps required')
    if 'failure' in records or 'summary' not in records:
        raise IncompleteTrajectory('Failed or unfinished loop; no success reconstruction')
    used = set()
    def take(name):
        if name not in records: raise ValueError('Missing loop record: ' + name)
        used.add(name)
        return records[name]
    state = initial_state(envelope, trusted_common)
    equal(take('started'), dict(protocol=LOOP_VERSION, envelope=envelope, initial_state=state,
          contract_elicited=False, semantic_labels_generated=False), 'Loop start')
    valid_result(cached_result)
    equal(cached_result.get('sql_sha256'), digest(trusted_common['history_sql']), 'Cache SQL')
    start = tool_records.get('started.json')
    if type(start) is not dict: raise ValueError('Tool start missing')
    equal(start.get('configuration'), state, 'Tool start configuration')
    equal(start.get('input_receipt'), input_receipt, 'Source-bound input receipt')
    equal(start.get('history_sql_sha256'), digest(trusted_common['history_sql']), 'History SQL')
    equal(start.get('cache_rows_sha256'), cached_result['sequence_sha256'], 'Cache rows')
    history = NativeHistory(build_candidate(payload(envelope['messages']))['payload'],
                            observation_byte_limit=1048576)
    attempts = sql_calls = 0
    for number in range(1, 5):
        request = history.begin()
        equal(take(f'g{number:03d}.pending'), request, 'Generation request')
        response = take(f'g{number:03d}.response')
        if type(response) is not dict or set(response) != {'request_sha256','raw_response_text','wall_seconds'}:
            raise ValueError('Generation response schema')
        equal(response['request_sha256'], digest(request), 'Generation request hash')
        duration(response['wall_seconds'])
        raw = response['raw_response_text']
        if type(raw) is not str or len(raw.encode('utf-8')) > 1048576:
            raise ValueError('Bounded raw provider response required')
        try:
            decision = history.accept(raw)
        except (ValueError, TypeError, RecursionError):
            decision = dict(status='INVALID_ACTION_RESPONSE', stop=True, dispatch_tool=False, action=None)
        equal(take(f'g{number:03d}.decision'), decision, 'Recomputed decision')
        if decision['stop']:
            summary = dict(status=decision['status'], generation_calls=number, tool_attempts=attempts,
                           semantic_labels_generated=False, contract_elicited=False)
            equal(take('summary'), summary, 'Loop summary')
            if used != set(records): raise ValueError('Unexpected loop records or calls after terminal')
            expected_files = {'started.json','summary.json'} | {
                f'attempt-{i:03d}.{kind}.json' for i in range(1, attempts+1) for kind in ('pending','completed')}
            if set(tool_records) != expected_files: raise ValueError('Tool inventory differs from loop attempts')
            close = tool_records['summary.json']
            if close.get('status') != 'TOOL_SESSION_CLOSED':
                raise IncompleteTrajectory('Tool closure incomplete or environment invalid')
            required = dict(unresolved_attempts=[], attempts=attempts, sql_calls=sql_calls,
                budget_profile=state['budget_profile'], initial_budget=60,
                nonbinding_for_configured_horizon=True, spent_declared_units=60-state['remaining_budget'],
                remaining_budget=state['remaining_budget'], model_calls_by_this_component=0,
                semantic_approvals=0, inference_eligible=False)
            equal({k:close.get(k) for k in required}, required, 'Tool closure')
            return dict(status='COMPLETED_LOOP_AND_TOOL_TRACE_REPLAY_VERIFIED',
                loop_status=summary['status'], generation_calls=number, tool_attempts=attempts,
                sql_calls=sql_calls, final_action=decision['action'],
                semantic_correctness_assessed=False, api_accounting_verified=False,
                database_reexecuted=False)
        attempts += 1
        action = decision['action']
        pending = dict(generation_id=request['generation_id'], call_id=decision['call_id'],
                       tool=action['kind'], arguments={k:v for k,v in action.items() if k!='kind'})
        equal(take(f't{attempts:03d}.pending'), pending, 'Loop tool request')
        result = take(f't{attempts:03d}.response')
        if type(result) is not dict or set(result) != {'request_sha256','observation','wall_seconds'}:
            raise ValueError('Tool response schema')
        equal(result['request_sha256'], digest(pending), 'Tool request hash')
        duration(result['wall_seconds'])
        tool_pending = tool_records.get(f'attempt-{attempts:03d}.pending.json')
        completed = tool_records.get(f'attempt-{attempts:03d}.completed.json')
        if type(tool_pending) is not dict or type(completed) is not dict:
            raise IncompleteTrajectory('Missing tool attempt records')
        equal(tool_pending.get('request'), dict(tool=pending['tool'], arguments=pending['arguments']), 'Backend request')
        keys = {'attempt','request','admission','charge','balance_before','balance_after','automatic_retry'}
        if set(tool_pending) != keys: raise ValueError('Tool pending schema')
        equal({k:completed.get(k) for k in keys}, tool_pending, 'Backend pending/completed binding')
        if completed.get('environment_invalidated') is not False:
            raise IncompleteTrajectory('Invalidated tool environment')
        duration(completed.get('measured_action_wall_seconds'))
        observation = result['observation']
        equal(completed.get('response'), observation, 'Backend raw observation')
        charge = tool_pending['charge']
        equal(tool_pending['balance_before'], state['remaining_budget'], 'Balance before')
        equal(tool_pending['automatic_retry'], False, 'Retry flag')
        equal(tool_pending['attempt'], attempts, 'Attempt number')
        after = deepcopy(state)
        after['attempts_used'] += 1
        after['remaining_budget'] -= charge
        equal(tool_pending['balance_after'], after['remaining_budget'], 'Balance after')
        check_transition(state, after, observation, pending['tool'])
        if pending['tool']=='read_cache' and not state['materialized_available']:
            if observation.get('status')!='CACHE_UNAVAILABLE' or any(k in observation for k in ('rows','columns')):
                raise ValueError('Unavailable cache leaked')
        private = completed.get('private')
        if type(private) is not dict: raise ValueError('Tool private record required')
        if pending['tool']=='read_cache':
            admission = ('INVALID_REQUEST' if pending['arguments']!={'cache_id':'v000'} else
                         'ADMITTED' if state['materialized_available'] else 'CACHE_UNAVAILABLE')
        else:
            sql = pending['arguments']['sql']
            admission = ('ADMITTED' if sql.strip() and len(sql.encode('utf-8'))<=LIMITS['max_sql_bytes']
                         else 'INVALID_REQUEST')
        equal(tool_pending['admission'], admission, 'Recomputed tool admission')
        if tool_pending['admission']=='ADMITTED':
            expected_charge = state['read_cost'] if pending['tool']=='read_cache' else 10
            equal(charge, expected_charge, 'Admitted tool price')
            backend = private.get('backend_result')
            if type(backend) is not dict: raise ValueError('Backend result missing')
            if pending['tool']=='read_cache': equal(backend, cached_result, 'Bound cache contents')
            else:
                sql_calls += 1
                # The read-only worker result has no SQL hash. Bind it through
                # the sealed completed record's exact pending request instead;
                # do not invent a missing provenance field or execute again.
            expected_observation = dict(status=backend.get('status','BACKEND_ERROR'), complete=False,
                charged_units=charge, remaining_budget=after['remaining_budget'], attempt=attempts)
            if backend.get('complete') is True:
                valid_result(backend)
                expected_observation.update(status='EXECUTED', complete=True,
                                            columns=backend['columns'], rows=backend['rows'])
            elif backend.get('status') in ('EXECUTION_ERROR','DENIED') and type(backend.get('error')) is str:
                expected_observation['error'] = backend['error'][:1024]
            equal(observation, expected_observation, 'Backend projection')
        else:
            equal(charge, 0, 'Rejected tool charge')
            equal(private, {}, 'Rejected tool private contents')
            equal(observation, dict(status=tool_pending['admission'], complete=False,
                charged_units=0, remaining_budget=after['remaining_budget'], attempt=attempts), 'Rejected response')
        exposure = observation_bound.apply(observation)
        equal(take(f't{attempts:03d}.exposure'), exposure, 'Recomputed bounded exposure')
        history.observe(decision['call_id'], serialize(exposure['observation']))
        state = after
    raise ValueError('Missing terminal outcome')
