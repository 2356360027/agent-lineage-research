"""Externally sealed batch replay or stopped inventory. No SQL or network.

The frozen launcher must first bind source, dependencies, semantic admissions,
inputs and worker termination. A hash alone is not independent attestation.
Stopped batches expose coverage only, never prefix effects or reconstructed
answers. Complete batches are replayed with a single shared cost ledger.
"""
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_journal import verify as verify_journal
from runtime.cache_route_symmetric_loop import ObservationBound
from runtime.native_action_interface import _load
from runtime.native_trajectory_replay import equal, read_sealed_tools, replay
from runtime.native_accounting_replay import replay_completed
from runtime import native_symmetric_batch as batch
from scripts.fetch_bird_fixed import no_link_ancestors

COMPLETED={'ANSWERED','ABSTAINED','INVALID_ACTION','INVALID_ACTION_RESPONSE','TOOL_HORIZON_VIOLATION'}
FAILED={'FAILED','INTERRUPTED','ENVIRONMENT_INVALID','FINALIZATION_FAILED'}


def read(path): return _load(Path(path).read_text('utf-8'))


def journal(root):
    verify_journal(root)
    result={}
    for path in root.iterdir():
        no_link_ancestors(path)
        if path.name=='artifact_hashes.json':continue
        if path.suffix!='.json':raise ValueError('Unexpected journal extension')
        result[path.stem]=read(path)
    return result


def inspect(root,plan,groups,*,expected_plan_sha,expected_archive_sha,mode,
            native_counter,plain_counter,frozen_tool_sources):
    root=Path(root);no_link_ancestors(root)
    batch.validate(plan,groups,expected_plan_sha)
    if audit.hash_file(root/'artifact_hashes.json')!=expected_archive_sha:
        raise ValueError('External archive seal differs')
    entries=list(root.rglob('*'))
    for p in entries:no_link_ancestors(p)
    hashes={p.relative_to(root).as_posix():audit.hash_file(p) for p in entries
            if p.is_file() and p!=root/'artifact_hashes.json'}
    equal(hashes,read(root/'artifact_hashes.json'),'Whole archive inventory/hash')
    logs=journal(root/'batch')
    equal(logs.get('plan'),plan,'Frozen/executed plan')
    equal(logs.get('settings'),batch.settings(mode),'Batch settings')
    summary=read(root/'summary.json');equal(summary,logs.get('summary'),'Summary copies')
    if summary.get('status') not in (mode+'_FINISHED',mode+'_STOPPED'):
        raise ValueError('Unknown batch status')
    stopped=summary['status']==mode+'_STOPPED'
    bad_reason=(type(summary.get('stop_reason')) is not str or not summary['stop_reason']) if stopped else summary.get('stop_reason') is not None
    if bad_reason:
        raise ValueError('Invalid batch stop reason')
    rows=summary.get('rows')
    if type(rows) is not list or len(rows)!=8:raise ValueError('All eight dispositions required')
    expected_batch={'plan','settings','summary'}
    top={'batch','summary.json','artifact_hashes.json'}
    failure_seen=False
    for i,(row,job) in enumerate(zip(rows,plan['jobs'])):
        equal(row.get('id'),job['id'],'Job identity/order')
        n=row.get('dispatch_attempts')
        if type(n) is not int or not 0<=n<=4:raise ValueError('Invalid dispatch count')
        if row.get('status')=='NOT_STARTED':
            if not stopped or not failure_seen:raise ValueError('Unstarted job without preceding failure')
            equal(row,dict(id=job['id'],status='NOT_STARTED',dispatch_attempts=0),'Unstarted disposition')
            if (root/job['id']).exists():raise ValueError('Unstarted job has files')
            continue
        if failure_seen or row.get('status') not in COMPLETED|FAILED:
            raise ValueError('Unexpected job or work after terminal failure')
        failure_seen=row['status'] in FAILED
        for suffix in ('pending','outcome'):expected_batch.add(f'job-{i:02d}.{suffix}')
        equal(logs.get(f'job-{i:02d}.pending'),dict(id=job['id'],automatic_retry=False),'Job claim')
        equal(logs.get(f'job-{i:02d}.outcome'),row,'Job outcome copy')
        if (root/job['id']).exists():top.add(job['id'])
        elif row['status'] in COMPLETED:raise ValueError('Completed job missing files')
    equal(sorted(logs),sorted(expected_batch),'Batch journal inventory')
    equal(sorted(p.name for p in root.iterdir()),sorted(top),'Top-level inventory')
    if stopped!=failure_seen:raise ValueError('Stop status differs from dispositions')
    expected_summary=dict(planned_trajectories=8,task_pairs=2,shared_database=True,independent_confirmation=False,
        dispatch_attempts=sum(r['dispatch_attempts'] for r in rows),model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None,
        semantic_labels=0,answers_graded=0,invoice_verified=False,automatic_retry=False)
    equal({k:summary.get(k) for k in expected_summary},expected_summary,'Batch totals')
    if stopped:
        return dict(status='SEALED_STOPPED_INVENTORY_ONLY',planned=8,
            not_started=sum(r['status']=='NOT_STARTED' for r in rows),
            failed=sum(r['status'] in FAILED for r in rows),
            completed_before_failure=sum(r['status'] in COMPLETED for r in rows),
            dispatch_attempts=summary['dispatch_attempts'],effects_computed=False,
            accounting_verified=False,automatic_retry=False,archive_sha256=expected_archive_sha)
    guard=batch.new_guard();bound=ObservationBound(plain_counter.chat)
    readouts=[];prompt=completion=0
    for job,row in zip(plan['jobs'],rows):
        trace=root/job['id']
        equal(sorted(p.name for p in trace.iterdir()),['api','loop','tools'],'Completed job directories')
        loop=journal(trace/'loop');api=journal(trace/'api')
        tools=read_sealed_tools(trace/'tools',
            manifest_sha256=hashes[job['id']+'/tools/artifact_hashes.json'],source_hashes=frozen_tool_sources)
        item=groups[job['family']]
        result=replay(loop,envelope=job['envelope'],trusted_common=item['common'],cached_result=item['cache'],
            input_receipt=batch.input_receipt(expected_plan_sha,job,mode),tool_records=tools,observation_bound=bound)
        billing=replay_completed(api,loop_records=loop,counter=native_counter,guard=guard)
        equal(row['status'],result['loop_status'],'Job loop status')
        equal(row.get('loop_generation_calls'),result['generation_calls'],'Loop calls')
        equal(row['dispatch_attempts'],billing['calls'],'Dispatch calls')
        equal(row.get('tools'),tools['summary.json'],'Closed tool state')
        prompt+=billing['reported_prompt_tokens'];completion+=billing['reported_completion_tokens']
        readouts.append(dict(id=job['id'],family=job['family'],cell_index=job['cell_index'],
                             **result))
    equal(guard.snapshot(),summary.get('cost_guard'),'Final shared guard')
    equal(guard.calls,summary['dispatch_attempts'],'Final dispatch/reservation count')
    return dict(status='SEALED_NATIVE_BATCH_REPLAY_VERIFIED',planned=8,readouts=readouts,
        reported_prompt_tokens=prompt,reported_completion_tokens=completion,cost_guard=guard.snapshot(),
        semantic_correctness_assessed=False,C_type_drift_assessed=False,independent_confirmation=False,
        archive_sha256=expected_archive_sha,provider_invoice_verified=False,model_calls_by_auditor=0)
