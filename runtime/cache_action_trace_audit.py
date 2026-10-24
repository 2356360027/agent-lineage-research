"""Offline replay of a finished trajectory; never dispatch or execute SQL.

Caller must FIRST verify the sealed batch, all file hashes, source version and
inputs. This replay is not an integrity seal, provider attestation or semantic
judge. Its dependencies must be loaded from the execution's verified source.
Stopped/partial batches require a separate failure inventory, not this replay.
"""
from copy import deepcopy
import json
import math
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime import cache_action_preview as preview
from runtime.cache_action_adapter import ActionAdapter
from runtime.cache_action_capacity import ActionCapacity
from runtime.cache_action_loop import run
from runtime.cache_intent_tools import ToolSession, valid_result, VERSION, SHORT_RECOVERY_PROFILE
from runtime.cache_intent_journal import verify as verify_journal
from scripts.fetch_bird_fixed import no_link_ancestors


def require(value, message):
    if not value:
        raise ValueError(message)


def same(left, right, message):
    require(audit.digest(left) == audit.digest(right), message)


def elapsed(value):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            'Invalid recorded duration')


class SavedTools:
    """Reconstruct admission and exposures from records, never open a database."""
    def __init__(self, job, records, cache):
        self.records, self.cache = records, cache
        packet = preview.validate(job['envelope'])
        valid_result(cache)
        same(audit.digest(cache), job['cached_result_sha256'], 'Cache identity differs')
        same(cache['sql_sha256'], audit.digest(packet['history']['sql']), 'Historical SQL differs')
        # Only pure describe/_classify methods are used; __init__/call/close
        # would access a database and are deliberately never invoked.
        self.state = object.__new__(ToolSession)
        for key, value in dict(available=job['available'], cache_cost=1,
                initial_budget=30, balance=30, attempts=0, sql_calls=0,
                budget_profile=SHORT_RECOVERY_PROFILE, max_attempts=3,
                nonbinding_for_configured_horizon=True,
                expected_db=packet['context']['database_sha256']).items():
            setattr(self.state, key, value)
        self.used = {'started', 'summary'}
        start = records['started']
        same(start['version'], VERSION, 'Tool version differs')
        same(start['configuration'], self.describe(), 'Initial tool state differs')
        same(start['history_sql_sha256'], cache['sql_sha256'], 'Tool history differs')
        same(start['cache_rows_sha256'], cache['sequence_sha256'], 'Tool cache rows differ')
        self.counts = dict(successful_cache_reads=0, failed_cache_reads=0,
                           SQL_attempts=0, successful_SQL_results=0)

    def describe(self):
        return self.state.describe()

    def call(self, name, arguments):
        n = self.state.attempts + 1
        require(n <= 3, 'Too many tool attempts')
        admission, cost = self.state._classify(name, arguments)
        pending = dict(attempt=n, request=dict(tool=name, arguments=arguments),
            admission=admission, charge=cost, balance_before=self.state.balance,
            balance_after=self.state.balance-cost, automatic_retry=False)
        stem = f'attempt-{n:03d}'
        same(self.records[stem+'.pending'], pending, 'Tool admission/budget differs')
        completed = self.records[stem+'.completed']
        require(set(completed) == set(pending) | {'response','private',
                'environment_invalidated','measured_action_wall_seconds'}, 'Unexpected tool record fields')
        same({k:completed[k] for k in pending}, pending, 'Tool request copies differ')
        require(completed['environment_invalidated'] is False, 'Invalid environment; failure audit required')
        elapsed(completed['measured_action_wall_seconds'])
        self.state.balance -= cost
        self.state.attempts = n
        response = dict(status=admission, complete=False, charged_units=cost,
                        remaining_budget=self.state.balance, attempt=n)
        if name == 'run_sql': self.counts['SQL_attempts'] += 1
        if admission == 'ADMITTED':
            require(set(completed['private']) == {'backend_result'}, 'Missing backend record')
            backend = completed['private']['backend_result']
            if name == 'read_cache':
                same(backend, self.cache, 'Read did not return bound cache')
            else:
                self.state.sql_calls += 1
                # run_child does not itself emit a SQL hash. The enclosing
                # pending/completed record binds this result to the request;
                # do not invent a stronger independent execution attestation.
                if 'sql_sha256' in backend:
                    same(backend['sql_sha256'], audit.digest(arguments['sql']), 'SQL/result binding differs')
            if backend.get('complete') is True:
                valid_result(backend)
                response.update(status='EXECUTED', complete=True,
                                columns=backend['columns'], rows=backend['rows'])
            else:
                response.update(status=backend.get('status', 'BACKEND_ERROR'))
                if backend.get('status') in ('EXECUTION_ERROR','DENIED') and type(backend.get('error')) is str:
                    response['error'] = backend['error'][:1024]
        else:
            same(completed['private'], {}, 'Rejected request acquired backend data')
        same(completed['response'], response, 'Tool response was not derived from saved backend')
        if name == 'read_cache':
            self.counts['successful_cache_reads' if response['complete'] else 'failed_cache_reads'] += 1
        elif name == 'run_sql' and response['complete']:
            self.counts['successful_SQL_results'] += 1
        self.used.update((stem+'.pending', stem+'.completed'))
        return deepcopy(response)

    def finish(self):
        summary = self.records['summary']
        for key, value in dict(status='TOOL_SESSION_CLOSED', unresolved_attempts=[],
                attempts=self.state.attempts, sql_calls=self.state.sql_calls,
                budget_profile=SHORT_RECOVERY_PROFILE, initial_budget=30,
                nonbinding_for_configured_horizon=True,
                spent_declared_units=30-self.state.balance, remaining_budget=self.state.balance,
                model_calls_by_this_component=0, semantic_approvals=0, inference_eligible=False).items():
            same(summary.get(key), value, 'Tool closure accounting differs: '+key)
        require(set(self.records) == self.used, 'Extra or missing tool records')


