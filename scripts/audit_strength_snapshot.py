"""Bounded read-only checks for pending 766/786; no semantic admission or model calls."""
import argparse
import json
from pathlib import Path
import sqlite3
import time

from scripts.check_cache_information_loss import SOURCE_SHA, sha
from scripts.check_cache_full_schema import DATABASES, check_path

QUERIES = {
    'schema': "SELECT name,sql FROM sqlite_schema WHERE type='table' AND name IN ('superhero','attribute','hero_attribute') ORDER BY name",
    'dictionary': "SELECT id,attribute_name,attribute_name='Strength' AS native_upper,attribute_name='strength' AS native_lower FROM attribute WHERE lower(attribute_name)='strength' ORDER BY id",
    'value_types': "SELECT typeof(ha.attribute_value),COUNT(*) FROM hero_attribute ha JOIN attribute a ON a.id=ha.attribute_id WHERE a.attribute_name='Strength' GROUP BY typeof(ha.attribute_value)",
    'duplicate_pairs': "SELECT COUNT(*) AS duplicate_groups,COALESCE(SUM(n-1),0) AS extra_rows FROM (SELECT ha.hero_id,ha.attribute_id,COUNT(*) n FROM hero_attribute ha JOIN attribute a ON a.id=ha.attribute_id WHERE a.attribute_name='Strength' GROUP BY ha.hero_id,ha.attribute_id HAVING COUNT(*)>1)",
    'eligible_counts': """WITH matched AS (
SELECT s.id,s.full_name FROM superhero s JOIN hero_attribute ha ON ha.hero_id=s.id
JOIN attribute a ON a.id=ha.attribute_id
WHERE a.attribute_name='Strength' AND ha.attribute_value=(
SELECT MAX(h.attribute_value) FROM hero_attribute h JOIN attribute b ON b.id=h.attribute_id WHERE b.attribute_name='Strength')),
entities AS (SELECT DISTINCT id,full_name FROM matched)
SELECT (SELECT COUNT(*) FROM matched) AS joined_rows,COUNT(*) AS hero_entities,
(SELECT COUNT(*) FROM (SELECT DISTINCT full_name FROM entities)) AS distinct_names_including_null,
COALESCE(SUM(full_name IS NULL),0) AS null_names,
COALESCE(SUM(full_name=''),0) AS empty_names,
COALESCE(SUM(full_name='-'),0) AS dash_names FROM entities"""
}


def run(project, output):
    project, output = Path(project).resolve(), Path(output).absolute()
    check_path(output)
    if output.exists():
        raise ValueError('Refuse overwrite')
    source = project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001/arcwise_plat_full_with_diff.json'
    rel, digest = DATABASES['superhero']
    database = project/'runtime/runs/datasets'/rel
    for path in (source, database):
        check_path(path)
    if sha(source) != SOURCE_SHA or sha(database) != digest:
        raise ValueError('Wrong fixed inputs')
    sidecars = [Path(str(database)+s) for s in ('-wal','-shm','-journal')]
    if any(p.exists() for p in sidecars):
        raise ValueError('Unexpected database sidecar')
    records = json.loads(source.read_text('utf-8'))
    index = {int(r['question_id']): r for r in records}
    if len(records) != 498 or len(index) != 498:
        raise ValueError('Wrong query source size')
    selected = [index[i] for i in (766,786)]
    if any(r['db_id'] != 'superhero' for r in selected):
        raise ValueError('Wrong database identity')
    queries = dict(QUERIES)
    queries.update({f'reference_{i}':index[i]['SQL'] for i in (766,786)})
    output.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        with (output/name).open('x', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)

    save('design.json', dict(database_sha256=digest, source_sha256=SOURCE_SHA,
        query_count=len(queries), queries=queries, task_records=selected,
        source_sql_modified=False, model_calls=0, timeout_seconds=30,
        scope='Fixed legacy SQLite snapshot; not human meaning or general sufficiency'))
    for name in ('audit_strength_snapshot.py','check_cache_information_loss.py','check_cache_full_schema.py'):
        (output/name).write_bytes((project/'scripts'/name).read_bytes())
    con = None
    completed = 0
    try:
        con = sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
        con.execute('PRAGMA query_only=ON')
        deadline = time.monotonic()+30
        con.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        allowed = {sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE}
        con.set_authorizer(lambda action,*args:sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        for label, sql in queries.items():
            cursor = con.execute(sql)
            rows = cursor.fetchmany(1001)
            if len(rows)>1000:
                raise ValueError('Unexpected row count; refuse truncated success')
            save(label+'.json',dict(sql=sql,columns=[c[0] for c in cursor.description],rows=rows))
            completed += 1
        con.close()
        con = None
        if sha(source)!=SOURCE_SHA or sha(database)!=digest or any(p.exists() for p in sidecars):
            raise ValueError('Fixed inputs changed or sidecar appeared')
        summary = dict(status='READONLY_FIXED_SNAPSHOT_CHECK_COMPLETE', completed_queries=completed,
            reference_queries=2, diagnostic_queries=5, model_calls=0,
            inputs_unchanged=True, sqlite_version=sqlite3.sqlite_version,
            human_admission=False, general_sufficiency_proved=False)
        save('summary.json',summary)
        return summary
    except BaseException as exc:
        save('failure.json',dict(error_type=type(exc).__name__,completed_queries=completed,automatic_retry=False))
        raise
    finally:
        if con is not None:
            con.close()
        save('artifact_hashes.json',{p.name:sha(p) for p in sorted(output.iterdir()) if p.is_file()})


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],args.output),indent=2))
