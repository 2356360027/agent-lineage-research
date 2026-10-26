"""Six-family formal reuse suite with executed caches and collision witnesses.

This module constructs data, not model responses. Development and held-out
templates differ in query structure, not just names/seeds. Instances within a
template are correlated fixtures, not new independent semantic mechanisms.
"""
from collections import Counter, defaultdict
from fractions import Fraction
import copy
import hashlib
import json
import math
import sqlite3

VERSION = 'reuse-six-family-candidate-v1'
ITEM_COLUMNS = ['id','name','grp','period','score','active']
ITEM_DDL = ("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT NOT NULL, grp TEXT NOT NULL, "
            "period INTEGER NOT NULL, score INTEGER CHECK(score IS NULL OR score BETWEEN -10000 AND 10000), "
            "active INTEGER NOT NULL CHECK(active IN (0,1))) STRICT")
PAIR_DDL = ('CREATE TABLE pairs(k1 INTEGER NOT NULL,k2 INTEGER NOT NULL,name TEXT NOT NULL,'
            'PRIMARY KEY(k1,k2)) STRICT')
JOIN_SCHEMA = [
    ('member', ['id','weight'], 'CREATE TABLE member(id INTEGER PRIMARY KEY,weight INTEGER NOT NULL) STRICT'),
    ('event', ['id','title'], 'CREATE TABLE event(id INTEGER PRIMARY KEY,title TEXT NOT NULL) STRICT'),
    ('attendance', ['event_id','member_id'],
     'CREATE TABLE attendance(event_id INTEGER NOT NULL REFERENCES event(id),'
     'member_id INTEGER NOT NULL REFERENCES member(id),PRIMARY KEY(event_id,member_id)) STRICT')]

DEVELOPMENT = {
    'entity_projection': ['entity_global','entity_grouped'],
    'null_aggregation': ['avg_global','avg_grouped'],
    'scope': ['scope_disjoint','scope_boolean'],
    'group_coverage': ['group_target','group_threshold'],
    'join_multiplicity': ['join_distinct_count','join_unique_sum'],
    'ranking_ties': ['rank_max_ids','rank_max_count'],
}
CONFIRMATION = {
    'entity_projection': ['entity_postfilter','entity_composite'],
    'null_aggregation': ['avg_weighted_rollup','avg_distinct'],
    'scope': ['scope_boundary','scope_joint_group'],
    'group_coverage': ['group_max_ties','group_having_loss'],
    'join_multiplicity': ['join_antijoin','join_intersection'],
    'ranking_ties': ['rank_topk_ties','rank_partitioned'],
}


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',',':'), allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def item(i, name='A', grp='A', period=2026, score=1, active=1):
    return [i,name,grp,period,score,active]