def replay(job, records, tools, cache, counter, guard, *, mode):
    """Recompute every finished-loop record, including invalid outputs, verbatim.

    records/tools are name-without-.json mappings from verified journals,
    excluding seals and copied source files. guard is shared in batch order.
    No remote calls, SQL execution, retries, new samples or correctness labels.
    """
    require(mode in ('OFFICIAL_DEVELOPMENT','SCRIPTED_NOT_MODEL'), 'Explicit provenance required')
    same(records['backend'], dict(kind=mode, provider_invoice_verified=False), 'Backend mode differs')
    session = SavedTools(job, tools, cache)
    used = {'backend'}
    capacity = ActionCapacity(job['envelope'], counter)
    transport_count = 0
    wall = 0

    def save(name, value):
        require(name in records, 'Replay requires an absent record: '+name)
        expected = deepcopy(records[name]); actual = deepcopy(value)
        if 'wall_seconds' in actual:
            elapsed(expected['wall_seconds'])
            actual.pop('wall_seconds'); expected.pop('wall_seconds')
        if name.startswith('api-') and name.endswith('.pending'):
            timeout = expected['timeout_seconds']
            require(type(timeout) in (int,float) and math.isfinite(timeout) and 0 < timeout <= 90,
                    'Invalid dispatch timeout')
            actual.pop('timeout_seconds'); expected.pop('timeout_seconds')
        same(expected, actual, 'Replay record differs: '+name)
        require(name not in used, 'Duplicate record emission')
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
            require(record['transport_status'] == 'RESPONSE_RECEIVED', 'Transport failed; use failure audit')
            same(json.loads(record['response_text']), raw, 'Transport/provider copies differ')
            used.add(name)
        return deepcopy(raw)

    adapter = ActionAdapter(counter=counter, capacity=capacity, transport=transport,
        save=save, guard=guard, deadline=600, clock=lambda:0)
    result = run(job['envelope'], session, adapter, save, capacity.observation_bound())
    session.finish()
    require(set(records) == used, 'Extra or missing trace records')
    final_answer = None
    if result['status'] == 'ANSWERED':
        final_answer = records[f"g{result['generation_calls']:03d}.decision"]['action']['text']
    return dict(final_status=result['status'], final_answer=final_answer,
        generation_calls=result['generation_calls'], tool_attempts=result['tool_attempts'],
        dispatch_attempts=transport_count, summed_provider_wall_seconds=wall,
        spent_declared_units=30-session.state.balance, **session.counts,
        semantic_labels=0, C_drift_assessed=False)


def read_json(path):
    return json.loads(Path(path).read_text('utf-8'))


def verify_tree(root):
    root = Path(root)
    no_link_ancestors(root)
    entries = list(root.rglob('*'))
    require(not any(p.is_symlink() for p in entries), 'Unexpected artifact link')
    actual = {p.relative_to(root).as_posix():audit.hash_file(p) for p in entries
              if p.is_file() and p != root/'artifact_hashes.json'}
    same(actual, read_json(root/'artifact_hashes.json'), 'Full artifact manifest differs')
    return actual


