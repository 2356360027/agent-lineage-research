"""Synthetic SQLite relation tests, not real-data execution or task admission.

Uses unmodified pinned reference SQL for 728 and 733. This is a classical
group-selection baseline check, not a proposed research method. Assumes SQLite
default BINARY text collation; actual schema and human meaning remain unverified.
"""
import argparse
import json
import random
import shutil
import sqlite3
from pathlib import Path

from runtime.arcwise_compatibility import verify_arcwise
from runtime import bird_readonly_audit as bird
from scripts.audit_cache_action import no_links, snapshot

SOURCE_SHA = 'baefe2ca4fbab86c000df72aad9eaa563f7d422e425af2cbff071f656ea4eea8'
SCHEMA = '''
CREATE TABLE colour(id INTEGER PRIMARY KEY, colour TEXT COLLATE BINARY);
CREATE TABLE publisher(id INTEGER PRIMARY KEY, publisher_name TEXT COLLATE BINARY);
CREATE TABLE superhero(id INTEGER PRIMARY KEY, eye_colour_id INTEGER, publisher_id INTEGER);
'''


def gold_count(columns, rows, *, complete):
    """Trusted complete grouped output only; never infer absent rows from top-k."""
    if complete is not True:
        raise ValueError('Incomplete grouped output cannot certify absence')
    if list(columns) != ['EyeColor', 'Count', 'PopularityRank']:
        raise ValueError('Unexpected schema')
    seen = set()
    answer = 0
    for row in rows:
        if len(row) != 3:
            raise ValueError('Invalid row width')
        colour, count, rank = row
        if colour is not None and not isinstance(colour, str):
            raise ValueError('Unexpected colour type')
        if colour in seen:
            raise ValueError('Duplicate group')
        seen.add(colour)
        if type(count) is not int or count < 0 or type(rank) is not int or rank < 1:
            raise ValueError('Invalid count/rank')
        if colour == 'Gold':
            answer = count
    return answer


def cases():
    colours = [(1, 'Gold'), (2, 'Blue'), (3, None), (4, 'gold'), (5, 'Gold')]
    publishers = [(1, 'Marvel Comics'), (2, 'DC Comics'), (3, 'Marvel Comics')]
    hand = {
        'empty': [],
        'gold_absent': [(1, 2, 1)],
        'gold_only': [(1, 1, 1), (2, 1, 1)],
        'gold_not_top_rank': [(1, 1, 1), (2, 2, 1), (3, 2, 1)],
        'tied_counts': [(1, 1, 1), (2, 2, 1)],
        'case_sensitive': [(1, 4, 1), (2, 1, 1)],
        'duplicate_colour_labels': [(1, 1, 1), (2, 5, 1)],
        'duplicate_publisher_names': [(1, 1, 1), (2, 1, 3)],
        'null_and_unmatched_joins': [(1, None, 1), (2, 999, 1), (3, 1, None), (4, 1, 999), (5, 3, 1)],
        'other_publisher': [(1, 1, 2), (2, 2, 1)],
    }
    for name, heroes in hand.items():
        yield name, colours, publishers, heroes
    for seed in range(64):
        rng = random.Random(seed)
        heroes = [(i + 1, rng.choice([None, 1, 2, 3, 4, 5, 999]),
                   rng.choice([None, 1, 2, 3, 999])) for i in range(rng.randrange(81))]
        yield f'fixed-seed-{seed}', colours, publishers, heroes


def compare_fixture(history_sql, current_sql, fixture):
    name, colours, publishers, heroes = fixture
    con = sqlite3.connect(':memory:')
    try:
        con.executescript(SCHEMA)
        con.executemany('INSERT INTO colour VALUES (?,?)', colours)
        con.executemany('INSERT INTO publisher VALUES (?,?)', publishers)
        con.executemany('INSERT INTO superhero VALUES (?,?,?)', heroes)
        con.execute('PRAGMA query_only=ON')
        cur = con.execute(history_sql)
        columns = [x[0] for x in cur.description]
        rows = cur.fetchall()
        direct = con.execute(current_sql).fetchall()
        derived = gold_count(columns, rows, complete=True)
        return {'case': name, 'source_rows': rows, 'derived': derived,
                'direct_rows': direct, 'matches': direct == [(derived,)]}
    finally:
        con.close()


def run(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    no_links(output)
    if output.exists():
        raise ValueError('Refuse overwrite')
    source_dir = project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001'
    _, records = verify_arcwise(source_dir)
    source_path = source_dir/'arcwise_plat_full_with_diff.json'
    if bird.hash_file(source_path) != SOURCE_SHA:
        raise ValueError('Unexpected source version')
    if any(records[i]['db_id'] != 'superhero' for i in (728, 733)):
        raise ValueError('Unexpected database')
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, output/'audit_source.py')
    shutil.copyfile(project/'configs/native_positive_screen_20261025.json', output/'screen.json')
    bird.save_new(output/'source_records.json', [records[i] for i in (728, 733)])
    bird.save_new(output/'design.json', {
        'purpose': 'Reference-query conversion on synthetic in-memory SQLite fixtures only',
        'source_sha256': SOURCE_SHA, 'sqlite_version': sqlite3.sqlite_version,
        'schema': SCHEMA, 'fixed_seeds': list(range(64)), 'hand_cases': 10,
        'expected_fixture_pairs': 74, 'expected_reference_selects': 148,
        'real_database_queries': 0, 'model_calls': 0, 'formal_tasks_admitted': 0,
        'scope': 'Default BINARY collation and complete results; fixture agreement is not a proof for every schema or human semantic validation',
    })
    try:
        results = []
        for fixture in cases():
            result = compare_fixture(records[728]['SQL'], records[733]['SQL'], fixture)
            results.append(result)
            bird.save_new(output/(result['case']+'.json'), result)
        summary = {
            'status': 'SYNTHETIC_RELATION_CHECK_COMPLETE',
            'fixture_pairs': len(results), 'matches': sum(r['matches'] for r in results),
            'reference_selects': 2*len(results), 'real_database_queries': 0,
            'model_calls': 0, 'formal_tasks_admitted': 0,
            'human_semantics_verified': False, 'official_database_verified': False,
            'method_gain_demonstrated': False,
        }
        bird.save_new(output/'summary.json', summary)
        if not all(r['matches'] for r in results):
            raise ValueError('Relation mismatch; preserve all fixtures, do not drop failures')
    except BaseException as exc:
        bird.save_new(output/'failure.json', {'type': type(exc).__name__, 'message': str(exc)})
        raise
    finally:
        hashes = snapshot(output)
        bird.save_new(output/'artifact_hashes.json', hashes)
    actual = snapshot(output)
    actual.pop('artifact_hashes.json')
    if actual != hashes:
        raise ValueError('Archive changed')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1], args.output), indent=2))
