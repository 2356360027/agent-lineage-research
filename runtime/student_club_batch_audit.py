"""Sealed offline batch consistency audit; no SQL, inference or human labels.

The caller must independently bind the supplied archive seal and frozen source
hashes to its launch receipt. Stopped batches get inventory coverage only, not
successful-prefix effects. Hash consistency is not independent attestation.
"""
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime import student_club_native_batch as batch
from runtime.deepseek_trajectory_adapter import CostGuard
from runtime.native_symmetric_batch_audit import journal, read
from runtime.native_trajectory_replay import equal
from runtime.cache_intent_journal import verify
from runtime.student_club_definition_history import DefinitionHistory
from runtime.student_club_native_wire import payload, MAX_OUTPUT
from runtime.student_club_native_adapter import MAX_INPUT
from runtime.student_club_trace_audit import replay
from scripts.fetch_bird_fixed import no_link_ancestors

COMPLETED={'ANSWERED','ABSTAINED','INVALID_ACTION_RESPONSE'}
FAILED={'FAILED','INTERRUPTED','ENVIRONMENT_INVALID','FINALIZATION_FAILED'}
SOURCES={'tool_source.py','base_tool_source.py','readonly_source.py','column_scoped_source.py'}


def tool_records(root,source_hashes):
    verify(root)
    if set(source_hashes)!=SOURCES:raise ValueError('Four frozen tool source hashes required')
    result={}
    for p in root.iterdir():
        if p.name=='artifact_hashes.json':continue
        if p.name in SOURCES:equal(audit.hash_file(p),source_hashes[p.name],'Frozen tool source')
        elif p.suffix=='.json':result[p.stem]=read(p)
        else:raise ValueError('Unexpected tool artifact')
    if not SOURCES<={p.name for p in root.iterdir()}:raise ValueError('Missing source copies')
    for field,name in [('source_sha256','tool_source.py'),('base_source_sha256','base_tool_source.py'),
                       ('readonly_source_sha256','readonly_source.py')]:
        equal(result['started'][field],source_hashes[name],'Tool source receipt')
    return result


