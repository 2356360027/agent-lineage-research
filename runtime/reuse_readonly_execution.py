"""Bounded cache-only SQL execution for the separately frozen E3 followup.

This is an application-level gate over an ephemeral SQLite database, not an OS
sandbox or a proof of semantic admissibility. It never opens a user database.
Accepted execution still requires separate result and counterworld checks.
"""
from dataclasses import dataclass
import json
import math
import sqlite3
import time


@dataclass(frozen=True)
class Limits:
    sql_bytes: int = 8192
    rows: int = 1000
    columns: int = 32
    cell_bytes: int = 8192
    sqlite_value_bytes: int = 65536
    output_bytes: int = 1048576
    vm_steps: int = 200000
    seconds: float = 1.0


FUNCTIONS = frozenset(('count','sum','total','avg','min','max','coalesce','ifnull',
    'nullif','abs','round','lower','upper','length','substr','substring','trim',
    'ltrim','rtrim','typeof','row_number','rank','dense_rank','first_value',
    'last_value','nth_value','lag','lead','percent_rank','cume_dist','ntile',
    'like','glob'))


def _validate(cache, sql, limits):
    if not isinstance(sql,str) or not sql.strip() or len(sql.encode())>limits.sql_bytes or '\x00' in sql:
        raise ValueError('SQL missing, too long or contains NUL')
    if not isinstance(cache,dict) or not isinstance(cache.get('columns'),list) or not isinstance(cache.get('rows'),list):
        raise ValueError('Explicit cache columns and rows required')
    columns=cache['columns']
    if not 1<=len(columns)<=limits.columns or len(cache['rows'])>limits.rows:
        raise ValueError('Cache shape exceeds limits')
    if any(not isinstance(c,str) or not c or len(c.encode())>128 or '\x00' in c for c in columns):
        raise ValueError('Invalid column name')
    if len({c.casefold() for c in columns})!=len(columns):
        raise ValueError('Duplicate SQLite column name')
    for row in cache['rows']:
        if not isinstance(row,(tuple,list)) or len(row)!=len(columns):
            raise ValueError('Cache row shape mismatch')
        for value in row:
            if value is None:continue
            if type(value) is int and -(2**63)<=value<2**63:continue
            if type(value) is float and math.isfinite(value):continue
            if isinstance(value,str) and len(value.encode())<=limits.cell_bytes and '\x00' not in value:continue
            raise ValueError('Unsupported or oversized cache scalar')


def execute_cache_sql(cache, sql, limits=Limits()):
    """One attempt; no correction, retry, implicit truncation or fallback."""
    start=time.perf_counter()
    result={'status':'REJECTED','columns':None,'rows':None,'error':None,
            'denied_operations':[],'read_relations':[],'functions':[],
            'vm_steps_upper_approximation':0,'readonly_ephemeral_cache':True,
            'semantic_admissibility_proven':False}
    con=None;steps=0;expired=False;reads=set();functions=set();denied=[]
    try:
        _validate(cache,sql,limits)
        con=sqlite3.connect(':memory:',cached_statements=0)
        con.enable_load_extension(False)
        con.execute('PRAGMA temp_store=MEMORY')
        con.execute('PRAGMA trusted_schema=OFF')
        quoted=','.join('"'+c.replace('"','""')+'"' for c in cache['columns'])
        con.execute('CREATE TABLE cache('+quoted+')')
        con.executemany('INSERT INTO cache VALUES('+','.join('?' for _ in cache['columns'])+')',cache['rows'])
        con.commit()
        con.execute('PRAGMA query_only=ON')
        for category,value in (
            (sqlite3.SQLITE_LIMIT_SQL_LENGTH,limits.sql_bytes),
            (sqlite3.SQLITE_LIMIT_LENGTH,limits.sqlite_value_bytes),
            (sqlite3.SQLITE_LIMIT_COLUMN,limits.columns),
            (sqlite3.SQLITE_LIMIT_EXPR_DEPTH,100),
            (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT,16),
            (sqlite3.SQLITE_LIMIT_ATTACHED,0),
            (sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,0),
            (sqlite3.SQLITE_LIMIT_VDBE_OP,50000),
            (sqlite3.SQLITE_LIMIT_FUNCTION_ARG,16)):
            con.setlimit(category,value)
        allowed_columns={c.casefold() for c in cache['columns']}

        def authorize(code,first,second,database,source):
            if code in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_RECURSIVE):return sqlite3.SQLITE_OK
            # SQLite's COUNT(*) fast path can report database=None and column=''.
            # Only that columnless cache read is accepted without a DB name;
            # there are no attached or temporary relations in this connection.
            cache_scope=database=='main' or (database is None and second=='')
            if code==sqlite3.SQLITE_READ and cache_scope and first=='cache' and (second=='' or (second or '').casefold() in allowed_columns):
                reads.add(first);return sqlite3.SQLITE_OK
            if code==sqlite3.SQLITE_FUNCTION and (second or '').lower() in FUNCTIONS:
                functions.add(second.lower());return sqlite3.SQLITE_OK
            if len(denied)<8:denied.append({'code':code,'object':first,'detail':second,'database':database})
            return sqlite3.SQLITE_DENY

        def progress():
            nonlocal steps,expired
            steps+=100
            expired=steps>=limits.vm_steps or time.perf_counter()-start>=limits.seconds
            return int(expired)

        con.set_authorizer(authorize)
        con.set_progress_handler(progress,100)
        cursor=con.execute(sql)
        if cursor.description is None:raise ValueError('Statement did not produce a result')
        rows=cursor.fetchmany(limits.rows+1)
        if len(rows)>limits.rows:raise OverflowError('Output row bound exceeded; no truncated answer')
        columns=[c[0] for c in cursor.description]
        payload={'columns':columns,'rows':[list(r) for r in rows]}
        encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        if len(encoded)>limits.output_bytes:raise OverflowError('Output byte bound exceeded')
        if time.perf_counter()-start>limits.seconds:raise TimeoutError('Execution time bound exceeded')
        result.update(status='EXECUTED',**payload)
    except (ValueError,sqlite3.Error,OverflowError,TimeoutError,MemoryError) as exc:
        limited=expired or isinstance(exc,(OverflowError,TimeoutError,MemoryError))
        result.update(status='RESOURCE_LIMIT' if limited else 'REJECTED' if denied or isinstance(exc,ValueError) else 'SQL_ERROR',
                      error=type(exc).__name__+': '+str(exc))
    finally:
        if con is not None:con.close()
        result.update(seconds=time.perf_counter()-start,denied_operations=denied,
            read_relations=sorted(reads),functions=sorted(functions),
            vm_steps_upper_approximation=steps+99)
    return result


