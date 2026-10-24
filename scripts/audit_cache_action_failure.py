"""Offline inventory for FIRST-request transport failure only; never retry.

Other failure shapes need separate inspection, not silent coercion into this
case. Does not establish whether the provider received or billed the request.
"""
import argparse
import importlib.util
from pathlib import Path
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared',type=Path,required=True)
    p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    helper=Path(__file__).with_name('audit_cache_action.py')
    spec=importlib.util.spec_from_file_location('_action_archive_helper',helper)
    h=importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
    require=h.require; root=args.prepared.absolute(); h.no_links(root)
    require(not args.output.exists(),'Never overwrite failure audit')
    before=h.snapshot(root)
    require(h.sha(root/'manifest.json')==args.manifest_sha256,'Preparation hash differs')
    m=h.read(root/'manifest.json')
    require(h.snapshot(root/'source')==m['source_files'],'Frozen source differs')
    require(not any(n.startswith(('runtime.','scripts.')) for n in sys.modules),'Mixed imports')
    sys.path.insert(0,str(root/'source'))
    from scripts.run_cache_action import verify
    from runtime.cache_intent_journal import verify as verify_journal
    from runtime import cache_action_preview as preview
    from runtime.cache_action_capacity import ActionCapacity
    from runtime.deepseek_trajectory_adapter import CostGuard
    m,plan,caches,counter=verify(root,args.manifest_sha256)
    claim=h.read(root/'execution_claim.json')
    require(claim['manifest_sha256']==args.manifest_sha256 and claim['approved_cny']==.50
            and claim['automatic_retry'] is False,'Claim differs')
    exp=root/'experiment'; summary=h.read(exp/'summary.json')
    inventory=h.snapshot(exp); inventory.pop('artifact_hashes.json')
    require(inventory==h.read(exp/'artifact_hashes.json'),'Experiment hash mismatch')
    job=plan['jobs'][0]; ident=job['id']; trace=exp/ident/'trace'; tools=exp/ident/'tools'
    require({p.name for p in exp.iterdir()}=={'batch',ident,'summary.json','artifact_hashes.json'},'Unexpected job inventory')
    require(summary['status']=='OFFICIAL_DEVELOPMENT_STOPPED' and summary['stop_reason']=='RuntimeError'
            and summary['dispatch_attempts']==1 and len(summary['rows'])==12,'Different failure shape')
    require(summary['planned_trajectories']==12 and summary['reused_development_tasks']==2
            and summary['answers_graded']==0 and summary['semantic_labels']==0,'Unexpected grading/accounting')
    for folder in (exp/'batch',trace,tools): verify_journal(folder)
    require(h.read(exp/'batch/summary.json')==summary and h.read(exp/'batch/plan.json')==plan,'Batch copies differ')
    require({p.name for p in (exp/'batch').iterdir()}=={'artifact_hashes.json','plan.json','settings.json',
            'summary.json','job-00.pending.json','job-00.outcome.json'},'Unexpected batch journal')
    for i,(j,row) in enumerate(zip(plan['jobs'],summary['rows'])):
        require(row['id']==j['id'],'Row order differs')
        if i:
            require(row==dict(id=j['id'],status='NOT_STARTED',dispatch_attempts=0),'Unexpected later activity')
    row=summary['rows'][0]
    require(row['status']=='FAILED' and row['error_type']=='RuntimeError' and row['dispatch_attempts']==1,'First row differs')
    require(h.read(exp/'batch/job-00.outcome.json')==row,'Outcome copy differs')
    require(h.read(exp/'batch/job-00.pending.json')==dict(id=ident,automatic_retry=False),'Pending job differs')
    require({p.name for p in trace.iterdir()}=={name+'.json' for name in ('artifact_hashes','backend','started',
        'g001.pending','g001.failure','api-g001.capacity','api-g001.pending','api-g001.failure','transport-001')},
        'Unexpected response/call records')
    transport=h.read(trace/'transport-001.json')
    require(transport==dict(billing_unknown=True,error_type='URLError',transport_status='TRANSPORT_ERROR'),
            'Not the supported network failure')
    pending=h.read(trace/'api-g001.pending.json'); payload=preview.payload(job['envelope']['messages'])
    require(pending['payload']==payload and pending['automatic_retry'] is False,'Payload differs')
    capacity=ActionCapacity(job['envelope'],counter)(payload)
    require(capacity==h.read(trace/'api-g001.capacity.json') and pending['payload_sha256']==capacity['payload_sha256'],
            'Capacity/payload binding differs')
    guard=CostGuard(ceiling_micro_cny=500000,max_calls=48,input_rate=2,output_rate=8)
    require(guard.reserve(capacity['reference_input_tokens'],512)==pending['ticket'],'Reservation differs')
    require(h.read(trace/'api-g001.failure.json')==dict(error_type='RuntimeError',billing_unknown=True,
        automatic_retry=False,guard=guard.snapshot()),'Failed reservation differs')
    guard.terminal=True
    require(summary['cost_guard']==guard.snapshot(),'Uncertain cost accounting differs')
    closure=h.read(tools/'summary.json')
    require(row['tools']==closure and closure['status']=='TOOL_SESSION_CLOSED' and closure['attempts']==0
            and closure['sql_calls']==0 and closure['spent_declared_units']==0,'Unexpected tool activity')
    require(not list(tools.glob('attempt-*')),'Unexpected tool attempts')
    require(h.snapshot(root)==before,'Artifacts changed during failure audit')
    result=dict(status='FIRST_TRANSPORT_FAILURE_AUDITED_NO_MODEL_RESPONSE',
        prepared_manifest_sha256=args.manifest_sha256,experiment_manifest_sha256=h.sha(exp/'artifact_hashes.json'),
        dispatch_attempts=1,received_model_responses=0,tool_attempts=0,not_started_trajectories=11,
        cost_guard=guard.snapshot(),provider_invoice_verified=False,semantic_labels=0,new_API_calls=0,
        failure=transport,rows=summary['rows'],
        interpretation='Transport error, not evidence of invalid key, task failure, intent drift or method performance.',
        retry_authorized_by_this_audit=False,
        analysis_source_files={'scripts/audit_cache_action_failure.py':h.sha(__file__),
                               'scripts/audit_cache_action.py':h.sha(helper)})
    result=h.archive(root,args.output,result,before)
    print(__import__('json').dumps({k:result[k] for k in ('status','dispatch_attempts','received_model_responses',
          'tool_attempts','not_started_trajectories','cost_guard','backup')},indent=2))


if __name__=='__main__': main()
