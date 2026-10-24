"""Offline recovery of an interrupted launcher's already sealed experiment.

Never executes a worker, requests credentials, retries calls, or edits the run.
The caller must first establish that the worker is no longer running. File
stability checks are not a substitute for that process check.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile

from runtime import bird_readonly_audit as audit
from scripts.fetch_bird_fixed import no_link_ancestors
from scripts.prepare_staged_contract_review import load_archive
from scripts.run_deepseek_recovery import verify_prepared


def file_hashes(root):
    result = {}
    for path in sorted(root.rglob('*')):
        no_link_ancestors(path)
        if path.is_file():
            result[path.relative_to(root).as_posix()] = audit.hash_file(path)
    return result


def verify_tar(archive, prefix, expected):
    seen = set()
    with tarfile.open(archive, 'r:gz') as stream:
        for member in stream:
            if member.isdir():
                continue
            name = member.name.removeprefix(prefix + '/')
            if (not member.name.startswith(prefix + '/') or not member.isfile()
                    or name not in expected or name in seen):
                raise ValueError('Unexpected archive member')
            with stream.extractfile(member) as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            if digest != expected[name]:
                raise ValueError('Archive hash mismatch')
            seen.add(name)
    if seen != set(expected):
        raise ValueError('Incomplete archive')
    return len(seen)


def recover(root, manifest_sha, output, *, worker_stopped_confirmed=False):
    if not worker_stopped_confirmed:
        raise ValueError('Explicit prior process check required')
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError('Recovery artifacts must remain outside original run')
    no_link_ancestors(output)
    verify_prepared(root, manifest_sha)
    claim = json.loads((root/'execution_claim.json').read_text('utf-8'))
    if claim.get('manifest_sha256') != manifest_sha or claim.get('approved_cny') != 8:
        raise ValueError('Execution claim mismatch')
    experiment = root/'experiment'
    artifact_sha = audit.hash_file(experiment/'artifact_hashes.json')
    raw, _ = load_archive(experiment, artifact_sha)
    summary = json.loads((experiment/'summary.json').read_text('utf-8'))
    if summary != json.loads(raw['batch/summary.json']):
        raise ValueError('Inconsistent terminal summaries')
    if (summary.get('status') != 'OFFICIAL_UNREVIEWED_FINISHED'
            or len(summary.get('rows', [])) != 12
            or any(r['status'] == 'NOT_STARTED' for r in summary['rows'])):
        raise ValueError('This recovery tool only archives completed sealed batches')
    before = file_hashes(root)
    output.mkdir(parents=True, exist_ok=False)
    audit.save_new(output/'original_file_hashes.json', before)
    archive = output/(root.name + '.tar.gz')
    with tarfile.open(archive, 'x:gz') as stream:
        stream.add(root, arcname=root.name)
    verified = verify_tar(archive, root.name, before)
    if file_hashes(root) != before:
        raise ValueError('Source changed during offline recovery; preserve attempt')
    receipt = {
        'status': 'OFFLINE_ARCHIVE_VERIFIED_ORIGINAL_LAUNCHER_INTERRUPTED',
        'utc': datetime.now(timezone.utc).isoformat(),
        'prepared_manifest_sha256': manifest_sha,
        'experiment_artifact_manifest_sha256': artifact_sha,
        'archive_sha256': audit.hash_file(archive),
        'archive_bytes': archive.stat().st_size,
        'verified_files': verified,
        'original_files_modified': False,
        'original_execution_outcome_present': 'execution_outcome.json' in before,
        'worker_exit_code_known': False,
        'prior_process_check_asserted_by_caller': True,
        'recorded_experiment_status': summary['status'],
        'semantic_correctness_established': False,
        'provider_invoice_verified': False,
        'raw_private_archive': True,
        'new_model_calls': 0,
        'new_network_requests': 0,
        'automatic_retry': False,
    }
    audit.save_new(output/'receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('run', 'manifest-sha', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--worker-stopped-confirmed', action='store_true')
    args = parser.parse_args()
    print(json.dumps(recover(args.run, args.manifest_sha, args.output,
                            worker_stopped_confirmed=args.worker_stopped_confirmed), indent=2))


if __name__ == '__main__':
    main()
