"""Acquire one legacy official SQLite member and inspect DDL only; no models.

Reuses verified metadata. Never overwrites/resumes an existing output. This is
not a corrected Arcwise snapshot certification or human task admission.
"""
import argparse
import json
from pathlib import Path
import shutil

from scripts import fetch_bird_fixed as fetch
from scripts.audit_cache_action import snapshot
from runtime import bird_readonly_audit as bird
from runtime.arcwise_compatibility import verify_arcwise

SOURCE_SHA = 'baefe2ca4fbab86c000df72aad9eaa563f7d422e425af2cbff071f656ea4eea8'
ENTRY = 'minidev/MINIDEV/dev_databases/student_club/student_club.sqlite'


def run(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    fetch.absent(output)
    if not output.is_relative_to(project/'runtime/runs/datasets'):
        raise ValueError('New private dataset directory required')
    old = project/'runtime/runs/datasets/bird-mini-fixed-20261005-002'
    metadata = fetch.validate_metadata(old)
    tasks = project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001'
    _, records = verify_arcwise(tasks)
    if bird.hash_file(tasks/'arcwise_plat_full_with_diff.json') != SOURCE_SHA:
        raise ValueError('Question source changed')
    if any(records[i]['db_id'] != 'student_club' for i in (1317, 1371)):
        raise ValueError('Task database changed')
    selected = [e for e in fetch.read_json(old/'zip_inventory.json')['entries'] if e['filename']==ENTRY]
    if len(selected)!=1 or selected[0]['bytes']!=2641920 or selected[0]['compressed_bytes']!=614801:
        raise ValueError('Unexpected member/size')
    output.mkdir(parents=True, exist_ok=False)
    try:
        for name in ['source_manifest.json', *metadata['files'], *metadata['legacy_question_files']]:
            shutil.copyfile(old/name, output/name)
        for name, path in [('prepare_source.py', Path(__file__)), ('fetch_source.py', Path(fetch.__file__)),
                           ('readonly_source.py', Path(bird.__file__))]:
            shutil.copyfile(path, output/name)
        fetch.write_json_exclusive(output/'selected_db_ids.json', ['student_club'])
        bird.save_new(output/'preparation.json', dict(task_source_sha256=SOURCE_SHA,
            status='LEGACY_SNAPSHOT_ACQUISITION_NOT_SEMANTIC_ADMISSION',
            metadata_origin='Verified byte copies from bird-mini-fixed-20261005-002, not newly fetched metadata',
            selected_member=selected[0], model_calls=0, formal_tasks_admitted=0,
            compatibility='Legacy BIRD SQLite; corrected Arcwise database identity unverified',
            license_card_sha256=metadata['files']['README.md']['sha256'],
            license_note='Pinned dataset card declares CC-BY-SA-4.0; raw data remains private'))
        acquired = fetch.databases_stage(output, timeout=20)
        db = output/'databases/student_club/student_club.sqlite'
        before = fetch.file_receipt(db)
        query = "SELECT name, sql FROM sqlite_schema WHERE type='table' AND name IN ('event','attendance','member') ORDER BY name"
        bird.save_new(output/'schema.pending.json', dict(sql=query, limits=bird.LIMITS,
            database_before=before, automatic_retry=False))
        response = bird.run_child(db, query)
        bird.save_new(output/'schema.response.json', response)
        after = fetch.file_receipt(db)
        bird.save_new(output/'database_after.json', dict(receipt=after, unchanged=before==after))
        if before!=after or not response.get('complete'):
            raise ValueError('DDL check failed or database changed')
        result = dict(status='LEGACY_SNAPSHOT_AND_DDL_VERIFIED_NOT_ADMITTED',
            database=before, readonly_calls=1, model_calls=0, formal_tasks_admitted=0,
            acquisition=acquired, original_reference_queries_executed=False)
        bird.save_new(output/'preparation_summary.json', result)
    except BaseException as exc:
        bird.save_new(output/'preparation_failure.json', dict(type=type(exc).__name__,
            message=str(exc), automatic_retry=False))
        raise
    finally:
        bird.save_new(output/'preparation_artifact_hashes.json', snapshot(output))
    observed=snapshot(output); expected=observed.pop('preparation_artifact_hashes.json')
    if observed!=fetch.read_json(output/'preparation_artifact_hashes.json'):
        raise ValueError('Preparation seal mismatch')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],args.output),indent=2))
