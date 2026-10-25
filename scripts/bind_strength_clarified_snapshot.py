"""Bind the reviewed adapted pair to its fixed DB; read-only, no model calls."""
import argparse
import json
from pathlib import Path
import sqlite3
import time

from scripts.check_strength_clarified_semantics import DRAFT_SHA, HISTORY, CURRENT, canonical, digest, oracle
from scripts.check_cache_full_schema import DATABASES, check_path

TEST_SEAL = 'e0490a731e2020d2b44b27739199c9d345417039422f36622cadc1d7c7f91be3'
QUERIES = {
    'heroes': 'SELECT id,full_name FROM superhero ORDER BY id',
    'attributes': 'SELECT id,attribute_name FROM attribute ORDER BY id',
    'associations': 'SELECT hero_id,attribute_id,attribute_value FROM hero_attribute ORDER BY hero_id,attribute_id,attribute_value',
    'history': HISTORY,
    'current': CURRENT
}


def run(project, output):
    project,output = Path(project).resolve(),Path(output).absolute()
    draft=project/'reports/cache_relation_review_20261025/CLARIFIED_PAIR_REVIEW_V1.txt'
    tests=project/'runtime/runs/strength-clarified-semantics-20261025-001'
    rel,expected_db=DATABASES['superhero']
    database=project/'runtime/runs/datasets'/rel
    for p in (draft,database,output,tests):
        check_path(p)
    if output.exists():
        raise ValueError('Refuse overwrite')
    if digest(draft)!=DRAFT_SHA or digest(database)!=expected_db or digest(tests/'artifact_hashes.json')!=TEST_SEAL:
        raise ValueError('Fixed input mismatch')
    manifest=json.loads((tests/'artifact_hashes.json').read_text('utf-8'))
    if any(digest(tests/name)!=value for name,value in manifest.items()):
        raise ValueError('Prior semantics archive mismatch')
    if digest(project/'scripts/check_strength_clarified_semantics.py')!=manifest['check_source.py']:
        raise ValueError('Query/oracle source differs from tested version')
    sidecars=[Path(str(database)+s) for s in ('-wal','-shm','-journal')]
    if any(p.exists() for p in sidecars):
        raise ValueError('Unexpected sidecar')
    output.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        with (output/name).open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2)
    for name in ('bind_strength_clarified_snapshot.py','check_strength_clarified_semantics.py','check_cache_full_schema.py','check_cache_information_loss.py'):
        (output/name).write_bytes((project/'scripts'/name).read_bytes())
    (output/'review_draft.txt').write_bytes(draft.read_bytes())
    save('design.json',dict(draft_sha256=DRAFT_SHA,database_sha256=expected_db,
        finite_checks_seal=TEST_SEAL,queries=QUERIES,row_cap_per_query=10000,
        query_deadline_seconds=30,model_calls=0,sqlite_version=sqlite3.sqlite_version))
    con=None
    completed=0
    try:
        con=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
        con.execute('PRAGMA query_only=ON')
        deadline=time.monotonic()+30
        con.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE}
        con.set_authorizer(lambda action,*args:sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        results={}
        for name,sql in QUERIES.items():
            cursor=con.execute(sql)
            rows=cursor.fetchmany(10001)
            if len(rows)>10000:
                raise ValueError('Incomplete extraction: refuse truncation')
            results[name]=rows
            save(name+'.json',dict(sql=sql,columns=[c[0] for c in cursor.description],rows=rows))
            completed+=1
        expected=oracle(results['heroes'],results['attributes'],results['associations'])
        actual=dict(names=canonical([r[0] for r in results['history']]),count=results['current'][0][0])
        save('comparison.json',dict(expected=expected,actual=actual,match=expected==actual))
        if actual!=expected:
            raise ValueError('Adapted SQL differs from procedural semantics')
        con.close()
        con=None
        if digest(database)!=expected_db or digest(draft)!=DRAFT_SHA or any(p.exists() for p in sidecars):
            raise ValueError('Inputs changed or sidecar appeared')
        summary=dict(status='ADAPTED_PAIR_FIXED_SNAPSHOT_BOUND',queries=completed,
            history_rows=len(actual['names']),current_count=actual['count'],python_match=True,
            source_rows={name:len(results[name]) for name in ('heroes','attributes','associations')},
            model_calls=0,inputs_unchanged=True,paid_execution_authorized=False)
        save('summary.json',summary)
        return summary
    except BaseException as exc:
        save('failure.json',dict(error_type=type(exc).__name__,completed_queries=completed,automatic_retry=False))
        raise
    finally:
        if con is not None:
            con.close()
        save('artifact_hashes.json',{p.name:digest(p) for p in sorted(output.iterdir()) if p.is_file()})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],args.output),indent=2))
