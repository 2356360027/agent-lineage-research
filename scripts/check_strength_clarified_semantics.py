"""Finite SQL-versus-Python checks for a proposed task; no real DB/model access."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

SCHEMA_SHA = '352bb81ba26df7b6ed461a01def44db37ae670af23d18c788cd6f7b13411844a'
DRAFT_SHA = '3172efd672168824195ac05d62e07a97c2f0b2cfafaf8f5b7953f63372f015d5'
ELIGIBLE = """FROM superhero s WHERE EXISTS (
SELECT 1 FROM hero_attribute ha JOIN attribute a ON a.id=ha.attribute_id
WHERE ha.hero_id=s.id AND a.attribute_name COLLATE BINARY='Strength'
AND ha.attribute_value=(SELECT MAX(h.attribute_value) FROM hero_attribute h
JOIN attribute b ON b.id=h.attribute_id WHERE b.attribute_name COLLATE BINARY='Strength'))"""
HISTORY = 'SELECT DISTINCT s.full_name COLLATE BINARY '+ELIGIBLE
CURRENT = 'SELECT COUNT(*) '+ELIGIBLE


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(values):
    return sorted(values, key=lambda v: (v is not None, '' if v is None else v))


def oracle(heroes, attributes, associations):
    strengths = {i for i, name in attributes if name == 'Strength'}
    values = [v for _, a, v in associations if a in strengths and v is not None]
    if any(type(v) is not int for v in values):
        raise ValueError('Fixtures require integer values; no coercion assumed')
    maximum = max(values) if values else None
    eligible = {h for h,a,v in associations if maximum is not None and a in strengths and v == maximum}
    names = {name for i,name in heroes if i in eligible}
    count = sum(i in eligible for i,_ in heroes)
    return dict(names=canonical(names),count=count)


def cases():
    strength = [(1,'Strength')]
    return [
        ('empty',[],strength,[]),
        ('all_null',[(1,'A')],strength,[(1,1,None)]),
        ('same_name_ties',[(1,'A'),(2,'A')],strength,[(1,1,100),(2,1,100)]),
        ('name_values',list(enumerate([None,None,'','','-','-','A','a',' A','A '],1)),strength,[(i,1,100) for i in range(1,11)]),
        ('duplicate_links',[(1,'A'),(2,'B')],strength,[(1,1,100),(1,1,100),(2,1,99)]),
        ('wrong_case',[(1,'A'),(2,'B')],[(1,'Strength'),(2,'strength')],[(1,1,5),(2,2,999)]),
        ('two_attribute_ids',[(1,'A'),(2,'B')],[(1,'Strength'),(2,'Strength')],[(1,1,50),(2,2,100)]),
        ('null_hero_above_max',[(1,'A')],strength,[(1,1,5),(None,1,100)]),
        ('negative_numeric_order',[(1,'A'),(2,'B')],strength,[(1,1,-2),(2,1,-10)]),
        ('mixed_nulls',[(1,None),(2,'B'),(3,'C')],strength,[(1,1,7),(2,1,None),(3,None,999)])
    ]


def run(project, output):
    project,output = Path(project),Path(output)
    archived = project/'runtime/runs/cache-full-schema-witnesses-20261025-001/766-786.json'
    draft = project/'reports/cache_relation_review_20261025/CLARIFIED_PAIR_REVIEW_V1.txt'
    if digest(archived)!=SCHEMA_SHA or digest(draft)!=DRAFT_SHA:
        raise ValueError('Wrong frozen schema or draft')
    if output.exists():
        raise ValueError('Refuse overwrite')
    schema = json.loads(archived.read_text('utf-8'))['schema']
    output.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        with (output/name).open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2)
    (output/'check_source.py').write_bytes(Path(__file__).read_bytes())
    (output/'review_draft.txt').write_bytes(draft.read_bytes())
    save('schema.json',schema)
    save('design.json',dict(draft_sha256=DRAFT_SHA,archived_schema_source_sha256=SCHEMA_SHA,
        history_sql=HISTORY,current_sql=CURRENT,cases=len(cases()),sqlite_version=sqlite3.sqlite_version,
        scope='Finite program checks, not human admission, model samples or universal SQL proof'))
    passed = 0
    try:
        for name,heroes,attributes,associations in cases():
            con = sqlite3.connect(':memory:')
            try:
                con.execute('PRAGMA foreign_keys=ON')
                for kind in ('table','index','view','trigger'):
                    for typ,_,_,ddl in schema:
                        if typ==kind and ddl is not None:
                            con.execute(ddl)
                con.executemany('INSERT INTO superhero(id,full_name) VALUES (?,?)',heroes)
                con.executemany('INSERT INTO attribute(id,attribute_name) VALUES (?,?)',attributes)
                con.executemany('INSERT INTO hero_attribute(hero_id,attribute_id,attribute_value) VALUES (?,?,?)',associations)
                fk = con.execute('PRAGMA foreign_key_check').fetchall()
                integrity = con.execute('PRAGMA integrity_check').fetchall()
                if fk or integrity != [('ok',)]:
                    raise ValueError('Invalid full-schema fixture')
                con.execute('PRAGMA query_only=ON')
                actual = dict(names=canonical([r[0] for r in con.execute(HISTORY)]),count=con.execute(CURRENT).fetchone()[0])
                expected = oracle(heroes,attributes,associations)
                save(name+'.json',dict(heroes=heroes,attributes=attributes,associations=associations,
                    actual=actual,expected=expected,foreign_key_check=fk,integrity=integrity,match=actual==expected))
                if actual!=expected:
                    raise ValueError('SQL differs from independent procedural semantics')
                if name=='null_hero_above_max' and actual!=dict(names=[],count=0):
                    raise ValueError('Maximum population regression')
                passed += 1
            finally:
                con.close()
        summary = dict(status='FINITE_CLARIFIED_SEMANTICS_MATCH',cases_passed=passed,
            in_memory_task_queries=2*passed,real_database_queries=0,model_calls=0,
            null_hero_fk_valid_and_excludes_lower_hero=True,task_admitted=False)
        save('summary.json',summary)
        return summary
    except BaseException as exc:
        save('failure.json',dict(error_type=type(exc).__name__,cases_passed=passed,automatic_retry=False))
        raise
    finally:
        save('artifact_hashes.json',{p.name:digest(p) for p in sorted(output.iterdir()) if p.is_file()})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],args.output),indent=2))
