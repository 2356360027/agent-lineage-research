"""Private allow-listed backup of candidate preparation, including DNS failure.

No model, SQL or network. This preserves data, not a semantic approval.
"""
import argparse
import json
from pathlib import Path
import tarfile

from scripts.audit_cache_action import snapshot
from scripts.archive_completed_deepseek_batch import verify_tar
from scripts.fetch_bird_fixed import absent, no_link_ancestors, read_json
from runtime.bird_readonly_audit import hash_file, save_new

SEALED = {
    'runtime/runs/datasets/student-club-fixed-20261026-001': ('preparation_artifact_hashes.json', None),
    'runtime/runs/datasets/student-club-fixed-20261026-002': ('preparation_artifact_hashes.json', 'be4a94f661392566dacda976dd3bdc2f478448297b5d9b04e7cf3a0149f66be4'),
    'runtime/runs/student-club-structure-20261026-001': ('artifact_hashes.json', 'b244e5279fbb17a51cd08319f4705d59a3dfe86312534079c2a48b336f1db601'),
}
EXTRA = (
    'runtime/runs/student-club-review-20261026-001/INDEPENDENT_REVIEW.txt',
    'scripts/prepare_student_club_assets.py', 'scripts/audit_student_club_structure.py',
    'scripts/backup_student_club_candidate.py', 'scripts/fetch_bird_fixed.py',
    'runtime/bird_readonly_audit.py', 'runtime/arcwise_compatibility.py',
    'tests/test_student_club_structure.py',
    'configs/cache_next_domain_screen_20261026.json',
    'configs/run_records/student_club_structure_20261026_001.json',
    'configs/run_records/contract_origin_and_verifier_gate_20261026_001.json',
)


def run(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    absent(output)
    if not output.is_relative_to(project/'runtime/runs'):
        raise ValueError('Private destination required')
    expected = {}
    for rel, (seal, digest) in SEALED.items():
        root = project/rel
        files = snapshot(root)
        actual_seal = files.pop(seal)
        if (digest is not None and actual_seal != digest) or files != read_json(root/seal):
            raise ValueError('Candidate input seal mismatch')
        files[seal] = actual_seal
        expected.update({rel+'/'+name: sha for name, sha in files.items()})
    for rel in EXTRA:
        no_link_ancestors(project/rel)
        expected[rel] = hash_file(project/rel)
    output.mkdir(parents=True, exist_ok=False)
    save_new(output/'source_hashes.json', expected)
    bundle = output/'private-candidate.tar.gz'
    with tarfile.open(bundle, 'x:gz') as stream:
        for rel in sorted(expected):
            stream.add(project/rel, arcname='candidate/'+rel, recursive=False)
    verified = verify_tar(bundle, 'candidate', expected)
    if any(hash_file(project/rel)!=sha for rel,sha in expected.items()):
        raise ValueError('Source changed while archiving')
    receipt = dict(status='PRIVATE_CANDIDATE_BACKUP_VERIFIED', files=verified,
        bytes=bundle.stat().st_size, sha256=hash_file(bundle),
        model_calls=0, SQL_queries=0, human_approvals=0, original_files_modified=False,
        off_device=False, raw_uploaded=False,
        scope='Failed and successful acquisitions, SQLite database, structural records, blank review and selected code/configs; not a complete environment or all project history')
    save_new(output/'receipt.json', receipt)
    return receipt


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args=p.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1], args.output), indent=2))
