"""Prespecified E3 cells, single-attempt transforms and oracle-blind fallback.

No generation here. Kept separate from later scoring so a wrong but executable
cache result cannot trigger hidden-answer-assisted recomputation.
"""
import copy
import random
import time

from runtime.reuse_scenarios import canonical,template_spec,connect_world,execute_sql
from runtime.reuse_readonly_execution import FUNCTIONS,execute_cache_sql,parse_transform,compare_answer

TRANSFORM_INSTRUCTION=(
    'You are executing a previously selected cache-reuse action. Independently check '
    'whether a defensible cache-only conversion is possible. Return exactly one JSON '
    'object with the field "sql": either one SQLite SELECT/WITH query string, or null '
    'if you cannot produce a defensible conversion. Never invent missing values. '
    'The only accessible relation is cache with the supplied columns and actual rows. '
    'The original database tables are NOT accessible in this step. They appear only '
    'to specify the current and historical tasks. Preserve requested output columns, '
    'NULL behavior, and row multiplicity; use real division where required. You may '
    'use SELECT, CTEs, subqueries, aggregation, joins of cache, and SQLite window '
    'functions in the explicitly supplied allowlist. No writes, ATTACH, PRAGMA, '
    'extension loading, external files, nondeterministic functions, or multiple SQL '
    'statements. A null refusal is allowed even though reuse was selected earlier. '
    'Do not include markdown, additional fields or commentary.')


def select_cases(cases):
    selected=[c for c in cases if c['instance']==0]
    if (len(cases)!=192 or len(selected)!=24 or len({c['base_id'] for c in selected})!=12
        or len({c['template'] for c in selected})!=12
        or any(c['split']!='confirmation_candidate' for c in cases)):
        raise ValueError('Exactly the frozen instance0 of all12 held templates required')
    return selected


def build_cells(cases,decisions):
    subset=select_cases(cases);ids={c['case_id'] for c in subset}
    rows=[];seen=set()
    for d in decisions:
        if d['case_id'] not in ids:continue
        key=(d['case_id'],d['method'],d['price'])
        if key in seen:raise ValueError('Duplicate logical decision')
        seen.add(key)
        if d['method'] not in ('M1','M2','M3','M4','M5','M6') or d['price'] not in (1,10,20):
            raise ValueError('Unplanned method or price')
        rows.append({'cell_id':f'{key[0]}-{key[1]}-p{key[2]}',
            'case_id':key[0],'method':key[1],'price':key[2],'action':d['action'],
            'decision_job_id':d.get('job_id'),
            'requires_generation':d['method'] in ('M1','M2','M3','M4') and d['action']=='reuse'})
    if len(rows)!=432:raise ValueError('All432 prespecified logical cells are required')
    rows.sort(key=lambda r:r['cell_id']);random.Random(2026102901).shuffle(rows)
    return rows


def transform_messages(case):
    # Explicitly select only public information; no prices, label, method,
    # counterworlds, private transforms or expected result enter the generator.
    data={'task':copy.deepcopy(case['public']),
          'execution':{'relation':'cache','functions':sorted(FUNCTIONS),
                       'sql_dialect':'SQLite','multiple_statements':False}}
    return [{'role':'system','content':TRANSFORM_INSTRUCTION},
            {'role':'user','content':canonical(data)}]


def recompute(case):
    start=time.perf_counter();con=None
    try:
        spec=template_spec(case['template'],case['instance'])
        con=connect_world(spec,case['audit']['world']);setup=time.perf_counter()-start
        query_start=time.perf_counter();answer=execute_sql(con,case['public']['current_sql'])
        return {'status':'EXECUTED',**answer,'database_setup_seconds':setup,
                'query_seconds':time.perf_counter()-query_start,
                'source':'public_current_SQL_on_fresh_readonly_fixture'}
    finally:
        if con is not None:con.close()


def execute_cell(case,cell,raw=None,planner_row=None):
    """No comparison with expected answer until score_execution is called."""
    start=time.perf_counter()
    record={'cell_id':cell['cell_id'],'action':cell['action'],'first_status':'NOT_ATTEMPTED',
            'first_execution':None,'final_result':None,'fallback_used':False,
            'fallback_reason':None,'transform_sql':None,'parse':None}
    if cell['action']=='recompute':
        record.update(first_status='RECOMPUTED',final_result=recompute(case))
    elif cell['action']=='reuse':
        if cell['method']=='M6':
            if planner_row is None or planner_row['case_id']!=case['case_id']:
                raise ValueError('Missing matching frozen classical rewrite')
            sql=planner_row['rewrite_sql'];parse={'valid':isinstance(sql,str) and bool(sql.strip()),
                                                'sql':sql,'abstained':False,'error':None}
        else:
            if raw is None:
                record['first_status']='MISSING_GENERATION_NO_ASSUMED_FALLBACK'
                record['execution_seconds']=time.perf_counter()-start
                return record
            parse=parse_transform(raw);sql=parse['sql']
        record['parse']=parse;record['transform_sql']=sql
        if not parse['valid']:record['first_status']='INVALID_OUTPUT'
        elif parse['abstained']:record['first_status']='EXPLICIT_REFUSAL'
        else:
            result=execute_cache_sql(case['public']['cache'],sql)
            record['first_execution']=result;record['first_status']=result['status']
            if result['status']=='EXECUTED':record['final_result']=result
        if record['final_result'] is None:
            record.update(fallback_used=True,fallback_reason=record['first_status'],final_result=recompute(case))
    else:record['first_status']='UNRESOLVED_ACTION_NO_FINAL_ANSWER'
    record['execution_seconds']=time.perf_counter()-start
    return record


def score_execution(case,record):
    """Post-execution only. Never overwrites a result or changes the fallback."""
    expected=case['audit']['current_result']
    result={'snapshot_final_correct':compare_answer(record['final_result'],expected)
            if record['final_result'] else False,
            'snapshot_cache_first_correct':compare_answer(record['first_execution'],expected)
            if record['first_execution'] else False,
            'negative_cache':not case['audit']['sufficient'],
            'finite_worlds':[],'semantic_guarantee_from_testing':False}
    if record['transform_sql'] is None or record['first_execution'] is None:return result
    # Run the SAME frozen SQL, with no new model call and no repair. Include
    # the negative collision world; its unchanged cache cannot determine bothQ.
    spec=template_spec(case['template'],case['instance'])
    for name,world in (('snapshot',case['audit']['world']),('counterworld',case['audit']['other_world'])):
        con=connect_world(spec,world)
        try:
            cache=execute_sql(con,case['public']['history_sql'])
            target=execute_sql(con,case['public']['current_sql'])
        finally:con.close()
        actual=execute_cache_sql(cache,record['transform_sql'])
        result['finite_worlds'].append({'name':name,'execution':actual,'answer_matches':compare_answer(actual,target)})
    return result


def measure_cache_construction(case):
    start=time.perf_counter();con=None
    try:
        con=connect_world(template_spec(case['template'],case['instance']),case['audit']['world'])
        setup=time.perf_counter()-start;query_start=time.perf_counter()
        cache=execute_sql(con,case['public']['history_sql']);query=time.perf_counter()-query_start
        if cache!={k:case['public']['cache'][k] for k in ('columns','rows')}:
            raise ValueError('Historical cache no longer reconstructs exactly')
        return {'database_setup_seconds':setup,'history_query_seconds':query,
                'total_seconds':setup+query,'amortized_seconds':{str(n):(setup+query)/n for n in (1,10,100)},
                'setup_and_query_included':True,'tiny_fixture_not_production_benchmark':True}
    finally:
        if con is not None:con.close()
