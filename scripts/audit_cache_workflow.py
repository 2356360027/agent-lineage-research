"""Offline audit of a completed first-action batch; never dispatches or repairs.

Uses the hash-checked frozen parser and payload builder, not current experiment
code. Integrity is not remote attestation or independent semantic validation.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_tree(root):
    root = Path(root)
    entries = list(root.rglob('*'))
    require(not any(p.is_symlink() for p in entries), 'Unexpected link')
    actual = {p.relative_to(root).as_posix(): sha(p) for p in entries
              if p.is_file() and p != root/'artifact_hashes.json'}
    require(actual == read(root/'artifact_hashes.json'), 'Artifact manifest mismatch')
    return len(actual)


def audit_run(root, expected_sha):
    root = Path(root).resolve()
    require(sha(root/'manifest.json') == expected_sha, 'Preparation hash mismatch')
    manifest = read(root/'manifest.json')
    for name, digest in manifest['source_files'].items():
        require(sha(root/'source'/name) == digest, 'Frozen source changed')
    require(not any(n.startswith(('runtime.', 'scripts.')) for n in sys.modules),
            'Run this audit as a standalone script to avoid mixed imports')
    sys.path.insert(0, str(root/'source'))
    from scripts.run_cache_workflow import verify
    from runtime import cache_workflow_diagnostic as workflow
    from runtime.cache_intent_journal import verify as verify_journal
    from runtime.cache_intent_staged_loop import parse_action
    from runtime.bird_readonly_audit import digest
    from runtime.deepseek_trajectory_adapter import CostGuard
    _, plan, counter = verify(root, expected_sha)
    exp = root/'experiment'
    files = verify_tree(exp)
    summary = read(exp/'summary.json')
    require(summary == read(exp/'batch/summary.json'), 'Summary copies differ')
    require(plan == read(exp/'batch/plan.json'), 'Executed plan differs')
    require(summary['status'] == 'OFFICIAL_DEVELOPMENT_FINISHED', 'Not a finished batch')
    claim = read(root/'execution_claim.json')
    require(claim['manifest_sha256'] == expected_sha and claim['approved_cny'] == .30,
            'Execution claim differs')
    require({p.name for p in exp.iterdir() if p.is_dir()} ==
            {'batch'} | {j['id'] for j in plan['jobs']}, 'Unexpected job directories')
    for folder in [exp/'batch'] + [exp/j['id'] for j in plan['jobs']]:
        verify_journal(folder)
    guard = CostGuard(ceiling_micro_cny=300000, max_calls=12, input_rate=2, output_rate=8)
    rows, inputs, outputs, elapsed, syntax_errors = [], 0, 0, 0.0, []
    for job in plan['jobs']:
        folder = exp/job['id']
        phases = ['contract', 'action'] if job['workflow'] == 'contract_first' else ['action']
        contract = None
        row = dict(id=job['id'], history=job['history'], workflow=job['workflow'],
                   available=job['available'], status='NOT_STARTED', action=None, dispatches=0)
        for index, phase in enumerate(phases, 1):
            pending = read(folder/(phase+'.pending.json'))
            provider = read(folder/(phase+'.provider.json'))
            raw = provider['raw']
            transport = read(folder/f'transport-{index:03d}.json')
            require(transport['transport_status'] == 'RESPONSE_RECEIVED' and
                    json.loads(transport['response_text']) == raw, 'Transport/provider mismatch')
            p = workflow.payload(job, contract)
            require(pending['payload'] == p and pending['payload_sha256'] == digest(p),
                    'Payload or raw contract carry-forward mismatch')
            n = counter.chat(p['messages'])
            require(0 < n <= 8192 and pending['input_tokens'] == n, 'Input count mismatch')
            ticket = guard.reserve(n, 512)
            require(ticket == pending['ticket'] and pending['automatic_retry'] is False,
                    'Dispatch accounting mismatch')
            usage = raw['usage']
            require(raw['model'] == 'deepseek-flash' and usage['prompt_tokens'] == n and
                    usage['completion_tokens'] <= 512, 'Provider usage/model mismatch')
            guard.settle(ticket, usage)
            require(guard.snapshot() == read(folder/(phase+'.cost.json')), 'Cost snapshot mismatch')
            inputs += n
            outputs += usage['completion_tokens']
            elapsed += provider['wall_seconds']
            row['dispatches'] += 1
            require(len(raw['choices']) == 1, 'Unexpected response choices')
            choice = raw['choices'][0]
            message = choice['message']
            require(not any(message.get(k) for k in
                    ('reasoning_content', 'tool_calls', 'function_call', 'refusal')),
                    'Unsupported output fields')
            text = message['content']
            require(counter.text(text) <= 512, 'Output cap exceeded')
            try:
                require(choice['finish_reason'] == 'stop', 'Unfinished output')
                if phase == 'contract':
                    workflow.parse_contract(text)
                    contract = text
                else:
                    action = parse_action(text)
                    row.update(status='ACTION_RECORDED', action=action['kind'],
                        unavailable_cache_requested=action['kind'] == 'read_cache' and not job['available'])
            except (ValueError, TypeError) as error:
                row['status'] = 'INVALID_CONTRACT' if phase == 'contract' else 'INVALID_ACTION'
                syntax_errors.append(dict(id=job['id'], phase=phase, error_type=type(error).__name__))
                break
        require(row == read(folder/'outcome.json'), 'Outcome cannot be reproduced')
        require(len(list(folder.glob('transport-*.json'))) == row['dispatches'], 'Extra transport records')
        rows.append(row)
    require(rows == summary['rows'], 'Rows differ from reconstructed results')
    require(guard.snapshot() == summary['cost_guard'] and guard.calls == summary['dispatch_attempts'],
            'Summary accounting differs')
    require(summary['tool_executions'] == 0 and summary['semantic_labels'] == 0 and
            summary['confirmation_study'] is False and summary['stop_reason'] is None,
            'Unsupported summary claims')
    return dict(status='OFFLINE_INTEGRITY_AND_FIRST_ACTION_AUDIT_PASSED',
        prepared_manifest_sha256=expected_sha, experiment_manifest_sha256=sha(exp/'artifact_hashes.json'),
        summary_sha256=sha(exp/'summary.json'), verified_experiment_files=files,
        calls=guard.calls, prompt_tokens=inputs, completion_tokens=outputs,
        summed_provider_wall_seconds=elapsed, cost_guard=guard.snapshot(), rows=rows,
        action_counts=dict(Counter(r['action'] for r in rows if r['status']=='ACTION_RECORDED')),
        invalid_outputs=syntax_errors, independent_development_tasks=2,
        new_API_calls=0, SQL_executions=0, semantic_labels=0,
        conclusion='Seven valid run_sql proposals, one invalid action, no valid cache-read request. No repairs or replacements.',
        limits=['Two reused development tasks, not eight independent tasks.',
                'Invalid output remains in denominator; no inference of its intended action.',
                'First proposals only; SQL and final answers were not executed/evaluated.',
                'Workflow bundles schema guidance, extra call and generated context.',
                'Both workflows see historical cache description before generation.',
                'Does not establish interpretation drift, benefit of a method, or general absence of an effect.',
                'Client records and hashes are not provider invoice or remote weights attestation.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared', required=True, type=Path)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    require(not args.output.exists(), 'Never overwrite an audit/archive')
    result = audit_run(args.prepared, args.manifest_sha256)
    args.output.mkdir(parents=True, exist_ok=False)
    archive = args.output/'prepared-and-results.zip'
    entries = sorted(p for p in args.prepared.rglob('*') if p.is_file())
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for path in entries:
            z.write(path, path.relative_to(args.prepared).as_posix())
    with zipfile.ZipFile(archive) as z:
        require(z.testzip() is None, 'Archive CRC failed')
        require(len(z.namelist()) == len(entries), 'Archive inventory differs')
        for path in entries:
            require(hashlib.sha256(z.read(path.relative_to(args.prepared).as_posix())).hexdigest()
                    == sha(path), 'Archive content hash failed')
    result['backup'] = dict(file=archive.name, sha256=sha(archive), verified_files=len(entries),
                          off_device=False)
    with (args.output/'audit.json').open('x', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == '__main__':
    main()
