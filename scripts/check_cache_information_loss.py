"""Finite synthetic witnesses for previously screened reference-query pairs.

No model, network or real database queries. A witness shows non-determination
over the explicitly supplied schema, not universal SQL verification, natural
task admission, or evidence of agent intent drift. Source SQL is not rewritten.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

SOURCE_SHA = 'baefe2ca4fbab86c000df72aad9eaa563f7d422e425af2cbff071f656ea4eea8'
PAIRS = ((537, 555, 'codebase_community'), (766, 786, 'superhero'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixtures(pair):
    if pair == (537, 555):
        schema = '''CREATE TABLE users(Id INTEGER PRIMARY KEY, DisplayName TEXT);
CREATE TABLE posts(Id INTEGER PRIMARY KEY, OwnerUserId INTEGER REFERENCES users(Id), Score INTEGER);'''
        common = [('INSERT INTO users VALUES (?,?)', [(1, 'csgillespie')])]
        return schema, [common + [('INSERT INTO posts VALUES (?,?,?)', [(1, 1, 2), (2, 1, 4)])],
                        common + [('INSERT INTO posts VALUES (?,?,?)', [(1, 1, 2), (2, 1, 8)])]]
    if pair == (766, 786):
        schema = '''CREATE TABLE superhero(id INTEGER PRIMARY KEY, full_name TEXT);
CREATE TABLE attribute(id INTEGER PRIMARY KEY, attribute_name TEXT UNIQUE);
CREATE TABLE hero_attribute(hero_id INTEGER REFERENCES superhero(id), attribute_id INTEGER REFERENCES attribute(id), attribute_value INTEGER, PRIMARY KEY(hero_id, attribute_id));'''
        common = [('INSERT INTO attribute VALUES (?,?)', [(1, 'Strength')])]
        return schema, [common + [('INSERT INTO superhero VALUES (?,?)', [(1, 'Shared Name')]),
                                  ('INSERT INTO hero_attribute VALUES (?,?,?)', [(1, 1, 100)])],
                        common + [('INSERT INTO superhero VALUES (?,?)', [(1, 'Shared Name'), (2, 'Shared Name')]),
                                  ('INSERT INTO hero_attribute VALUES (?,?,?)', [(1, 1, 100), (2, 1, 100)])]]
    raise ValueError('Not a preselected pair')


def evaluate(schema, data, history_sql, current_sql):
    con = sqlite3.connect(':memory:')
    try:
        con.execute('PRAGMA foreign_keys=ON')
        con.executescript(schema)
        for sql, rows in data:
            con.executemany(sql, rows)
        con.commit()
        if con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Invalid fixture foreign keys')
        con.execute('PRAGMA query_only=ON')
        result = {}
        for name, sql in (('history', history_sql), ('current', current_sql)):
            cursor = con.execute(sql)
            result[name] = dict(columns=[c[0] for c in cursor.description], rows=cursor.fetchall())
        return result
    finally:
        con.close()


def witness(history_sql, current_sql, pair):
    schema, variants = fixtures(pair)
    results = [evaluate(schema, data, history_sql, current_sql) for data in variants]
    same = results[0]['history'] == results[1]['history']
    different = results[0]['current'] != results[1]['current']
    return dict(schema=schema, fixtures=variants, results=results,
                same_history=same, different_current=different,
                is_information_loss_witness=same and different)


def run(source, output):
    source, output = Path(source), Path(output)
    if sha(source) != SOURCE_SHA:
        raise ValueError('Wrong source snapshot')
    if output.exists():
        raise ValueError('Refuse overwrite')
    records = json.loads(source.read_text('utf-8'))
    index = {int(r['question_id']): r for r in records}
    if len(records) != 498 or len(index) != len(records):
        raise ValueError('Unexpected source records')
    results = []
    for h, c, db in PAIRS:
        if index[h]['db_id'] != db or index[c]['db_id'] != db:
            raise ValueError('Wrong database')
        result = witness(index[h]['SQL'], index[c]['SQL'], (h, c))
        results.append(dict(history_id=h, current_id=c, database=db,
                            history_sql=index[h]['SQL'], current_sql=index[c]['SQL'], **result))
    report = dict(status='FINITE_SYNTHETIC_REFERENCE_WITNESSES', source_sha256=SOURCE_SHA,
                  script_sha256=sha(__file__), sqlite_version=sqlite3.sqlite_version,
                  pairs=results, synthetic_databases=4, reference_query_executions=8,
                  model_calls=0, real_database_queries=0, new_human_approvals=0,
                  formal_tasks_admitted=0,
                  limitation='Explicit reduced schemas, not a verified full official-schema embedding. '
                  'Human semantics and production incidence remain unverified. No claim that all other '
                  'information or extra database reads could not resolve the current task.')
    output.mkdir(parents=True, exist_ok=False)
    (output/'check_source.py').write_bytes(Path(__file__).read_bytes())
    with (output/'witnesses.json').open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with (output/'artifact_hashes.json').open('x', encoding='utf-8') as f:
        json.dump({p.name: sha(p) for p in sorted(output.iterdir()) if p.name != 'artifact_hashes.json'}, f, indent=2)
    if sha(source) != SOURCE_SHA:
        raise ValueError('Source changed')
    if not all(r['is_information_loss_witness'] for r in results):
        raise ValueError('Witness failed; preserve output, do not discard it')
    return dict(status=report['status'], witnessed_pairs=len(results), model_calls=0,
                real_database_queries=0, formal_tasks_admitted=0)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(run(args.source, args.output), indent=2))
