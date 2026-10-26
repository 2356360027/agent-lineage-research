"""Run pinned upstream Calcite on public six-family SQL/cache inputs.

Preflight uses one positive instance per DEVELOPMENT template (12 queries).
Full development uses 48 contexts. No confirmation cases or model calls here.
Each invocation has new immutable artifacts; compilation/errors are preserved.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import random
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from runtime.reuse_scenarios import connect_world,execute_sql,execute_transform,same_rows,template_spec

JAVA_SOURCE='runtime/calcite_repro/src/main/java/research/ReuseSqlPlanner.java'
SQLGLOT_SHA='90aa461490fcd95d14ec3842a97506ae20f6d3e9313307ad31be793d479cca65'


def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def save(path,value):
    with Path(path).open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)


def load_sqlglot(wheel):
    wheel=Path(wheel).resolve()
    if sha(wheel)!=SQLGLOT_SHA: raise ValueError('SQLGlot wheel hash mismatch')
    if str(wheel) not in sys.path: sys.path.insert(0,str(wheel))
    import sqlglot
    if sqlglot.__version__!='30.22.0' or str(wheel) not in sqlglot.__file__:
        raise ValueError('Unpinned SQLGlot module')
    return sqlglot


def quote_query(sql,sg):
    parsed=sg.parse(sql,read='sqlite',error_level=sg.ErrorLevel.RAISE)
    if len(parsed)!=1 or not isinstance(parsed[0],sg.exp.Query):
        raise ValueError('Only a single SELECT query is allowed')
    # Generic renderer, identifier quoting only; no optimizer or oracle rewrite.
    return parsed[0].sql(identify=True,unsupported_level=sg.ErrorLevel.RAISE)


def projection_rewrite(public,sg):
    """Classical lossless-for-needed-columns bag projection, not an oracle rule.

    A complete unfiltered non-DISTINCT projection retains the multiplicities of
    all projected tuples. Q can substitute it if every referenced base column
    is retained. Reject unsupported constructs/ambiguous stars conservatively.
    This complements two upstream Calcite paths, which can miss COUNT(*) over
    a simple key projection. No template ID or private ground truth is read.
    """
    h=sg.parse_one(public['history_sql'],read='sqlite')
    q=sg.parse_one(public['current_sql'],read='sqlite')
    if not isinstance(h,sg.exp.Select) or h.args.get('distinct'): return None
    if any(h.args.get(k) for k in ('where','group','having','limit','offset','joins','with_','qualify')):
        return None
    source=h.args.get('from_')
    if source is None or not isinstance(source.this,sg.exp.Table): return None
    if source.this.db or source.this.catalog: return None
    relation=source.this.name
    retained={}
    for expression in h.expressions:
        col=expression.this if isinstance(expression,sg.exp.Alias) else expression
        if not isinstance(col,sg.exp.Column) or col.is_star: return None
        if col.table and col.table!=source.this.alias_or_name: return None
        retained[col.name]=expression.alias_or_name
    if len(retained)!=len(h.expressions) or not public['cache']['complete']: return None
    if set(retained.values())!=set(public['cache']['columns']): return None
    if q.find(sg.exp.CTE): return None
    tables=list(q.find_all(sg.exp.Table))
    if not tables or any(t.name!=relation or t.db or t.catalog for t in tables): return None
    for star in q.find_all(sg.exp.Star):
        if not isinstance(star.parent,sg.exp.Count): return None
    for column in q.find_all(sg.exp.Column):
        if column.name not in retained: return None
        column.set('this',sg.exp.to_identifier(retained[column.name]))
    for table in tables:
        old_alias=table.alias_or_name
        table.set('this',sg.exp.to_identifier('cache'))
        if not table.args.get('alias'):
            table.set('alias',sg.exp.TableAlias(this=sg.exp.to_identifier(old_alias)))
    return q.sql(dialect='sqlite',identify=True,unsupported_level=sg.ErrorLevel.RAISE)


def bag_equal(left,right):
    if len(left)!=len(right): return False
    unmatched=list(right)
    for row in left:
        for i,other in enumerate(unmatched):
            if same_rows([row],[other]):
                unmatched.pop(i); break
        else: return False
    return not unmatched


def stress_worlds(spec):
    yield {table:[] for table in spec['world']}
    rng=random.Random('calcite-adapter-check-v1-'+spec['template'])
    for _ in range(16):
        if 'items' in spec['world']:
            yield {'items':[[i,rng.choice(['A','B']),rng.choice(['A','Gold','Blue']),
                rng.choice([2025,2026,2027,2028]),rng.choice([None,-2,0,1,2,5]),
                rng.choice([0,1])] for i in range(rng.randrange(8))]}
        elif 'pairs' in spec['world']:
            yield {'pairs':[[i//2,i%2,rng.choice(['A','B'])] for i in range(rng.randrange(8))]}
        else:
            members=[[i,rng.choice([-2,0,1,5])] for i in range(rng.randrange(6))]
            events=[[i,rng.choice(['X','Y','Z'])] for i in range(rng.randrange(5))]
            yield {'member':members,'event':events,
                   'attendance':[[e[0],m[0]] for e in events for m in members if rng.random()<.3]}


def check_generated_rewrite(case,sql):
    """Post-selection audit only: the planner cannot see these worlds/results."""
    spec=template_spec(case['template'],case['instance'])
    checks=[]
    worlds=[case['audit']['world'],case['audit']['other_world'],*stress_worlds(spec)]
    for world in worlds:
        con=connect_world(spec,world)
        try:
            cache=execute_sql(con,case['public']['history_sql'])
            expected=execute_sql(con,case['public']['current_sql'])
            actual=execute_transform(cache,sql)
            checks.append({'value_match':bag_equal(actual['rows'],expected['rows']),
                           'column_match':actual['columns']==expected['columns']})
        finally: con.close()
    return {'worlds':len(checks),'value_mismatches':sum(not c['value_match'] for c in checks),
            'column_mismatches':sum(not c['column_match'] for c in checks)}


def invoke(command,output,timeout):
    started=time.perf_counter()
    try:
        proc=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        record={'status':'EXITED','returncode':proc.returncode,'stdout':proc.stdout.decode('utf-8',errors='replace'),
                'stderr':proc.stderr.decode('utf-8',errors='replace')}
    except subprocess.TimeoutExpired as exc:
        record={'status':'TIMEOUT','returncode':None,
                'stdout':(exc.stdout or b'').decode('utf-8',errors='replace'),
                'stderr':(exc.stderr or b'').decode('utf-8',errors='replace')}
    record.update(wall_seconds=time.perf_counter()-started,timeout_seconds=timeout,command=command)
    save(output,record)
    return record


def run(candidate,output,phase,dependency_design,wheel):
    candidate,output,dependency_design,wheel=map(lambda p:Path(p).resolve(),
        (candidate,output,dependency_design,wheel))
    if not output.is_relative_to(ROOT/'runtime/runs'): raise ValueError('Output must be a new local run directory')
    output.mkdir(parents=True,exist_ok=False)
    for relative in (JAVA_SOURCE,'scripts/run_reuse_calcite.py','runtime/reuse_scenarios.py'):
        dest=output/'source'/relative
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/relative,dest)
    index=json.loads((candidate/'sha256.json').read_bytes())
    for name,expected in index.items():
        path=(candidate/name).resolve()
        if not path.is_relative_to(candidate) or sha(path)!=expected:
            raise ValueError('Candidate archive changed')
    private=json.loads((candidate/'development.private.json').read_bytes())
    public={r['case_id']:r for r in json.loads((candidate/'development.calcite-inputs.json').read_bytes())}
    if phase=='preflight':
        cases=[c for c in private if c['instance']==0 and c['audit']['sufficient']]
    elif phase=='development': cases=private
    else: raise ValueError('Development only')
    if len(cases)!=(12 if phase=='preflight' else 48): raise ValueError('Fixed scope mismatch')
    sg=load_sqlglot(wheel)
    inputs=[]
    for case in cases:
        data=dict(public[case['case_id']])
        for key in ('current_sql','history_sql'):
            data[key+'_quoted']=quote_query(data[key],sg)
        # Check dialect adapter on private worlds; these rows/results are NEVER
        # sent to the planner. This tests transport semantics, not plan selection.
        spec=template_spec(case['template'],case['instance'])
        for world in (case['audit']['world'],case['audit']['other_world']):
            con=connect_world(spec,world)
            try:
                for key in ('current_sql','history_sql'):
                    original=execute_sql(con,data[key]); quoted=execute_sql(con,data[key+'_quoted'])
                    if original['columns']!=quoted['columns'] or not bag_equal(original['rows'],quoted['rows']):
                        raise ValueError('Quoted-query adapter changes result')
            finally: con.close()
        inputs.append(data)
    deps=json.loads(dependency_design.read_bytes())
    for record in deps['dependencies']:
        if sha(record['path'])!=record['sha256']: raise ValueError('Pinned Calcite dependency mismatch')
    for path,expected in deps['java_binaries'].items():
        if sha(path)!=expected: raise ValueError('Pinned JDK binary changed')
    java=next(p for p in deps['java_binaries'] if Path(p).name=='java.exe')
    javac=next(p for p in deps['java_binaries'] if Path(p).name=='javac.exe')
    cp=Path(deps['classpath_file']).read_text(encoding='utf-8').strip()
    if sha(deps['classpath_file'])!=deps['classpath_sha256']: raise ValueError('Dependency classpath mismatch')
    save(output/'private_cases.json',cases)
    save(output/'public_inputs.json',inputs)
    save(output/'plan.json',{'phase':phase,'utc':datetime.now(timezone.utc).isoformat(),'case_ids':[c['case_id'] for c in cases],
        'candidate_index_sha256':sha(candidate/'sha256.json'),'dependencies':deps['dependencies'],
        'java_binaries':deps['java_binaries'],'sqlglot_sha256':SQLGLOT_SHA,'model_calls':0,
        'per_process_timeout_seconds':60,'total_process_limit_seconds':1800,'retry':False,
        'oracle_or_base_rows_sent_to_planner':False,'current_query_given_to_all_model_methods':True,
        'limits':'Configured upstream HEP MV + SubstitutionVisitor coverage, not completeness. Java 25 JBR adaptation; not upstream JDK25 certification. '
                 'AVG type override DOUBLE disclosed; SQLGlot only quotes identifiers. Missing rewrites are unknown.'})
    save(output/'input_source_hashes.json',{str(p.relative_to(output)).replace('\\','/'):sha(p)
         for p in output.rglob('*') if p.is_file()})
    classes=output/'classes'; classes.mkdir()
    build=invoke([javac,'-J-Duser.language=en','-J-Duser.country=US','-J-Dfile.encoding=UTF-8',
                  '--release','17','-encoding','UTF-8','-cp',cp,'-d',str(classes),
                  str(output/'source'/JAVA_SOURCE)],output/'build.json',60)
    if build['returncode']!=0:
        save(output/'terminal.json',{'status':'COMPILATION_FAILED','model_calls':0,'planner_cases_started':0})
        save(output/'sha256.json',{str(p.relative_to(output)).replace('\\','/'):sha(p)
             for p in output.rglob('*') if p.is_file()})
        print(json.dumps({k:build[k] for k in ('returncode','stderr')},ensure_ascii=False,indent=2))
        raise SystemExit(1)
    save(output/'compiled_classes.json',{str(p.relative_to(classes)):sha(p) for p in classes.rglob('*.class')})
    records=[]; started=time.perf_counter()
    for i,(case,data) in enumerate(zip(cases,inputs)):
        if time.perf_counter()-started>=1800: break
        inp=output/f'{i:02d}.input.json'; dest=output/f'{i:02d}.planner.json'
        save(inp,data)
        process=invoke([java,'-Duser.language=en','-Duser.country=US','-Dfile.encoding=UTF-8',
                        '-Xmx768m','-cp',str(classes)+os.pathsep+cp,'research.ReuseSqlPlanner',str(inp),str(dest)],
                       output/f'{i:02d}.process.json',min(60,1800-(time.perf_counter()-started)))
        record={'case_id':case['case_id'],'template':case['template'],'family':case['family'],
            'sufficient':case['audit']['sufficient'],'process_status':process['status'],
            'wall_seconds':process['wall_seconds'],'planner_status':'MISSING_OUTPUT',
            'admissibility':'unknown','rewrite_execution':'NOT_ATTEMPTED',
            'baseline_engine':'NONE','rewrite_sql':None}
        if dest.exists():
            planned=json.loads(dest.read_bytes())
            if planned['case_id']!=case['case_id']: raise ValueError('Planner identity mismatch')
            record.update(planner_status=planned['status'],admissibility=planned['admissibility'])
            selected_sql=planned.get('rewrite_sql')
            if selected_sql:
                record['baseline_engine']='CALCITE_'+planned['selected_engine']
            elif planned['status']=='PLANNED':
                selected_sql=projection_rewrite(data,sg)
                if selected_sql:
                    record.update(admissibility='applicable',baseline_engine='CLASSICAL_BAG_PROJECTION')
            if selected_sql:
                record['rewrite_sql']=selected_sql
                try:
                    transform_started=time.perf_counter()
                    result=execute_transform(case['public']['cache'],selected_sql)
                    transform_seconds=time.perf_counter()-transform_started
                    equal=bag_equal(result['rows'],case['audit']['current_result']['rows'])
                    record.update(rewrite_execution='EXECUTED',answer_matches_fixed_snapshot=equal,
                                  rewrite_result=result,transform_seconds=transform_seconds,
                                  rewrite_stress=check_generated_rewrite(case,selected_sql))
                except Exception as exc:
                    record.update(rewrite_execution='ERROR',rewrite_error=str(exc))
        record['false_admissibility']=record['admissibility']=='applicable' and not record['sufficient']
        records.append(record)
        save(output/f'{i:02d}.audit.json',record)
        print(f'Completed planner {i+1}/{len(cases)}',flush=True)
    summary={'status':'COMPLETED_DEVELOPMENT_PLANNER' if len(records)==len(cases) else 'INCOMPLETE',
        'phase':phase,'planned':len(cases),'completed':len(records),'model_calls':0,
        'planner_statuses':dict(Counter(r['planner_status'] for r in records)),
        'baseline_engines':dict(Counter(r['baseline_engine'] for r in records)),
        'positive_contexts':sum(r['sufficient'] for r in records),
        'negative_contexts':sum(not r['sufficient'] for r in records),
        'cache_only_rewrites':sum(r['admissibility']=='applicable' for r in records),
        'false_admissibility':sum(r['false_admissibility'] for r in records),
        'rewrites_matching_snapshot':sum(r.get('answer_matches_fixed_snapshot') is True for r in records),
        'rewrite_execution_errors':sum(r['rewrite_execution']=='ERROR' for r in records),
        'stress_worlds_checked':sum(r.get('rewrite_stress',{}).get('worlds',0) for r in records),
        'stress_value_mismatches':sum(r.get('rewrite_stress',{}).get('value_mismatches',0) for r in records),
        'stress_column_mismatches':sum(r.get('rewrite_stress',{}).get('column_mismatches',0) for r in records),
        'rows':records,'warning':'Development only. Successful fixed-snapshot execution alone is not general equivalence. '
          'No-rewrite/parse/timeout states remain unknown and require recomputation. No model-method comparison yet.'}
    save(output/'summary.json',summary)
    save(output/'sha256.json',{str(p.relative_to(output)).replace('\\','/'):sha(p)
         for p in output.rglob('*') if p.is_file()})
    print(json.dumps({k:v for k,v in summary.items() if k!='rows'},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--phase',choices=['preflight','development'],required=True)
    p.add_argument('--dependency-design',required=True,type=Path);p.add_argument('--wheel',required=True,type=Path)
    a=p.parse_args();run(a.candidate,a.output,a.phase,a.dependency_design,a.wheel)
