"""Offline readout and private verified tar backup of an already audited batch.

No network, SQL or inference. Requires externally supplied hashes of the frozen
manifest and the completed audit produced by that frozen launcher. This is not
a substitute for the launcher audit, human judgment or causal identification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import bird_readonly_audit as audit
from runtime.superhero_answer_readout import disposition, reference_integer
from scripts.fetch_bird_fixed import no_link_ancestors


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def inventory(root):
    out = {}
    no_link_ancestors(root)
    for p in root.rglob('*'):
        no_link_ancestors(p)
        if p.is_file():
            out[p.relative_to(root).as_posix()] = audit.hash_file(p)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--audit-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, output = args.prepared.resolve(), args.output.resolve()
    no_link_ancestors(output)
    if output.exists() or output.is_relative_to(root):
        raise ValueError('New output outside the sealed preparation required')
    if audit.hash_file(root/'manifest.json') != args.manifest_sha256:
        raise ValueError('Prepared manifest differs')
    if audit.hash_file(root/'analysis/audit.json') != args.audit_sha256:
        raise ValueError('Externally verified audit differs')
    checked = read(root/'analysis/audit.json')
    if checked['status'] != 'SEALED_NATIVE_BATCH_REPLAY_VERIFIED':
        raise ValueError('Only complete, verified batches can receive this readout')
    hashes = inventory(root/'experiment')
    hashes.pop('artifact_hashes.json')
    if hashes != read(root/'experiment/artifact_hashes.json'):
        raise ValueError('Experiment changed after audit')
    if audit.hash_file(root/'experiment/artifact_hashes.json') != checked['archive_sha256']:
        raise ValueError('Audit not bound to current experiment')
    for family in ('positive', 'strength'):
        sealed = inventory(root/'inputs'/family)
        sealed.pop('artifact_hashes.json')
        if sealed != read(root/'inputs'/family/'artifact_hashes.json'):
            raise ValueError('Reference input archive changed')
    expected = {f: reference_integer(read(root/'inputs'/f/'PRIVATE_reference.json'))
                for f in ('positive', 'strength')}
    plan = read(root/'PRIVATE_plan.json')
    if audit.hash_file(root/'PRIVATE_plan.json') != read(root/'manifest.json')['plan_file_sha256']:
        raise ValueError('Plan changed')
    jobs = {j['id']: j for j in plan['jobs']}
    items = checked['readouts']
    if len(items) != 8 or {r['id'] for r in items} != set(jobs):
        raise ValueError('All eight slots required')
    rows = []
    for item in items:
        identifier = item['id']; job = jobs[identifier]
        if item['family'] != job['family']:
            raise ValueError('Family changed')
        ctx = json.loads(job['envelope']['messages'][1]['content'])['context']
        trace = root/'experiment'/identifier/'loop'
        actions = []
        for n in range(1, item['tool_attempts']+1):
            pending = read(trace/f't{n:03d}.pending.json')
            exposure = read(trace/f't{n:03d}.exposure.json')['observation']
            actions.append(dict(tool=pending['tool'], arguments=pending['arguments'],
                                observed_status=exposure['status'],
                                charged_units=exposure['charged_units']))
        final = item['final_action']
        text = final.get('text') if final else None
        rows.append(dict(id=identifier, family=job['family'],
            available=ctx['materialized_available'], read_price=ctx['read_cost'],
            first_decision=read(trace/'g001.decision.json'),
            actions=actions, final_status=item['loop_status'], final_answer=text,
            automated_disposition=disposition(item['loop_status'], text, expected[job['family']])))
    result = dict(status='POST_RUN_DESCRIPTIVE_SCALAR_READOUT',
        prepared_manifest_sha256=args.manifest_sha256, audit_sha256=args.audit_sha256,
        rows=rows, bound_reference_scalars=expected, independent_confirmation=False,
        task_pairs=2, shared_database=True, planned_cells=8,
        model_calls_by_readout=0, SQL_executions_by_readout=0, human_reviews_added=0,
        C_type_drift_assessed=False, inferential_statistics=False,
        cost_guard=checked['cost_guard'], reported_prompt_tokens=checked['reported_prompt_tokens'],
        reported_completion_tokens=checked['reported_completion_tokens'],
        warning='Exact scalar match is not proof of intent, causal reliance or method superiority. '
                'Read-cache proposals are not successful reuse; relative tool prices are not CNY.')
    output.mkdir(parents=True, exist_ok=False)
    audit.save_new(output/'PRIVATE_readout.json', result)
    sources = {f'prepared/{rel}': (root/rel, sha) for rel, sha in inventory(root).items()}
    for label, p in [('PRIVATE_readout.json', output/'PRIVATE_readout.json'),
                     ('readout_source.py', Path(__file__).resolve())]:
        sources[label] = (p, audit.hash_file(p))
    bundle = output/'private-bundle.tar.gz'
    with bundle.open('xb') as raw:
        with tarfile.open(fileobj=raw, mode='w:gz') as tar:
            for name, (path, _) in sorted(sources.items()):
                tar.add(path, arcname=name, recursive=False)
    with tarfile.open(bundle, 'r:gz') as tar:
        members = tar.getmembers()
        if len(members) != len(sources) or {m.name for m in members} != set(sources):
            raise ValueError('Backup member inventory mismatch')
        for member in members:
            if not member.isfile(): raise ValueError('Non-file backup member')
            with tar.extractfile(member) as handle:
                value = hashlib.file_digest(handle, 'sha256').hexdigest()
            path, expected_sha = sources[member.name]
            if value != expected_sha or audit.hash_file(path) != expected_sha:
                raise ValueError('Backup mismatch or source changed during archiving')
    receipt = dict(status='PRIVATE_BACKUP_CONTENT_VERIFIED', files=len(sources),
        bundle_sha256=audit.hash_file(bundle), same_disk_only=True, off_device=False,
        original_files_modified=False, raw_uploaded=False,
        prepared_manifest_sha256=args.manifest_sha256, audit_sha256=args.audit_sha256,
        backup_scope='Prepared source, input records, all raw outputs and audit/readout; '
                     'external database and tokenizer weights remain referenced by frozen hashes.')
    audit.save_new(output/'backup_receipt.json', receipt)
    print(json.dumps(dict(status=receipt['status'], files=receipt['files'],
                          bundle_sha256=receipt['bundle_sha256'], new_model_calls=0), indent=2))


if __name__ == '__main__':
    main()