def parse_transform(raw):
    """Strict transport: SQL string in a single JSON object, no fence stripping."""
    def unique_pairs(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON field')
            result[key]=value
        return result
    try:
        choice=raw['choices'][0]
        if choice['finish_reason']!='stop':raise ValueError('Unfinished response')
        obj=json.loads(choice['message']['content'],object_pairs_hook=unique_pairs)
        if not isinstance(obj,dict) or set(obj)!= {'sql'}:
            raise ValueError('Expected exactly the sql field')
        if obj['sql'] is None:return {'valid':True,'sql':None,'abstained':True,'error':None}
        if not isinstance(obj['sql'],str) or not obj['sql'].strip():
            raise ValueError('Expected a nonempty sql string or explicit null refusal')
        return {'valid':True,'sql':obj['sql'],'abstained':False,'error':None}
    except (KeyError,IndexError,TypeError,ValueError) as exc:
        return {'valid':False,'sql':None,'abstained':False,'error':type(exc).__name__+': '+str(exc)}


def compare_answer(actual, expected):
    """Exact columns and unordered row multiplicity, fixed 1e-12 numeric tolerance.

    Bipartite matching avoids a greedy near-equal-number assignment artifact.
    Comparisons are separate from gate acceptance and never change execution.
    """
    if actual.get('status')!='EXECUTED':return False
    if actual['columns']!=expected['columns'] or len(actual['rows'])!=len(expected['rows']):return False

    def equal(left,right):
        if len(left)!=len(right):return False
        for a,b in zip(left,right):
            if a is None or b is None:
                if a is not b:return False
            elif type(a) in (int,float) and type(b) in (int,float):
                if not math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12):return False
            elif type(a)!=type(b) or a!=b:return False
        return True

    neighbors=[[j for j,b in enumerate(expected['rows']) if equal(a,b)] for a in actual['rows']]
    matches={}
    for root in range(len(neighbors)):
        queue=[root];seen_left={root};parent={};free=None
        for left in queue:
            for right in neighbors[left]:
                if right in parent:continue
                parent[right]=left
                if right not in matches:
                    free=right;break
                if matches[right] not in seen_left:
                    seen_left.add(matches[right]);queue.append(matches[right])
            if free is not None:break
        if free is None:return False
        # Follow the alternating augmenting path without Python recursion.
        reverse={left:right for right,left in matches.items()}
        while free is not None:
            left=parent[free];previous=reverse.get(left)
            matches[free]=left;free=previous
    return True
