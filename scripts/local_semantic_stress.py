"""Offline, post-hoc SQL robustness experiment. No LLM, network or real DB writes.

Exhaustive only over the explicitly bounded synthetic domain, NOT SQL equivalence.
Historical queries remain verbatim. The Python set oracle is independent of SQL.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
import time

TARGET = "Women's Soccer"
DDL = """
CREATE TABLE event(event_id INTEGER PRIMARY KEY, event_name TEXT, event_date TEXT);
CREATE TABLE member(member_id INTEGER PRIMARY KEY, t_shirt_size TEXT);
CREATE TABLE attendance(link_to_event INTEGER NOT NULL REFERENCES event(event_id),
 link_to_member INTEGER NOT NULL REFERENCES member(member_id),
 PRIMARY KEY(link_to_event,link_to_member));
"""
STRONG_SQL = """SELECT COUNT(*) FROM member m WHERE m.t_shirt_size='Medium'
AND EXISTS(SELECT 1 FROM attendance a JOIN event e ON e.event_id=a.link_to_event
WHERE a.link_to_member=m.member_id AND e.event_name='Women''s Soccer')"""


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def worlds():
    # Fixed before execution: 4 event-name settings x 9 shirt settings x 16 edge sets.
    edges = list(itertools.product((1, 2), (1, 2)))
    for name_bits in itertools.product((False, True), repeat=2):
        for sizes in itertools.product(('Medium', 'Large', None), repeat=2):
            for mask in range(16):
                yield dict(events=[(i+1, TARGET if yes else 'Other', '2026-01-01')
                                   for i, yes in enumerate(name_bits)],
                           members=[(i+1, size) for i, size in enumerate(sizes)],
                           attendance=[pair for j, pair in enumerate(edges) if mask & (1 << j)])


def oracle(world):
    event_ids = {i for i, name, _ in world['events'] if name == TARGET}
    members = {i for i, size in world['members'] if size == 'Medium'}
    return len({m for e, m in world['attendance'] if e in event_ids and m in members})


def evaluate(world, queries):
    con = sqlite3.connect(':memory:')
    try:
        con.execute('PRAGMA foreign_keys=ON')
        con.executescript(DDL)
        con.executemany('INSERT INTO event VALUES(?,?,?)', world['events'])
        con.executemany('INSERT INTO member VALUES(?,?)', world['members'])
        con.executemany('INSERT INTO attendance VALUES(?,?)', world['attendance'])
        con.commit()
        con.execute('PRAGMA query_only=ON')
        ticks = [0]
        def bounded():
            ticks[0] += 1
            return ticks[0] > 1000
        con.set_progress_handler(bounded, 1000)
        results = []
        for sql in queries:
            ticks[0] = 0
            try:
                rows = con.execute(sql).fetchmany(2)
                if len(rows) != 1 or len(rows[0]) != 1 or type(rows[0][0]) is not int:
                    raise ValueError('Expected one integer scalar')
                results.append({'value': rows[0][0], 'error': None})
            except (sqlite3.Error, ValueError) as exc:
                results.append({'value': None, 'error': type(exc).__name__})
        return results
    finally:
        con.close()


def run(source, output):
    raw = source.read_bytes()
    data = json.loads(raw)
    entries = []
    for row in data['rows']:
        sqls = [a['sql'] for a in row['actions'] if a['kind'] == 'run_sql']
        if len(sqls) != 1:
            raise ValueError('Expected one original SQL per trajectory')
        entries.append(dict(job_id=row['job_id'], sql=sqls[0]))
    if len(entries) != 12 or len({e['job_id'] for e in entries}) != 12:
        raise ValueError('Wrong historical batch')
    entries.append(dict(job_id='classical_exists_control', sql=STRONG_SQL))
    output.mkdir(parents=True, exist_ok=False)
    script = Path(__file__).read_bytes()
    (output/'source.py').write_bytes(script)
    (output/'input_queries.json').write_bytes(encoded(entries))
    plan = dict(kind='POST_HOC_SYNTHETIC_PROGRAM_STRESS_NOT_NEW_MODEL_EXPERIMENT',
        source_sha256=digest(raw), source_path=str(source.resolve()),
        source_code_sha256=digest(script), generated_worlds=576,
        schema=DDL, intended_count='Distinct members across all matching named events',
        assumptions=['Numeric primary IDs', 'One edge per event/member pair',
                     'Foreign keys hold', 'Event names need not be unique',
                     'Member current shirt preference; no time-varying shirt field'],
        limits=['Researcher-built synthetic domain, not real BIRD modifications',
                'All SQL chosen before this run but witness family chosen after seeing SQL',
                'No independent validation, model effect or universal equivalence claim',
                'Worlds/trajectories are not independent human tasks'],
        created_at=datetime.now(timezone.utc).isoformat(), model_calls=0, api_cost_cny=0)
    (output/'plan.json').write_bytes(encoded(plan))
    started = time.perf_counter()
    counts = Counter()
    first_failures = {}
    rows = []
    for index, world in enumerate(worlds()):
        expected = oracle(world)
        values = evaluate(world, [e['sql'] for e in entries])
        for entry, got in zip(entries, values):
            passed = got['error'] is None and got['value'] == expected
            if not passed:
                counts[entry['job_id']] += 1
                first_failures.setdefault(entry['job_id'],
                    dict(world_id=index, world=world, expected=expected, observed=got))
            rows.append(dict(world_id=index, job_id=entry['job_id'], expected=expected,
                             observed=got, matches_entity_oracle=passed))
    (output/'observations.json').write_bytes(encoded(rows))
    (output/'first_counterexamples.json').write_bytes(encoded(first_failures))
    summary = dict(status='COMPLETED_LOCAL_SYNTHETIC_STRESS', worlds=576,
        historical_queries=12, classical_controls=1, query_executions=len(rows),
        per_query=[dict(job_id=e['job_id'], mismatched_worlds=counts[e['job_id']],
                       matched_worlds=576-counts[e['job_id']]) for e in entries],
        historical_queries_with_counterexample=sum(counts[e['job_id']] > 0 for e in entries[:-1]),
        elapsed_seconds=time.perf_counter()-started, sqlite_version=sqlite3.sqlite_version,
        model_calls=0, api_cost_cny=0, original_source_unchanged=(source.read_bytes()==raw),
        interpretation='Bounded post-hoc robustness result, not original-snapshot errors, '
                       'not cache causality and not efficacy of a new method.')
    (output/'summary.json').write_bytes(encoded(summary))
    hashes = {p.name:digest(p.read_bytes()) for p in output.iterdir() if p.is_file()}
    (output/'sha256.json').write_bytes(encoded(hashes))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output), indent=2))
