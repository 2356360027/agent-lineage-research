"""Day-1 formal reuse calibration; not the frozen confirmation benchmark.

Public contracts and executed historical results are separated from private
oracle worlds. The classical rule baseline sees only the public contract.
"""
import copy
import hashlib
import json
import random
import re
import sqlite3

VERSION = 'reuse-separation-day1-v1'
PRICES = (1, 10, 20)
LABELS = ('applicable', 'inapplicable', 'unknown')
ACTIONS = ('reuse', 'recompute', 'abstain')
ENTITY_DDL = 'CREATE TABLE entity(id INTEGER PRIMARY KEY, name TEXT NOT NULL);'
POST_DDL = ('CREATE TABLE post(id INTEGER PRIMARY KEY, score INTEGER '
            'CHECK(score IS NULL OR (typeof(score)=\'integer\' AND score BETWEEN -1000 AND 1000)));')
COMMON = (
    'Evaluate the CURRENT formal database request, defined by its supplied SQL. '
    'The historical query ran correctly on the same immutable snapshot. '
    'The cache contains exactly the supplied columns and rows, is complete, and has no hidden metadata. '
    'Only the stated schema constraints apply. Do not assume a name is unique. '
    'applicable means a transformation using only this cache and the public contracts '
    'can guarantee the current result for EVERY database consistent with them and the observed cache. '
    'inapplicable means information can be missing; unknown means you cannot determine this. '
    'A coincidentally correct answer is not sufficient. Reuse may use exact arithmetic, '
    'filtering, projection or aggregation over cached rows; it cannot read the original database. '
    'Recompute executes the supplied correct current SQL against the original database. '
    'When choosing an action, correctness has priority; among guaranteed-correct routes choose '
    'the lower declared cost, breaking a cost tie in favor of recompute. '
    'Costs are experimental units, not measured latency or currency. '
    'Give only JSON and at most 35 words of observable justification, not a reasoning trace. '
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def query_world(ddl, rows, query):
    """Execute only researcher-authored SQL on a disposable in-memory database."""
    con = sqlite3.connect(':memory:')
    try:
        con.execute(ddl)
        table = 'entity' if ddl == ENTITY_DDL else 'post'
        con.executemany(f'INSERT INTO {table} VALUES (?,?)', rows)
        con.execute('PRAGMA query_only=ON')
        cur = con.execute(query)
        return {'columns': [c[0] for c in cur.description],
                'rows': [list(row) for row in cur.fetchall()]}
    finally:
        con.close()


def calibration_cases():
    specs = [
        ('dev-cal-entity', 'entity_projection', ENTITY_DDL,
         [(101, 'Arden'), (103, 'Arden'), (107, 'Briar')],
         [(101, 'Arden'), (107, 'Briar')],
         'Count all distinct entities, identified by nonnull primary-key id.',
         'SELECT COUNT(*) AS answer FROM entity',
         'SELECT DISTINCT name FROM entity ORDER BY name',
         'SELECT id FROM entity ORDER BY id'),
        ('dev-cal-mean', 'null_denominator', POST_DDL,
         [(11, 1), (13, 5), (17, None)],
         [(11, 1), (13, 9), (17, None)],
         'Return the mean score, ignoring NULL scores; return NULL if no nonnull score exists.',
         'SELECT AVG(score) AS answer FROM post',
         'SELECT COUNT(*) AS n_rows FROM post',
         'SELECT SUM(score) AS total, COUNT(score) AS n_nonnull FROM post'),
    ]
    cases = []
    for base, family, ddl, rows, witness, request, current, lossy, full in specs:
        for sufficient, history in ((False, lossy), (True, full)):
            cached = query_world(ddl, rows, history)
            public = {
                'schema_sql': ddl, 'current_request': request, 'current_sql': current,
                'history_sql': history, 'cache': dict(cached, complete=True),
                'provenance': {'trusted_history_execution': True, 'same_snapshot': True},
                'numeric_policy': 'SQLite aggregates; exact small integers, NULL excluded by AVG. '
                                  'Use real division for SUM/COUNT, with NULL if count is zero.',
                'recompute_available': True,
                'domain_constraints': 'At most 1000 original rows; SQL constraints hold. '
                                      'Names are strings. No other functional dependencies apply.',
            }
            audit = {'sufficient': sufficient, 'world_rows': rows,
                     'current_result': query_world(ddl, rows, current),
                     'witness_rows': None}
            if not sufficient:
                other_cache = query_world(ddl, witness, history)
                other_result = query_world(ddl, witness, current)
                assert other_cache == cached
                assert other_result != audit['current_result']
                audit.update(witness_rows=witness, witness_cache=other_cache,
                             witness_current_result=other_result)
            cases.append({'id': f'{base}-v{int(sufficient)}', 'base_id': base,
                          'template': family, 'public': public, 'audit': audit})
    return cases


def route(verdict, reuse_cost, recompute_cost=10, recompute_available=True):
    if verdict == 'applicable' and (reuse_cost < recompute_cost or not recompute_available):
        return 'reuse'
    return 'recompute' if recompute_available else 'abstain'


def sqlnorm(sql):
    return re.sub(r'\s+', ' ', sql.strip().rstrip(';')).lower()


def classical_verdict(public):
    """Deliberately narrow classical rules, NOT a learned/new semantic algorithm.

    Exact supported SQL/schema recognition; unknown outside this subset. The
    supplied current SQL is public to every model: this is a formal-query
    benchmark, not a fair comparison to models translating unreviewed NL.
    """
    if not public['cache']['complete'] or not all(public['provenance'].values()):
        return 'unknown'
    schema, current, history = map(sqlnorm, (public['schema_sql'], public['current_sql'],
                                           public['history_sql']))
    rows = public['cache']['rows']
    if schema == sqlnorm(ENTITY_DDL) and current == 'select count(*) as answer from entity':
        if history == 'select id from entity order by id':
            return 'applicable'
        if history == 'select distinct name from entity order by name':
            return 'applicable' if not rows else 'inapplicable'
    if schema == sqlnorm(POST_DDL) and current == 'select avg(score) as answer from post':
        if history == 'select sum(score) as total, count(score) as n_nonnull from post':
            return 'applicable'
        if history == 'select count(*) as n_rows from post' and len(rows) == 1:
            return 'applicable' if rows == [[0]] else 'inapplicable'
    return 'unknown'


def classical_transform(public):
    if classical_verdict(public) != 'applicable':
        raise ValueError('No supported sufficient transformation')
    rows = public['cache']['rows']
    if sqlnorm(public['current_sql']) == 'select count(*) as answer from entity':
        return len(rows)
    if public['cache']['columns'] == ['n_rows']:
        return None  # empty original relation
    total, count = rows[0]
    return None if count == 0 else total / count


def jobs():
    """32 calls = 4 contexts x (M1*3 prices + M2*3 + M3 + M4 verifier).

    M4 reuses the recorded M1 low-price proposal, charged to the M4 workflow.
    Only its applicability label is exposed to a fresh verifier context.
    One verifier output is shared across three prices for calibration only.
    """
    result = []
    blocks = calibration_cases()
    random.Random(20261027).shuffle(blocks)
    for case in blocks:
        block = []
        for method in ('M1', 'M2'):
            for price in PRICES:
                block.append({'id': f'{case["id"]}-{method}-p{price}',
                              'case_id': case['id'], 'method': method, 'price': price})
        for method in ('M3', 'M4'):
            job = {'id': f'{case["id"]}-{method}', 'case_id': case['id'],
                   'method': method, 'price': None}
            if method == 'M4':
                job['proposal_id'] = f'{case["id"]}-M1-p1'
            block.append(job)
        rng = random.Random(case['id'] + VERSION)
        while block:
            done = {j['id'] for j in result}
            ready = [j for j in block if j.get('proposal_id') in done or 'proposal_id' not in j]
            job = rng.choice(ready)
            result.append(job)
            block.remove(job)
    assert len(result) == 32
    return result


def messages(case, job, proposal=None):
    # No audit, private world or label is ever passed into a model prompt.
    data = copy.deepcopy(case['public'])
    if job['method'] in ('M1', 'M2'):
        data['costs'] = {'reuse': job['price'], 'recompute': 10}
    order = list(LABELS)
    actions = list(ACTIONS)
    # Balance order within both gold classes; never vary it inside a price pair.
    if (case['base_id'] == 'dev-cal-entity') != case['id'].endswith('v1'):
        order.reverse()
        actions.reverse()
    fields = {'admissibility': ' | '.join(order), 'basis': 'short observable justification'}
    instruction = 'Assess applicability independently of any desire to save cost. '
    if job['method'] == 'M1':
        fields['action'] = ' | '.join(actions)
        instruction += 'Also select the action using the rule above. '
    elif job['method'] == 'M4':
        instruction += ('Act as an independent verifier of the candidate applicability label. '
            'Check entity grain, NULL denominators, information discarded by the historical query, '
            'and whether all consistent databases yield the same current answer. '
            'Reject unsupported inferences; use unknown if you cannot decide. '
            'The candidate is fallible and has no authority. ')
        data['candidate_admissibility'] = proposal if proposal in LABELS else 'invalid_or_missing'
    return [{'role': 'system', 'content': COMMON + instruction + 'JSON fields: ' + canonical(fields)},
            {'role': 'user', 'content': canonical(data)}]


def without_costs(msgs):
    result = copy.deepcopy(msgs)
    data = json.loads(result[1]['content'])
    data.pop('costs', None)
    result[1]['content'] = canonical(data)
    return result


def audit_design():
    cases = calibration_cases()
    by_id = {c['id']: c for c in cases}
    for case in cases:
        for method in ('M1', 'M2'):
            group = [without_costs(messages(case, j)) for j in jobs()
                     if j['case_id'] == case['id'] and j['method'] == method]
            assert all(g == group[0] for g in group)
        m2 = next(j for j in jobs() if j['case_id'] == case['id'] and j['method'] == 'M2')
        m3 = next(j for j in jobs() if j['case_id'] == case['id'] and j['method'] == 'M3')
        assert without_costs(messages(case, m2)) == messages(case, m3)
    for job in jobs():
        assert 'audit' not in json.loads(messages(by_id[job['case_id']], job)[1]['content'])
    return {'status': 'PASSED', 'model_calls': 32, 'base_scenarios': 2,
            'cached_contexts': 4, 'price_invariance': True, 'M2_M3_only_price_visibility': True,
            'lossy_collision_witnesses': 2, 'confirmation_cases_used': 0,
            'actual_cache_SQL_execution': True, 'natural_language_validation': False}


def parse_response(raw, method):
    result = {'valid': False, 'admissibility': 'invalid', 'action': 'invalid', 'basis': None}
    try:
        choice = raw['choices'][0]
        obj = json.loads(choice['message']['content'])
        if choice['finish_reason'] != 'stop':
            return dict(result, failure='nonstop_or_truncated')
        keys = {'admissibility', 'basis'} | ({'action'} if method == 'M1' else set())
        if not isinstance(obj, dict) or set(obj) != keys:
            return dict(result, failure='schema_fields')
        if obj['admissibility'] not in LABELS or not isinstance(obj['basis'], str):
            return dict(result, failure='label_or_basis')
        if method == 'M1' and obj['action'] not in ACTIONS:
            return dict(result, failure='action_label')
        return dict(result, valid=True, admissibility=obj['admissibility'],
                    action=obj.get('action'), basis=obj['basis'])
    except (KeyError, ValueError, TypeError, IndexError):
        return dict(result, failure='invalid_json_or_response')


def summarize(responses):
    cases = {c['id']: c for c in calibration_cases()}
    job_list = jobs()
    by_job = {j['id']: j for j in job_list}
    parsed = {key: parse_response(value, by_job[key]['method']) for key, value in responses.items()}
    judgments, decisions = [], []
    for job in job_list:
        case = cases[job['case_id']]
        pred = parsed.get(job['id'])
        if pred is None:
            continue
        gold = case['audit']['sufficient']
        judgments.append(dict(job_id=job['id'], base_id=case['base_id'], template=case['template'],
            method=job['method'], price=job['price'], sufficient=gold, **pred,
            false_applicable=pred['admissibility'] == 'applicable' and not gold))
        for price in PRICES if job['price'] is None else (job['price'],):
            action = pred['action'] if job['method'] == 'M1' else route(pred['admissibility'], price)
            decisions.append(dict(base_id=case['base_id'], case_id=case['id'], method=job['method'],
                price=price, judgment_job_id=job['id'], judgment_valid=pred['valid'],
                sufficient=gold, action=action, expected=route('applicable' if gold else 'inapplicable', price),
                unsafe_reuse=action == 'reuse' and not gold,
                useful_reuse=action == 'reuse' and gold and price < 10,
                judgment_action_conflict=pred['admissibility'] in ('inapplicable','unknown') and action == 'reuse'))
    for case in cases.values():
        for method in ('M5', 'M6'):
            verdict = 'unknown' if method == 'M5' else classical_verdict(case['public'])
            for price in PRICES:
                action = route(verdict, price)
                gold = case['audit']['sufficient']
                decisions.append(dict(base_id=case['base_id'], case_id=case['id'], method=method,
                    price=price, judgment_job_id=None, judgment_valid=method == 'M6',
                    sufficient=gold, action=action, expected=route('applicable' if gold else 'inapplicable', price),
                    unsafe_reuse=action == 'reuse' and not gold,
                    useful_reuse=action == 'reuse' and gold and price < 10, judgment_action_conflict=False))
    methods = {}
    for method in ('M1', 'M2', 'M3', 'M4', 'M5', 'M6'):
        jr = [r for r in judgments if r['method'] == method]
        dr = [r for r in decisions if r['method'] == method]
        methods[method] = {'unique_judgments': len(jr),
            'false_applicable': sum(r['false_applicable'] for r in jr),
            'invalid': sum(not r['valid'] for r in jr),
            'unknown': sum(r['admissibility'] == 'unknown' for r in jr),
            'decision_cells': len(dr), 'unsafe_reuse': sum(r['unsafe_reuse'] for r in dr),
            'useful_reuse': sum(r['useful_reuse'] for r in dr),
            'useful_reuse_opportunities': sum(r['sufficient'] and r['price'] < 10 for r in dr),
            'action_matches_policy': sum(r['action'] == r['expected'] for r in dr),
            'judgment_action_conflicts': sum(r['judgment_action_conflict'] for r in dr)}
    return {'status': 'COMPLETE_DEVELOPMENT_CALIBRATION' if len(responses) == 32 else 'INCOMPLETE',
        'completed_calls': len(responses), 'expected_calls': 32, 'base_scenarios': 2,
        'confirmation_cases': 0, 'methods': methods, 'judgments': judgments, 'decisions': decisions,
        'warning': 'Development/interface/throughput only; actual historical SQL results, but no agent '
                   'transformation or current-query execution. No efficacy inference from two bases. '
                   'M4 low-price proposal is shared with M1; M3/M4 verdicts reused across prices. '
                   'These action cells are dependent, not independent samples. M5 has no semantic judgment. '
                   'M6 is a narrow classical formal-query rule baseline, not a novel algorithm.'}