def finished_batch(root, plan, caches, counter, *, expected_plan_sha256, mode):
    """Check sealed FINISHED batch, not in-progress or interrupted effects.

    Source/input admission remains the launcher's responsibility. An interrupted
    batch is retained but must use a failure audit, never be filtered to its
    successful prefix and sent through this function.
    """
    from runtime.cache_action_batch import validate
    from runtime.deepseek_trajectory_adapter import CostGuard
    require(mode in ('SCRIPTED_NOT_MODEL','OFFICIAL_DEVELOPMENT'), 'Explicit mode required')
    root = Path(root)
    require((root/'artifact_hashes.json').is_file(), 'No final seal; do not inspect effects')
    hashes = verify_tree(root)
    summary = read_json(root/'summary.json')
    require(summary['status'] == mode+'_FINISHED' and summary['stop_reason'] is None,
            'Not a finished batch; preserve records for separate failure audit')
    validate(plan, caches, expected_plan_sha256)
    verify_journal(root/'batch')
    same(read_json(root/'batch/summary.json'), summary, 'Batch summary copies differ')
    same(read_json(root/'batch/plan.json'), plan, 'Executed plan differs')
    maximum_cost=sum(ActionCapacity(j['envelope'],counter).certificate()['total_input_bound']*2
                     +4*512*8 for j in plan['jobs'])
    same(read_json(root/'batch/settings.json'), dict(mode=mode,ceiling_micro_cny=500000,
        max_calls=48,max_seconds=600,max_output_tokens=512,conditional_cost_micro_cny=maximum_cost,
        rates_micro_cny_per_token=[2,8],automatic_retry=False,confirmation_study=False), 'Batch settings differ')
    expected_batch={'plan.json','settings.json','summary.json','artifact_hashes.json'}
    for i in range(12): expected_batch.update((f'job-{i:02d}.pending.json',f'job-{i:02d}.outcome.json'))
    require({p.name for p in (root/'batch').iterdir()} == expected_batch, 'Unexpected batch inventory')
    require({p.name for p in root.iterdir()} == {'batch','summary.json','artifact_hashes.json'} |
            {j['id'] for j in plan['jobs']}, 'Unexpected experiment inventory')
    require(len(summary['rows']) == 12, 'Missing summary slots')
    guard=CostGuard(ceiling_micro_cny=500000,max_calls=48,input_rate=2,output_rate=8)
    results=[]; inputs=outputs=0
    for index, (job,row) in enumerate(zip(plan['jobs'],summary['rows'])):
        same(row['id'],job['id'],'Summary execution order differs')
        same(read_json(root/'batch'/f'job-{index:02d}.pending.json'),
             dict(id=job['id'],automatic_retry=False),'Job dispatch identity differs')
        same(read_json(root/'batch'/f'job-{index:02d}.outcome.json'),row,'Job summary copies differ')
        directory=root/job['id']
        require({p.name for p in directory.iterdir()} == {'trace','tools'}, 'Unexpected job inventory')
        for folder in ('trace','tools'): verify_journal(directory/folder)
        def records(folder):
            return {p.stem:read_json(p) for p in (directory/folder).glob('*.json')
                    if p.name != 'artifact_hashes.json'}
        trace,tools=records('trace'),records('tools')
        for filename,module in (('tool_source.py',__import__('runtime.cache_intent_tools',fromlist=['x'])),
                                ('readonly_source.py',audit)):
            same(audit.hash_file(directory/'tools'/filename),audit.hash_file(module.__file__),
                 'Copied tool source differs from loaded audit dependency')
        start=tools['started']
        same(start['input_receipt'],dict(plan_sha256=expected_plan_sha256,job_id=job['id'],backend=mode),
             'Tool receipt differs')
        same(start['source_sha256'],audit.hash_file(directory/'tools/tool_source.py'),'Tool source receipt differs')
        same(start['readonly_source_sha256'],audit.hash_file(directory/'tools/readonly_source.py'),'SQL source receipt differs')
        same(start['limits'],audit.LIMITS,'SQL limits differ')
        result=replay(job,trace,tools,caches[job['history_id']],counter,guard,mode=mode)
        same(row,dict(id=job['id'],status=result['final_status'],
            loop_generation_calls=result['generation_calls'],dispatch_attempts=result['dispatch_attempts'],
            tools=tools['summary']),'Reconstructed trajectory summary differs')
        for n in range(1,result['dispatch_attempts']+1):
            usage=trace[f'api-g{n:03d}.provider']['raw']['usage']
            inputs+=usage['prompt_tokens']; outputs+=usage['completion_tokens']
        results.append(dict(id=job['id'],history_id=job['history_id'],available=job['available'],
            trace_sha256=audit.hash_file(directory/'trace/artifact_hashes.json'),**result))
    same(summary['cost_guard'],guard.snapshot(),'Reconstructed API cost differs')
    for key,value in dict(planned_trajectories=12,reused_development_tasks=2,
            dispatch_attempts=sum(r['dispatch_attempts'] for r in results),
            model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None,semantic_labels=0,
            answers_graded=0,confirmation_study=False,invoice_verified=False,automatic_retry=False).items():
        same(summary.get(key),value,'Batch accounting differs: '+key)
    same(verify_tree(root),hashes,'Artifacts changed during replay')
    return dict(status='SEALED_FINISHED_TRACE_REPLAY_VERIFIED',mode=mode,rows=results,
        experiment_manifest_sha256=audit.hash_file(root/'artifact_hashes.json'),
        verified_files=len(hashes),planned_trajectories=12,independent_task_units=2,
        prompt_tokens=inputs,completion_tokens=outputs,cost_guard=guard.snapshot(),
        new_API_calls=0,new_SQL_executions=0,semantic_labels=0,C_drift_assessed=False,
        limits=['Source and input admission must be verified separately.',
                'Saved local records are not independent provider or SQL execution attestation.',
                'Declared tool units are not money or measured savings.',
                'Reading a cache does not prove internal reliance.',
                'Final answer correctness awaits human review; no C diagnosis from answer errors.'])
