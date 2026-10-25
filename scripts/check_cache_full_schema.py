"""Embed fixed information-loss witnesses in exact pinned SQLite schemas.

Reads only sqlite_schema on real read-only files. All inserted rows and task
queries use fresh in-memory databases. No model, paid API or human admission.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import time

from scripts.check_cache_information_loss import SOURCE_SHA, PAIRS, fixtures, sha

DATABASES = {
    'codebase_community': ('bird-mini-fixed-20261005-002/databases/codebase_community/codebase_community.sqlite',
                          '92101be6d2a9f6adceea59d38f6d1c556087f9eea1432432a21268cb1348036d'),
    'superhero': ('superhero-fixed-20261025-002/databases/superhero/superhero.sqlite',
                  '75e94a2c3236ee3bb2c01fb97a1c4b4c1c269bcefd4eab1d04be323d2d0825b1')}
SCHEMA_QUERY = 'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name,tbl_name'
INSERTS = {
    'INSERT INTO users VALUES (?,?)': 'INSERT INTO users(Id,DisplayName) VALUES (?,?)',
    'INSERT INTO posts VALUES (?,?,?)': 'INSERT INTO posts(Id,OwnerUserId,Score) VALUES (?,?,?)',
    'INSERT INTO attribute VALUES (?,?)': 'INSERT INTO attribute(id,attribute_name) VALUES (?,?)',
    'INSERT INTO superhero VALUES (?,?)': 'INSERT INTO superhero(id,full_name) VALUES (?,?)',
    'INSERT INTO hero_attribute VALUES (?,?,?)': 'INSERT INTO hero_attribute(hero_id,attribute_id,attribute_value) VALUES (?,?,?)'}


def check_path(path):
    p = Path(path).absolute()
    for q in (p, *p.parents):
        if q.is_symlink() or q.is_junction():
            raise ValueError('No links or junctions')


def schema_from_file(path, expected):
    check_path(path)
    if sha(path) != expected:
        raise ValueError('Database hash differs')
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Database sidecar present')
    con = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
    try:
        con.execute('PRAGMA query_only=ON')
        until = time.monotonic()+5
        con.set_progress_handler(lambda: int(time.monotonic()>until), 1000)
        schema = con.execute(SCHEMA_QUERY).fetchall()
    finally:
        con.close()
    if sha(path) != expected:
        raise ValueError('Database changed')
    return schema


def evaluate_full(schema, data, history_sql, current_sql):
    con = sqlite3.connect(':memory:')
    try:
        con.execute('PRAGMA foreign_keys=ON')
        for kind in ('table', 'index', 'view', 'trigger'):
            for typ, name, table, ddl in schema:
                if typ == kind and ddl is not None:
                    con.execute(ddl)
        if con.execute(SCHEMA_QUERY).fetchall() != schema:
            raise ValueError('Reconstructed schema differs')
        inserts = [(INSERTS[sql], rows) for sql, rows in data]
        for sql, rows in inserts:
            con.executemany(sql, rows)
        con.commit()
        fk = con.execute('PRAGMA foreign_key_check').fetchall()
        integrity = con.execute('PRAGMA integrity_check').fetchall()
        if fk or integrity != [('ok',)]:
            raise ValueError('Fixture violates full schema integrity')
        con.execute('PRAGMA query_only=ON')
        result = dict(explicit_column_inserts=inserts, foreign_key_violations=fk,
                      integrity=integrity, schema_exact=True)
        for label, sql in (('history', history_sql), ('current', current_sql)):
            cursor = con.execute(sql)
            result[label] = dict(columns=[c[0] for c in cursor.description], rows=cursor.fetchall())
        return result
    finally:
        con.close()


def run(project, output):
    project, output = Path(project).resolve(), Path(output).absolute()
    check_path(output)
    if output.exists():
        raise ValueError('Never overwrite output')
    source = project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001/arcwise_plat_full_with_diff.json'
    check_path(source)
    if sha(source) != SOURCE_SHA:
        raise ValueError('Wrong query source')
    records = json.loads(source.read_text('utf-8'))
    index = {int(r['question_id']): r for r in records}
    if len(index) != 498 or len(records) != 498:
        raise ValueError('Wrong source size')
    output.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        with (output/name).open('x', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
    (output/'check_source.py').write_bytes(Path(__file__).read_bytes())
    dependency = project/'scripts/check_cache_information_loss.py'
    (output/'fixture_source.py').write_bytes(dependency.read_bytes())
    save('design.json', dict(source_sha256=SOURCE_SHA, databases=DATABASES,
        pairs=PAIRS, purpose='Full-schema synthetic witnesses, not model evaluation',
        original_query_changes=False, model_calls=0, source_schema_query=SCHEMA_QUERY,
        sqlite_version=sqlite3.sqlite_version))
    rows = []
    try:
        for h, c, db in PAIRS:
            if index[h]['db_id'] != db or index[c]['db_id'] != db:
                raise ValueError('Wrong task database')
            rel, digest = DATABASES[db]
            path = project/'runtime/runs/datasets'/rel
            schema = schema_from_file(path, digest)
            _, variants = fixtures((h, c))
            values = [evaluate_full(schema, v, index[h]['SQL'], index[c]['SQL']) for v in variants]
            row = dict(history_id=h, current_id=c, database=db, schema=schema,
                       database_sha256=digest, variants=values,
                       history_sql=index[h]['SQL'], current_sql=index[c]['SQL'],
                       same_history=values[0]['history']==values[1]['history'],
                       different_current=values[0]['current']!=values[1]['current'])
            save(f'{h}-{c}.json', row)
            rows.append(row)
            if not row['same_history'] or not row['different_current']:
                raise ValueError('Witness did not hold; preserve failure')
            if sha(path) != digest:
                raise ValueError('Source database changed')
        if sha(source) != SOURCE_SHA:
            raise ValueError('Source queries changed')
        summary = dict(status='FULL_PINNED_SCHEMA_WITNESSES_CONFIRMED', pairs=len(rows),
                       schema_metadata_queries=2, real_task_queries=0,
                       synthetic_reference_queries=8, synthetic_databases=4,
                       model_calls=0, new_human_approvals=0, formal_tasks_admitted=0,
                       boundary='Satisfies declared schema constraints in fixed legacy snapshots; '
                       'does not establish unstored domain invariants, natural frequency, '
                       'human interpretation, or insufficiency with additional base-table reads.')
        save('summary.json', summary)
    except BaseException as exc:
        save('failure.json', dict(error_type=type(exc).__name__, automatic_retry=False))
        raise
    finally:
        save('artifact_hashes.json', {p.name:sha(p) for p in output.iterdir() if p.is_file()})
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1], args.output), indent=2))