def template_spec(template, instance):
    """Return symbolic SQL plus TWO legal worlds; oracle implemented separately."""
    schema = [('items', ITEM_COLUMNS, ITEM_DDL)]
    world = {'items': [item(1), item(2,name='A'), item(3,name='B',grp='B')]}
    other = copy.deepcopy(world)
    q = h0 = h1 = transform = None
    target = 'Formal output of current SQL; unordered rows, with all requested columns.'
    proof = ''
    if template.startswith('entity_'):
        if template == 'entity_composite':
            schema = [('pairs',['k1','k2','name'],PAIR_DDL)]
            world = {'pairs': [[1,10,'A'],[1,20,'A'],[2,10,'B']]}
            other = {'pairs': [[1,10,'A'],[2,10,'B']]}
            q = 'SELECT COUNT(*) AS answer FROM pairs'
            h0 = 'SELECT DISTINCT k1,name FROM pairs ORDER BY k1,name'
            h1 = 'SELECT k1,k2 FROM pairs ORDER BY k1,k2'
            transform = 'SELECT COUNT(*) AS answer FROM cache'
            proof = 'Composite primary-key pairs retain one row per original entity; projection onto k1/name does not.'
        else:
            other['items'].pop(1)
            if template == 'entity_global':
                q = 'SELECT COUNT(*) AS answer FROM items'
                h0 = 'SELECT DISTINCT name FROM items ORDER BY name'
                h1 = 'SELECT id FROM items ORDER BY id'
                transform = 'SELECT COUNT(*) AS answer FROM cache'
            elif template == 'entity_grouped':
                q = 'SELECT grp,COUNT(*) AS answer FROM items GROUP BY grp ORDER BY grp'
                h0 = 'SELECT DISTINCT grp,name FROM items ORDER BY grp,name'
                h1 = 'SELECT id,grp FROM items ORDER BY id'
                transform = 'SELECT grp,COUNT(*) AS answer FROM cache GROUP BY grp ORDER BY grp'
            elif template == 'entity_postfilter':
                world['items'][2][5] = other['items'][1][5] = 0
                q = 'SELECT COUNT(*) AS answer FROM items WHERE active=1'
                h0 = 'SELECT DISTINCT name,active FROM items ORDER BY name,active'
                h1 = 'SELECT id,active FROM items ORDER BY id'
                transform = 'SELECT COUNT(*) AS answer FROM cache WHERE active=1'
            proof = 'A complete primary-key projection retains entity multiplicity, including the required grouping/filter attribute.'
    elif template.startswith('avg_'):
        world = {'items': [item(1,score=1),item(2,score=5),item(3,score=None,grp='B')]}
        other = copy.deepcopy(world)
        if template == 'avg_global':
            other['items'][1][4] = 9
            q = 'SELECT AVG(score) AS answer FROM items'
            h0 = 'SELECT COUNT(*) AS n_rows FROM items'
            h1 = 'SELECT SUM(score) AS total,COUNT(score) AS n_nonnull FROM items'
            transform = 'SELECT CASE WHEN n_nonnull=0 THEN NULL ELSE total*1.0/n_nonnull END AS answer FROM cache'
        elif template == 'avg_grouped':
            world = {'items': [item(1,score=6),item(2,score=None,grp='B')]}
            other = {'items': [item(1,score=2),item(2,score=4),item(3,score=None,grp='B')]}
            q = 'SELECT grp,AVG(score) AS answer FROM items GROUP BY grp ORDER BY grp'
            h0 = 'SELECT grp,SUM(score) AS total FROM items GROUP BY grp ORDER BY grp'
            h1 = 'SELECT grp,SUM(score) AS total,COUNT(score) AS n_nonnull FROM items GROUP BY grp ORDER BY grp'
            transform = 'SELECT grp,CASE WHEN n_nonnull=0 THEN NULL ELSE total*1.0/n_nonnull END AS answer FROM cache ORDER BY grp'
        elif template == 'avg_weighted_rollup':
            world = {'items': [item(1,score=1),item(2,score=5,grp='B'),item(3,score=None,grp='C')]}
            other = copy.deepcopy(world)
            other['items'].append(item(4,score=1))
            q = 'SELECT AVG(score) AS answer FROM items'
            h0 = 'SELECT grp,AVG(score) AS mean FROM items GROUP BY grp ORDER BY grp'
            h1 = 'SELECT grp,SUM(score) AS total,COUNT(score) AS n_nonnull FROM items GROUP BY grp ORDER BY grp'
            transform = 'SELECT CASE WHEN COALESCE(SUM(n_nonnull),0)=0 THEN NULL ELSE SUM(total)*1.0/SUM(n_nonnull) END AS answer FROM cache'
        elif template == 'avg_distinct':
            world = {'items': [item(1,score=0),item(2,score=0),item(3,score=6),item(4,score=None)]}
            other = {'items': [item(1,score=0),item(2,score=3),item(3,score=3),item(4,score=None)]}
            q = 'SELECT AVG(DISTINCT score) AS answer FROM items'
            h0 = 'SELECT SUM(score) AS total,COUNT(score) AS n_nonnull FROM items'
            h1 = 'SELECT DISTINCT score FROM items ORDER BY score'
            transform = 'SELECT AVG(score) AS answer FROM cache'
        proof = ('Ordinary AVG needs SUM and the nonnull count, optionally rolled up over complete groups. '
                 'AVG DISTINCT instead needs the distinct value set; repeated values cannot be reconstructed from SUM/COUNT alone.')
    elif template.startswith('scope_'):
        if template == 'scope_disjoint':
            world = {'items': [item(1,period=2025),item(2,period=2026)]}
            other = copy.deepcopy(world)
            other['items'].append(item(3,period=2026))
            q = 'SELECT COUNT(*) AS answer FROM items WHERE period=2026'
            h0 = 'SELECT COUNT(*) AS n FROM items WHERE period=2025'
            h1 = 'SELECT period,COUNT(*) AS n FROM items GROUP BY period ORDER BY period'
            transform = 'SELECT COALESCE(SUM(n),0) AS answer FROM cache WHERE period=2026'
        elif template == 'scope_boolean':
            world = {'items': [item(1,grp='A',active=0),item(2,grp='B',active=1)]}
            other = copy.deepcopy(world)
            other['items'][0][5] = 1
            q = "SELECT COUNT(*) AS answer FROM items WHERE grp='A' AND active=1"
            h0 = "SELECT COUNT(*) AS n FROM items WHERE grp='A' OR active=1"
            h1 = 'SELECT id,grp,active FROM items ORDER BY id'
            transform = "SELECT COUNT(*) AS answer FROM cache WHERE grp='A' AND active=1"
        elif template == 'scope_boundary':
            world = {'items': [item(1,period=2026),item(2,period=2027),item(3,period=2028)]}
            other = copy.deepcopy(world)
            other['items'][1][3] = 2028
            q = 'SELECT COUNT(*) AS answer FROM items WHERE period>=2026 AND period<2028'
            h0 = 'SELECT COUNT(*) AS n FROM items WHERE period>=2026 AND period<=2028'
            h1 = 'SELECT period,COUNT(*) AS n FROM items GROUP BY period ORDER BY period'
            transform = 'SELECT COALESCE(SUM(n),0) AS answer FROM cache WHERE period>=2026 AND period<2028'
        elif template == 'scope_joint_group':
            world = {'items': [item(1,grp='A'),item(2,grp='B'),item(3,grp='B',period=2025)]}
            other = copy.deepcopy(world)
            other['items'][1][2] = 'A'
            q = "SELECT COUNT(*) AS answer FROM items WHERE period=2026 AND grp='A'"
            h0 = 'SELECT period,COUNT(*) AS n FROM items GROUP BY period ORDER BY period'
            h1 = 'SELECT period,grp,COUNT(*) AS n FROM items GROUP BY period,grp ORDER BY period,grp'
            transform = "SELECT COALESCE(SUM(n),0) AS answer FROM cache WHERE period=2026 AND grp='A'"
        proof = 'Complete projected predicate columns or complete counts by every predicate key permit exact filtering and aggregation.'
    elif template.startswith('group_'):
        world = {'items': [item(i,grp='Blue') for i in (1,2,3)] + [item(4,grp='Gold')]}
        other = copy.deepcopy(world)
        other['items'].append(item(5,grp='Gold'))
        full = 'SELECT grp,COUNT(*) AS n FROM items GROUP BY grp'
        h0 = full + ' ORDER BY n DESC,grp LIMIT 1'
        h1 = full + ' ORDER BY grp'
        if template == 'group_target':
            q = "SELECT COUNT(*) AS answer FROM items WHERE grp='Gold'"
            transform = "SELECT COALESCE(SUM(n),0) AS answer FROM cache WHERE grp='Gold'"
        elif template == 'group_threshold':
            threshold = instance + 2
            q = f'SELECT COUNT(*) AS answer FROM ({full} HAVING COUNT(*)>={threshold}) AS groups'
            transform = f'SELECT COUNT(*) AS answer FROM cache WHERE n>={threshold}'
        elif template == 'group_max_ties':
            world['items'].append(item(5,grp='Gold'))
            other['items'].append(item(6,grp='Gold'))
            q = f'WITH counts AS ({full}) SELECT grp FROM counts WHERE n=(SELECT MAX(n) FROM counts) ORDER BY grp'
            transform = 'SELECT grp FROM cache WHERE n=(SELECT MAX(n) FROM cache) ORDER BY grp'
        elif template == 'group_having_loss':
            world = {'items': [item(1,grp='Blue'),item(2,grp='Blue'),item(3,grp='Gold')]}
            other = copy.deepcopy(world)
            other['items'].append(item(4,grp='Red'))
            q = 'SELECT COUNT(DISTINCT grp) AS answer FROM items'
            h0 = full + ' HAVING COUNT(*)>=2 ORDER BY grp'
            transform = 'SELECT COUNT(*) AS answer FROM cache'
        proof = 'A complete group-count table retains every group and its cardinality; filtering, sums, or ranking of groups use only these columns.'
    elif template.startswith('join_'):
        schema = JOIN_SCHEMA
        world = {'member': [[1,2],[2,4]], 'event': [[10,'X'],[20,'X'],[30,'Y']],
                 'attendance': [[10,1],[20,1]]}
        other = copy.deepcopy(world)
        joined = 'FROM member m JOIN attendance a ON a.member_id=m.id JOIN event e ON e.id=a.event_id'
        matched = "EXISTS(SELECT 1 FROM attendance a JOIN event e ON e.id=a.event_id WHERE a.member_id=m.id AND e.title='X')"
        if template == 'join_distinct_count':
            other['attendance'][1][1] = 2
            q = f'SELECT COUNT(*) AS answer FROM member m WHERE {matched}'
            h0 = f"SELECT COUNT(*) AS n_edges {joined} WHERE e.title='X'"
            h1 = f"SELECT DISTINCT m.id AS id {joined} WHERE e.title='X' ORDER BY id"
            transform = 'SELECT COUNT(*) AS answer FROM cache'
        elif template == 'join_unique_sum':
            other['attendance'] = [[10,2]]
            q = f'SELECT SUM(m.weight) AS answer FROM member m WHERE {matched}'
            h0 = f"SELECT SUM(m.weight) AS total {joined} WHERE e.title='X'"
            h1 = f"SELECT DISTINCT m.id AS id,m.weight AS weight {joined} WHERE e.title='X' ORDER BY id"
            transform = 'SELECT SUM(weight) AS answer FROM cache'
        elif template == 'join_antijoin':
            world['attendance'] = other['attendance'] = [[10,1]]
            other['member'].append([3,8])
            q = 'SELECT COUNT(*) AS answer FROM member m WHERE NOT EXISTS(SELECT 1 FROM attendance a WHERE a.member_id=m.id)'
            h0 = 'SELECT DISTINCT member_id FROM attendance ORDER BY member_id'
            h1 = 'SELECT m.id AS id,COUNT(a.member_id) AS n_edges FROM member m LEFT JOIN attendance a ON a.member_id=m.id GROUP BY m.id ORDER BY m.id'
            transform = 'SELECT COUNT(*) AS answer FROM cache WHERE n_edges=0'
        elif template == 'join_intersection':
            world['attendance'] = [[10,1],[30,1]]
            other['attendance'] = [[10,1],[30,2]]
            q = f"SELECT COUNT(*) AS answer FROM member m WHERE {matched} AND " + matched.replace("'X'", "'Y'")
            h0 = f"SELECT e.title AS title,COUNT(DISTINCT m.id) AS n {joined} WHERE e.title IN ('X','Y') GROUP BY e.title ORDER BY e.title"
            h1 = f"SELECT DISTINCT m.id AS id,e.title AS title {joined} WHERE e.title IN ('X','Y') ORDER BY id,title"
            transform = 'SELECT COUNT(*) AS answer FROM (SELECT id FROM cache GROUP BY id HAVING COUNT(DISTINCT title)=2) AS both_titles'
        proof = 'Full entity IDs avoid join multiplicity; anti-join requires coverage of unlinked entities; intersection requires per-entity membership, not marginal counts.'
    elif template.startswith('rank_'):
        world = {'items': [item(1,score=9),item(2,score=7),item(3,score=3,grp='B'),item(4,score=None,grp='B')]}
        other = copy.deepcopy(world)
        other['items'][1][4] = 9
        h1 = 'SELECT id,score FROM items ORDER BY id'
        h0 = 'SELECT id,score FROM items WHERE score IS NOT NULL ORDER BY score DESC,id LIMIT 1'
        if template == 'rank_max_ids':
            q = 'SELECT id FROM items WHERE score=(SELECT MAX(score) FROM items) ORDER BY id'
            transform = 'SELECT id FROM cache WHERE score=(SELECT MAX(score) FROM cache) ORDER BY id'
        elif template == 'rank_max_count':
            q = 'SELECT COUNT(*) AS answer FROM items WHERE score=(SELECT MAX(score) FROM items)'
            h0 = 'SELECT MAX(score) AS maximum FROM items'
            h1 = 'SELECT score,COUNT(*) AS n FROM items WHERE score IS NOT NULL GROUP BY score ORDER BY score'
            transform = 'SELECT COALESCE(SUM(n),0) AS answer FROM cache WHERE score=(SELECT MAX(score) FROM cache)'
        elif template == 'rank_topk_ties':
            other['items'][1][4] = 7
            other['items'][2][4] = 7
            q = 'SELECT id FROM items WHERE score>=(SELECT score FROM items WHERE score IS NOT NULL ORDER BY score DESC,id LIMIT 1 OFFSET 1) ORDER BY id'
            h0 = 'SELECT id,score FROM items WHERE score IS NOT NULL ORDER BY score DESC,id LIMIT 2'
            transform = 'SELECT id FROM cache WHERE score>=(SELECT score FROM cache WHERE score IS NOT NULL ORDER BY score DESC,id LIMIT 1 OFFSET 1) ORDER BY id'
        elif template == 'rank_partitioned':
            other = copy.deepcopy(world)
            other['items'][0][0] = 5
            q = 'SELECT t.id AS id FROM items t WHERE t.score=(SELECT MAX(u.score) FROM items u WHERE u.grp=t.grp) ORDER BY t.id'
            h0 = 'SELECT grp,MAX(score) AS maximum FROM items GROUP BY grp ORDER BY grp'
            h1 = 'SELECT id,grp,score FROM items ORDER BY id'
            transform = 'SELECT t.id AS id FROM cache t WHERE t.score=(SELECT MAX(u.score) FROM cache u WHERE u.grp=t.grp) ORDER BY t.id'
        proof = 'All relevant IDs/scores retain tied membership; a full score-frequency table retains only tie cardinality, not IDs. Global extrema do not retain partition winners.'
    if not all((q,h0,h1,transform)):
        raise ValueError('Unsupported template: ' + template)
    world, other = [vary(w, template, instance) for w in (world, other)]
    return {'template': template, 'instance': instance, 'schema': schema, 'world': world,
            'other': other, 'current_sql': q, 'lossy_sql': h0, 'sufficient_sql': h1,
            'transform_sql': transform, 'request': target, 'positive_rule': proof}


