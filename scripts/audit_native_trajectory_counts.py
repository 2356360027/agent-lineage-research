"""Offline counter check: real initial inputs plus explicitly synthetic history.

No provider calls, execution, authorization or scientific results. Never replaces
recorded model actions. The synthetic continuation is the same across all inputs.
"""
import argparse
import json
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime.native_trajectory_counter import NativeTrajectoryCounter
from runtime.native_action_history import NativeHistory
from scripts.run_cache_workflow import counter_for


def run(root, output):
    root, output = Path(root), Path(output)
    manifest = json.loads((root/'manifest.json').read_text('utf-8'))
    counter_for(manifest)  # Verify pinned tokenizer package before use.
    counter = NativeTrajectoryCounter(root/'reference', manifest['tokenizer'])
    plan = json.loads((root/'plan.json').read_text('utf-8'))
    rows = []
    for job, expected in zip(plan['jobs'], manifest['input_tokens'], strict=True):
        history = NativeHistory(job['payload'], observation_byte_limit=1024)
        for turn in range(1,5):
            payload = history.begin()['payload']
            count = counter.inspect(payload)
            if turn == 1 and count['reference_input_tokens'] != expected:
                raise ValueError('First-request regression')
            rows.append(dict(id=job['id'], turn=turn,
                history_kind='ORIGINAL_INITIAL_INPUT' if turn==1 else 'SYNTHETIC_CONTINUATION_NOT_MODEL',
                **count))
            if turn<4:
                call_id=f'scripted-{turn}'
                fake={'choices':[{'index':0,'finish_reason':'tool_calls','message':{
                    'role':'assistant','content':None,'tool_calls':[{
                        'id':call_id,'type':'function','function':{
                            'name':'run_sql','arguments':'{"sql":"SELECT 1"}'}}]}}]}
                history.accept(json.dumps(fake))
                history.observe(call_id,'{"status":"SCRIPTED_ONLY","rows":[[1]]}')
    output.mkdir(parents=True,exist_ok=False)
    receipt=dict(status='OFFLINE_NATIVE_TRAJECTORY_COUNT_CHECK',rows=rows,
                 initial_contexts=len(plan['jobs']), synthetic_continuation_contexts=len(rows)-len(plan['jobs']),
                 first_counts_unchanged=True, new_API_calls=0, tool_executions=0,
                 multi_turn_provider_parity_verified=False, executable_upstream_parity_tested=False,
                 whole_horizon_worst_case_cost_established=False)
    audit.save_new(output/'counts.json',receipt)
    files=['runtime/native_trajectory_counter.py','runtime/native_action_history.py',
           'runtime/native_action_counter.py','runtime/native_action_interface.py',
           'scripts/audit_native_trajectory_counts.py']
    project=Path(__file__).resolve().parents[1]
    audit.save_new(output/'provenance.json',dict(
        source_hashes={f:audit.hash_file(project/f) for f in files},
        input_plan_sha256=audit.hash_file(root/'plan.json'),
        prepared_manifest_sha256=audit.hash_file(root/'manifest.json')))
    audit.save_new(output/'artifact_hashes.json',{f.name:audit.hash_file(f) for f in output.iterdir() if f.is_file()})
    return dict(status=receipt['status'], initial_contexts=receipt['initial_contexts'],
                synthetic_continuation_contexts=receipt['synthetic_continuation_contexts'],
                first_counts_unchanged=True, new_API_calls=0,
                input_token_range=[min(r['reference_input_tokens'] for r in rows),max(r['reference_input_tokens'] for r in rows)],
                audit_sha256=audit.hash_file(output/'counts.json'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared',required=True); p.add_argument('--output',required=True)
    a=p.parse_args(); print(json.dumps(run(a.prepared,a.output),indent=2))
