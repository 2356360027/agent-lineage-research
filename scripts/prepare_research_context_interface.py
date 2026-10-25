"""Offline-only draft builder. No API key, paid runner, tools or SQL."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime import bird_readonly_audit as audit
from runtime.research_context_interface import build_plan
from scripts.fetch_bird_fixed import no_link_ancestors
from scripts.run_cache_action import verify_archive
from scripts.run_cache_workflow import counter_for

SOURCES = {
    'positive': ('positive-symmetric-prepared-20261025-001',
                 '856d339da5de6b79fca6cba9453f2e673328dc8680d35352258ab5726e3298ff',
                 'd311f854241102cece854e6b7af28c76e713808f6b10002af5af91c9c5243d0c'),
    'strength': ('strength-symmetric-prepared-20261025-001',
                 '6083e0a39e1cebf9872cd54a3861c9d917b52da8093289e1cd1933044dbc0840',
                 '840dbc622a3f3d7666ac8fe729c02675eded92092e52cc5d20a189d5e6df60a4'),
}


def read(path):
    return json.loads(path.read_text('utf-8'))


def prepare(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    if not output.is_relative_to(project/'runtime/runs'):
        raise ValueError('Private inputs must remain in ignored runtime/runs')
    no_link_ancestors(output)
    if output.exists():
        raise ValueError('Never overwrite an existing draft')
    requests, provenance = {}, {}
    counter = None
    for task, (name, manifest_sha, seal) in SOURCES.items():
        root = project/'runtime/runs'/name
        if audit.hash_file(root/'manifest.json') != manifest_sha:
            raise ValueError('Source manifest mismatch')
        verify_archive(root/'experiment', seal)
        manifest = read(root/'manifest.json')
        if counter is None:
            counter = counter_for(manifest)
        else:
            counter_for(manifest)  # Verify this source tokenizer too.
        for i in range(4):
            path = root/'experiment'/f'cell-{i}'/'trace/api-g001.pending.json'
            ident = f'{task}-cell-{i}'
            requests[ident] = read(path)['payload']
            provenance[ident] = dict(prepared=name, manifest_sha256=manifest_sha,
                                    experiment_seal_sha256=seal, pending_file_sha256=audit.hash_file(path))
    plan = build_plan(requests)
    lengths = [dict(id=j['id'], input_tokens=counter.chat(j['payload']['messages'])) for j in plan['jobs']]
    if any(type(x['input_tokens']) is not int or not 0 < x['input_tokens'] <= 4000 for x in lengths):
        raise ValueError('Input bound exceeded; do not truncate')
    total = sum(x['input_tokens'] for x in lengths)
    # A conditional planning bound using previously recorded conservative rates,
    # not a current price quotation or authorization to send anything.
    receipt = dict(status='OFFLINE_CONTEXT_DRAFT_NOT_EXECUTABLE', maximum_calls=16,
                   development_task_pairs=2, dependent_contexts=8,
                   input_tokens_total=total, input_tokens_max=max(x['input_tokens'] for x in lengths),
                   output_token_cap_per_call=512, planning_rates_micro_cny_per_token=[2, 8],
                   conditional_micro_cny=total*2+16*512*8,
                   requires_fresh_price_check_and_paid_approval=True,
                   API_calls=0, SQL_calls=0, tools=0, old_results_changed=False)
    output.mkdir(parents=True, exist_ok=False)
    for name, value in [('PRIVATE_requests.json', requests), ('PRIVATE_plan.json', plan),
                        ('provenance.json', provenance), ('token_lengths.json', lengths), ('receipt.json', receipt)]:
        audit.save_new(output/name, value)
    for task, (name, _, seal) in SOURCES.items():
        verify_archive(project/'runtime/runs'/name/'experiment', seal)
    audit.save_new(output/'artifact_hashes.json', {p.name:audit.hash_file(p) for p in sorted(output.iterdir())})
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parents[1], args.output), indent=2))