def vary(world, template, instance):
    """Paired variation preserves the intended collision, verified after execution.

    Replications below are inside each template cluster, never a structural
    holdout claim. The rank-top-k and HAVING cases require special boundary
    preservation; their boundary is not selected using model performance.
    """
    result = {key: [] for key in world}
    k = instance + 1
    for table, rows in world.items():
        for block in range(k):
            for original in rows:
                row = list(original)
                shift = instance * 1000 + block * 100
                if table == 'items':
                    if template == 'rank_topk_ties' and block > 0:
                        continue
                    if template == 'group_having_loss' and row[2] != 'Blue' and block > 0:
                        continue
                    row[0] += shift
                    row[1] += f'_i{instance}'
                    if row[4] is not None:
                        row[4] *= k
                elif table == 'pairs':
                    row[0] += shift
                    row[2] += f'_i{instance}'
                elif table == 'member':
                    row[0] += shift
                    row[1] *= k
                elif table == 'event':
                    row[0] += shift
                elif table == 'attendance':
                    row[0] += shift
                    row[1] += shift
                result[table].append(row)
    return result


def connect_world(spec, world):
    con = sqlite3.connect(':memory:')
    try:
        con.execute('PRAGMA foreign_keys=ON')
        for table, columns, ddl in spec['schema']:
            if len(world[table])>1000: raise ValueError('Declared row bound exceeded')
            if table=='member' and any(not -10000<=r[1]<=10000 for r in world[table]):
                raise ValueError('Declared weight bound exceeded')
            con.execute(ddl)
            con.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in columns)+')', world[table])
        if con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Invalid witness foreign keys')
        con.execute('PRAGMA query_only=ON')
        return con
    except BaseException:
        con.close()
        raise