def inspect(root,plan,common,cache,*,expected_plan_sha,expected_archive_sha,
            native_counter,plain_counter,frozen_tool_sources,mode):
    root=Path(root);no_link_ancestors(root)
    if mode not in batch.MODES:raise ValueError('Explicit backend mode required')
    equal(audit.digest(plan),expected_plan_sha,'Plan seal')
    equal(plan,batch.build_plan(common,cache),'Frozen input/plan')
    equal(audit.hash_file(root/'artifact_hashes.json'),expected_archive_sha,'External archive seal')
    entries=list(root.rglob('*'))
    for p in entries:no_link_ancestors(p)
    hashes={p.relative_to(root).as_posix():audit.hash_file(p) for p in entries
            if p.is_file() and p!=root/'artifact_hashes.json'}
    equal(hashes,read(root/'artifact_hashes.json'),'Archive inventory and bytes')
    logs=journal(root/'batch');equal(logs['plan'],plan,'Executed plan')
    jobs=plan['design']['jobs'];counts=[]
    for j in jobs:
        request=DefinitionHistory(common,j,lambda *args:None).begin()
        receipt=native_counter.inspect(request);n=receipt['reference_input_tokens']
        if type(n) is not int or not 0<n<=MAX_INPUT:raise ValueError('Counter input bound')
        equal(receipt['wire_payload_sha256'],audit.digest(payload(request)),'Counted wire')
        counts.append(n)
    bound=(sum(counts)+(batch.MAX_CALLS-len(jobs))*MAX_INPUT)*batch.INPUT_RATE+batch.MAX_CALLS*MAX_OUTPUT*batch.OUTPUT_RATE
    if bound>batch.CEILING:raise ValueError('Conditional cost exceeds ceiling')
    equal(logs['settings'],dict(mode=mode,first_counts=counts,conditional_micro_cny=bound,
        max_seconds=batch.MAX_SECONDS,full_horizon_fit_guaranteed=False,approved_here=False,automatic_retry=False),'Settings')
    summary=read(root/'summary.json');equal(summary,logs['summary'],'Summary copies')
    if summary['status'] not in (mode+'_FINISHED',mode+'_STOPPED'):raise ValueError('Batch status')
    stopped=summary['status']==mode+'_STOPPED'
    if (stopped and (type(summary['stop_reason']) is not str or not summary['stop_reason'])) or (not stopped and summary['stop_reason'] is not None):
        raise ValueError('Stop reason')
    rows=summary['rows']
    if type(rows) is not list or len(rows)!=12:raise ValueError('All twelve dispositions required')
    inventory={'plan','settings','summary'};top={'batch','summary.json','artifact_hashes.json'};failed=False
    for i,(row,job) in enumerate(zip(rows,jobs)):
        equal(row['id'],job['id'],'Fixed job order')
        for key,maximum in [('dispatch_attempts',job['max_model_calls']),('tool_attempts',3)]:
            if type(row[key]) is not int or not 0<=row[key]<=maximum:raise ValueError('Count bounds')
        if row['status']=='NOT_STARTED':
            if not stopped or not failed:raise ValueError('Unstarted without preceding failure')
            equal(row,dict(id=job['id'],status='NOT_STARTED',dispatch_attempts=0,tool_attempts=0),'Unstarted row')
            continue
        if failed or row['status'] not in COMPLETED|FAILED:raise ValueError('Work after failure or unknown outcome')
        failed=row['status'] in FAILED
        for suffix in ('pending','outcome'):inventory.add(f'job-{i:02d}.{suffix}')
        equal(logs[f'job-{i:02d}.pending'],dict(id=job['id'],automatic_retry=False),'Job claim')
        equal(logs[f'job-{i:02d}.outcome'],row,'Outcome copy')
        if (root/job['id']).exists():top.add(job['id'])
        elif row['status'] in COMPLETED:raise ValueError('Completed trace absent')
    equal(sorted(logs),sorted(inventory),'Batch records')
    equal(sorted(p.name for p in root.iterdir()),sorted(top),'Top level inventory')
    if failed!=stopped:raise ValueError('Stop dispositions differ')
    totals=dict(planned_trajectories=12,task_pairs=1,independent_confirmation=False,
        dispatch_attempts=sum(r['dispatch_attempts'] for r in rows),tool_attempts=sum(r['tool_attempts'] for r in rows),
        real_model_calls=0 if mode=='SCRIPTED_NOT_MODEL' else None,semantic_labels=0,answers_graded=0,
        ordinary_definition_drift='NOT_ASSESSED',invoice_verified=False,automatic_retry=False)
    equal({k:summary.get(k) for k in totals},totals,'Declared totals')
    if stopped:
        return dict(status='SEALED_STOPPED_INVENTORY_ONLY',not_started=sum(r['status']=='NOT_STARTED' for r in rows),
            completed_before_failure=sum(r['status'] in COMPLETED for r in rows),effects_computed=False,
            accounting_verified=False,archive_sha256=expected_archive_sha)
    guard=CostGuard(ceiling_micro_cny=batch.CEILING,max_calls=batch.MAX_CALLS,input_rate=batch.INPUT_RATE,output_rate=batch.OUTPUT_RATE)
    results=[]
    for row,job in zip(rows,jobs):
        path=root/job['id']
        equal(sorted(p.name for p in path.iterdir()),['api','loop','tools'],'Trace inventory')
        tools=tool_records(path/'tools',frozen_tool_sources)
        result=replay(common,job,cache,loop_records=journal(path/'loop'),api_records=journal(path/'api'),
            tool_records=tools,input_receipt=dict(plan_sha256=expected_plan_sha,job_id=job['id'],backend=mode),
            native_counter=native_counter,plain_counter=plain_counter,guard=guard,mode=mode)
        for key,value in [('status',result['loop_status']),('dispatch_attempts',result['calls']),
                          ('generation_callback_attempts',result['calls']),('tool_attempts',result['tool_attempts']),('tools',tools['summary'])]:
            equal(row.get(key),value,'Replayed disposition '+key)
        results.append(dict(id=job['id'],**result))
    equal(guard.snapshot(),summary['cost_guard'],'Final shared cost guard')
    equal(sum(r['calls'] for r in results),totals['dispatch_attempts'],'Total calls')
    return dict(status='SEALED_STUDENT_CLUB_BATCH_REPLAY_VERIFIED',readouts=results,archive_sha256=expected_archive_sha,
        cost_guard=guard.snapshot(),semantic_correctness_assessed=False,independent_confirmation=False,
        provider_invoice_verified=False,model_calls_by_auditor=0,database_queries_by_auditor=0)
