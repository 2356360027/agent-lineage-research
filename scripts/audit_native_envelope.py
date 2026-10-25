"""Post-hoc, offline envelope diagnosis only; no dispatch or original rescoring.

The frozen parser and run stay unchanged. A copied response has ONLY an optional
integer-zero tool-call index removed before all frozen-format checks are replayed.
This is an observed provider extension, not claimed documented non-stream schema.
"""
import argparse
import json
from pathlib import Path
import tarfile

from runtime import bird_readonly_audit as audit
from runtime.native_action_interface import _load, parse_native_response
from scripts.archive_completed_deepseek_batch import file_hashes, verify_tar
from scripts.run_cache_action import verify_archive


def inspect_envelope(raw):
    body = _load(raw)
    if type(body) is not dict:
        raise ValueError('Provider object required')
    choices = body.get('choices')
    if type(choices) is not list or len(choices) != 1:
        raise ValueError('Exactly one choice required')
    calls = choices[0].get('message', {}).get('tool_calls')
    if type(calls) is not list or len(calls) != 1 or type(calls[0]) is not dict:
        raise ValueError('Exactly one call required')
    call = calls[0]
    changed = 'index' in call
    if changed:
        if type(call['index']) is not int or call['index'] != 0:
            raise ValueError('Only integer zero index is supported')
        del call['index']
    parsed = parse_native_response(json.dumps(body, ensure_ascii=False, allow_nan=False))
    return dict(index_extension_present=changed, action_kind=parsed['action']['kind'],
                remaining_format_checks_pass=True, execution_authorized=False)


def diagnose(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError('Separate output required')
    verified = json.loads((root/'analysis/audit.json').read_text('utf-8'))
    if verified['status'] != 'SEALED_NATIVE_CONTEXT_REPLAY_VERIFIED':
        raise ValueError('Frozen audit required first')
    verify_archive(root/'experiment', verified['experiment_manifest_sha256'])
    before = file_hashes(root)
    summary = json.loads((root/'experiment/summary.json').read_text('utf-8'))
    rows = []
    prompt = completion = 0
    for original in summary['rows']:
        raw = json.loads((root/'experiment'/original['id']/'provider.json').read_text('utf-8'))['raw_response_text']
        try:
            parse_native_response(raw)
            old_reason = None
        except ValueError as exc:
            old_reason = str(exc)
        try:
            detail = inspect_envelope(raw)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            detail = dict(remaining_format_checks_pass=False, error_type=type(exc).__name__)
        usage = _load(raw)['usage']
        prompt += usage['prompt_tokens']; completion += usage['completion_tokens']
        rows.append(dict(id=original['id'], original_rejection=old_reason, **detail))
    report = dict(status='POST_HOC_ENVELOPE_DIAGNOSIS_ONLY', rows=rows,
                  original_readout=summary['readout'], cost_guard=summary['cost_guard'],
                  reported_prompt_tokens=prompt, reported_completion_tokens=completion,
                  new_API_calls=0, SQL_executions=0, tool_executions=0,
                  original_scores_modified=False, semantic_correctness_assessed=False,
                  prospective_interface_validated=False)
    output.mkdir(parents=True, exist_ok=False)
    audit.save_new(output/'diagnostic.json', report)
    audit.save_new(output/'source_file_hashes.json', before)
    bundle = output/'private-bundle.tar.gz'
    with tarfile.open(bundle, 'x:gz') as stream:
        stream.add(root, arcname=root.name)
    count = verify_tar(bundle, root.name, before)
    if file_hashes(root) != before:
        raise ValueError('Original run changed during archive')
    receipt = dict(archive_sha256=audit.hash_file(bundle), verified_files=count,
                   archive_bytes=bundle.stat().st_size, off_device=False,
                   original_files_modified=False, new_API_calls=0)
    audit.save_new(output/'backup_receipt.json', receipt)
    audit.save_new(output/'artifact_hashes.json', file_hashes(output))
    return dict(report=report, backup=receipt)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', required=True); p.add_argument('--output', required=True)
    args = p.parse_args()
    print(json.dumps(diagnose(args.run, args.output), indent=2))