def execute_sql(con, sql):
    cur = con.execute(sql)
    return {'columns': [c[0] for c in cur.description], 'rows': [list(r) for r in cur.fetchall()]}


def execute_transform(cache, sql):
    con = sqlite3.connect(':memory:')
    try:
        columns = cache['columns']
        con.execute('CREATE TABLE cache('+','.join('"'+c.replace('"','""')+'"' for c in columns)+')')
        con.executemany('INSERT INTO cache VALUES('+','.join('?' for _ in columns)+')', cache['rows'])
        con.execute('PRAGMA query_only=ON')
        return execute_sql(con, sql)
    finally:
        con.close()


def python_oracle(spec, world):
    """Independent row/set/Fraction computation; never executes/parses SQL."""
    t = spec['template']
    rows = [dict(zip(ITEM_COLUMNS,r)) for r in world.get('items',[])]
    groups = defaultdict(list)
    for row in rows:
        groups[row['grp']].append(row)
    def mean(values):
        values = [v for v in values if v is not None]
        return None if not values else float(Fraction(sum(values), len(values)))
    if t == 'entity_global': ans = [[len(rows)]]
    elif t == 'entity_grouped': ans = [[g,len(rs)] for g,rs in sorted(groups.items())]
    elif t == 'entity_postfilter': ans = [[sum(r['active']==1 for r in rows)]]
    elif t == 'entity_composite': ans = [[len({tuple(r[:2]) for r in world['pairs']})]]
    elif t in ('avg_global','avg_weighted_rollup'): ans = [[mean(r['score'] for r in rows)]]
    elif t == 'avg_grouped': ans = [[g,mean(r['score'] for r in rs)] for g,rs in sorted(groups.items())]
    elif t == 'avg_distinct': ans = [[mean(set(r['score'] for r in rows))]]
    elif t == 'scope_disjoint': ans = [[sum(r['period']==2026 for r in rows)]]
    elif t == 'scope_boolean': ans = [[sum(r['grp']=='A' and r['active']==1 for r in rows)]]
    elif t == 'scope_boundary': ans = [[sum(2026<=r['period']<2028 for r in rows)]]
    elif t == 'scope_joint_group': ans = [[sum(r['period']==2026 and r['grp']=='A' for r in rows)]]
    elif t == 'group_target': ans = [[len(groups['Gold'])]]
    elif t == 'group_threshold': ans = [[sum(len(rs)>=spec['instance']+2 for rs in groups.values())]]
    elif t == 'group_max_ties':
        largest = max(map(len,groups.values()),default=None)
        ans = [[g] for g,rs in sorted(groups.items()) if len(rs)==largest]
    elif t == 'group_having_loss': ans = [[len(groups)]]
    elif t.startswith('join_'):
        weights = dict(world['member'])
        titles = dict(world['event'])
        members_x = {m for e,m in world['attendance'] if titles[e]=='X'}
        members_y = {m for e,m in world['attendance'] if titles[e]=='Y'}
        if t == 'join_distinct_count': ans = [[len(members_x)]]
        elif t == 'join_unique_sum': ans = [[sum(weights[m] for m in members_x) if members_x else None]]
        elif t == 'join_antijoin': ans = [[len(set(weights)-{m for e,m in world['attendance']})]]
        elif t == 'join_intersection': ans = [[len(members_x & members_y)]]
    elif t.startswith('rank_'):
        nonnull = [r for r in rows if r['score'] is not None]
        top = max((r['score'] for r in nonnull),default=None)
        winners = [r['id'] for r in nonnull if r['score']==top]
        if t == 'rank_max_ids': ans = [[i] for i in sorted(winners)]
        elif t == 'rank_max_count': ans = [[len(winners)]]
        elif t == 'rank_topk_ties':
            ordered = sorted(nonnull,key=lambda r:(-r['score'],r['id']))
            cutoff = ordered[1]['score'] if len(ordered)>=2 else None
            ans = [[r['id']] for r in sorted(nonnull,key=lambda r:r['id'])
                   if cutoff is not None and r['score']>=cutoff]
        elif t == 'rank_partitioned':
            by_group = {g:max((r['score'] for r in rs if r['score'] is not None),default=None)
                        for g,rs in groups.items()}
            ans = [[r['id']] for r in sorted(nonnull,key=lambda r:r['id']) if r['score']==by_group[r['grp']]]
    else:
        raise ValueError('Oracle not implemented: '+t)
    return ans


