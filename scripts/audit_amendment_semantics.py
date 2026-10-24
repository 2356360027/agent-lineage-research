"""Compare archived synthetic SQL rows to an independent Python definition.

No SQL re-execution or model inference. Finite software checks do not establish
human intent, universal equivalence, or a contribution over view selection.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

from runtime.bird_readonly_audit import hash_file, save_new
from scripts.audit_cache_action import no_links, snapshot
from scripts import audit_native_positive_relation as old

OLD_SEAL = 'a86e310537bd202c8be792245a9bda575c3b9b7480810418d25d8c955f218e08'
AMENDMENT_SEAL = '3c88f748c3377f828fe61fdc4f6f01bd85a5642511dd5b6975bf704dac348511'


def verify_sealed(directory, seal):
    no_links(directory)
    manifest = directory/'artifact_hashes.json'
    if hash_file(manifest) != seal: raise ValueError('Unexpected archive seal')
    actual = snapshot(directory); actual.pop('artifact_hashes.json')
    if actual != json.loads(manifest.read_text(encoding='utf-8')):
        raise ValueError('Archive file changed')


def expected_groups(colours, publishers, heroes):
    # Reject unsupported schemas instead of silently collapsing duplicate IDs.
    for rows in (colours, publishers, heroes):
        ids = [row[0] for row in rows]
        if any(type(i) is not int for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('Expected non-null unique integer primary keys')
    for rows in (colours, publishers):
        if any(value is not None and type(value) is not str for _, value in rows):
            raise ValueError('Expected text or NULL names')
    colour_by_id = dict(colours)
    eligible_publishers = {i for i, name in publishers if name == 'Marvel Comics'}
    counts = Counter()
    for hero_id, colour_id, publisher_id in heroes:
        if publisher_id in eligible_publishers and colour_id in colour_by_id:
            counts[colour_by_id[colour_id]] += 1
    return [(name, count, 1 + sum(other > count for other in counts.values()))
            for name, count in counts.items()]


def rows_match(expected, actual):
    if any(len(row) != 3 or (row[0] is not None and type(row[0]) is not str)
           or type(row[1]) is not int or type(row[2]) is not int for row in actual):
        return False
    return Counter(map(tuple, expected)) == Counter(map(tuple, actual))


def run(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    no_links(output)
    if output.exists(): raise ValueError('Refuse overwrite')
    archive = project/'runtime/runs/native-positive-relation-20261025-001'
    amendment = project/'runtime/runs/superhero-amendment-review-20261025-001'
    verify_sealed(archive, OLD_SEAL)
    verify_sealed(amendment, AMENDMENT_SEAL)
    if hash_file(old.__file__) != hash_file(archive/'audit_source.py'):
        raise ValueError('Fixture generator differs from archived source')
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, output/'checker_source.py')
    shutil.copyfile(archive/'audit_source.py', output/'fixture_source.py')
    shutil.copyfile(amendment/'adaptation.json', output/'adaptation.json')
    save_new(output/'design.json', dict(old_seal=OLD_SEAL, amendment_seal=AMENDMENT_SEAL,
        expected_fixture_count=74, new_SQL_queries=0, model_calls=0,
        purpose='Independent Python implementation of amended history semantics against already archived synthetic SQL results'))
    try:
        results = []
        for name, colours, publishers, heroes in old.cases():
            actual = json.loads((archive/(name+'.json')).read_text(encoding='utf-8'))
            if actual['case'] != name: raise ValueError('Fixture identity mismatch')
            expected = expected_groups(colours, publishers, heroes)
            results.append(dict(case=name, expected=expected, archived_rows=actual['source_rows'],
                                matches=rows_match(expected, actual['source_rows'])))
        save_new(output/'checks.json', results)
        summary = dict(status='ARCHIVED_SYNTHETIC_SEMANTIC_CHECK_COMPLETE',
            checked=len(results), matches=sum(r['matches'] for r in results),
            new_SQL_queries=0, model_calls=0, new_human_approvals=0,
            universal_equivalence_proven=False, current_733_semantics_admitted=False,
            method_increment_demonstrated=False)
        save_new(output/'summary.json', summary)
        if len(results) != 74 or not all(r['matches'] for r in results):
            raise ValueError('Semantic mismatch; no filtering or replacement')
    except BaseException as exc:
        save_new(output/'failure.json', dict(type=type(exc).__name__, message=str(exc)))
        raise
    finally:
        save_new(output/'artifact_hashes.json', snapshot(output))
    verify_sealed(output, hash_file(output/'artifact_hashes.json'))
    return dict(summary, manifest_sha256=hash_file(output/'artifact_hashes.json'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1], args.output), indent=2))