def same_rows(left, right):
    # Queries explicitly order non-scalar rows; keep schema checks separate.
    if len(left)!=len(right): return False
    for a,b in zip(left,right):
        if len(a)!=len(b): return False
        for x,y in zip(a,b):
            if x is None or y is None:
                if x is not y: return False
            elif isinstance(x,(int,float)) and isinstance(y,(int,float)):
                if not math.isclose(x,y,abs_tol=1e-12,rel_tol=1e-12): return False
            elif type(x) is not type(y) or x!=y: return False
    return True


def build_pair(template, instance, split, family):
    spec = template_spec(template, instance)
    con = connect_world(spec,spec['world'])
    other = connect_world(spec,spec['other'])
    try:
        current = execute_sql(con,spec['current_sql'])
        counter = execute_sql(other,spec['current_sql'])
        if not same_rows(current['rows'],python_oracle(spec,spec['world'])):
            raise ValueError('Current-query independent oracle mismatch: '+template)
        if not same_rows(counter['rows'],python_oracle(spec,spec['other'])):
            raise ValueError('Counterworld independent oracle mismatch: '+template)
        lossy = execute_sql(con,spec['lossy_sql'])
        other_lossy = execute_sql(other,spec['lossy_sql'])
        if lossy != other_lossy or same_rows(current['rows'],counter['rows']):
            raise ValueError('Invalid collision witness: '+template)
        result=[]
        for sufficient,query in ((False,spec['lossy_sql']),(True,spec['sufficient_sql'])):
            cache = execute_sql(con,query)
            transform_checked = None
            if sufficient:
                for db in (con,other):
                    transformed = execute_transform(execute_sql(db,query),spec['transform_sql'])
                    target = execute_sql(db,spec['current_sql'])
                    if transformed['columns']!=target['columns'] or not same_rows(transformed['rows'],target['rows']):
                        raise ValueError('Positive transform mismatch: '+template)
                transform_checked = True
            public = {'schema_sql': [ddl for _,_,ddl in spec['schema']],
                'current_request': spec['request'], 'current_sql': spec['current_sql'],
                'history_sql': query, 'cache': dict(cache,complete=True),
                'completeness_scope': 'Complete output of history SQL, including its WHERE/HAVING/LIMIT. '
                                      'It does not assert coverage of rows omitted by that query.',
                'provenance': {'trusted_history_execution':True,'same_snapshot':True},
                'domain_constraints': 'At most 1000 rows per table. Declared STRICT types and keys hold; '
                                      'member weights are integers between -10000 and 10000. No unlisted dependencies.',
                'numeric_policy': 'AVG ignores NULL; an empty nonnull aggregate has SQL NULL except COUNT=0. '
                                  'Use real division. Integer sums stay in exact range under these bounds.',
                'recompute_available':True}
            result.append({'base_id':f'{split}-{template}-{instance:02d}',
                'case_id':f'{split}-{template}-{instance:02d}-v{int(sufficient)}',
                'split':split,'family':family,'template':template,'instance':instance,'public':public,
                'public_sha256':digest(public),
                'audit':{'sufficient':sufficient,'world':spec['world'],'current_result':current,
                    'other_world':spec['other'],'other_current_result':counter,
                    'witness_cache_equal':not sufficient,'python_oracle_verified':True,
                    'positive_transform_sql':spec['transform_sql'] if sufficient else None,
                    'positive_rule':spec['positive_rule'] if sufficient else None,
                    'positive_transform_checked_on_both_worlds':transform_checked,
                    'exact_numeric_tolerance':{'abs':1e-12,'rel':1e-12}}})
        return result
    finally:
        con.close(); other.close()


def build_suite(split):
    if split not in ('development','confirmation_candidate'):
        raise ValueError('Unknown split')
    templates = DEVELOPMENT if split=='development' else CONFIRMATION
    n_instances = 2 if split=='development' else 8
    cases=[]
    for family,names in templates.items():
        for name in names:
            for i in range(n_instances):
                cases.extend(build_pair(name,i,split,family))
    if len({c['public_sha256'] for c in cases})!=len(cases):
        raise ValueError('Duplicate public context: do not inflate sample count')
    return cases


def structural_catalog():
    """Structural distinctions are recorded, not inferred from a different seed."""
    return {
        'entity_projection': 'dev global/group entity counts; held post-cache filtering and composite keys',
        'null_aggregation': 'dev ordinary/global and per-group AVG; held weighted rollup and DISTINCT AVG',
        'scope': 'dev disjoint periods and AND/OR; held overlapping interval endpoints and missing joint group key',
        'group_coverage': 'dev target count and threshold groups; held tied largest groups and HAVING-omitted groups',
        'join_multiplicity': 'dev semijoin entity count/sum; held anti-join and intersection across two memberships',
        'ranking_ties': 'dev global maximum IDs/tie count; held top-k WITH ties and partitioned maxima',
    }
