"""Software fixtures only, no research samples or network/model calls."""
from copy import deepcopy
from contextlib import closing
import itertools
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from runtime import cache_route_symmetric_tools as tools

audit = tools.base.audit
REAL_CHILD = audit.run_child


class SymmetricToolsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / 'fixture.sqlite'
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute('CREATE TABLE example(id INTEGER PRIMARY KEY)')
            connection.executemany('INSERT INTO example VALUES (?)', [(1,), (2,)])
            connection.commit()
        self.db_hash = audit.hash_file(self.db)
        self.sql = 'SELECT id FROM example ORDER BY id'
        rows = [[audit.typed(1)], [audit.typed(2)]]
        self.cache = dict(status='EXECUTED', complete=True, columns=['id'], rows=rows,
                          sql_sha256=audit.digest(self.sql), **audit.fingerprints(rows))
        self.serial = 0
        mocker = patch.object(audit, 'run_child', side_effect=lambda *a: deepcopy(self.cache))
        self.child = mocker.start()
        self.addCleanup(mocker.stop)

    def session(self, available=True, cost=1, **overrides):
        self.serial += 1
        args = dict(database_path=self.db, database_sha256=self.db_hash,
                    history_sql=self.sql, cached_result=self.cache,
                    input_receipt={'fixture': True, 'private_label': 'NOT_PUBLIC'},
                    output=self.root / str(self.serial),
                    materialized_available=available, cache_read_cost=cost)
        return tools.ToolSession(**(args | overrides))

    def call(self, session, tool):
        return session.call(tool, {'cache_id': 'v000'} if tool == 'read_cache' else {'sql': self.sql})

    def test_all_three_slot_sequences_affordable_in_all_four_conditions(self):
        for available, cost, sequence in itertools.product(
                (False, True), (1, 20), itertools.product(('read_cache', 'run_sql'), repeat=3)):
            with self.subTest(available=available, cost=cost, sequence=sequence):
                session = self.session(available, cost)
                spent = 0
                for number, tool in enumerate(sequence, 1):
                    response = self.call(session, tool)
                    charge = 10 if tool == 'run_sql' else cost if available else 0
                    spent += charge
                    self.assertEqual(response['charged_units'], charge)
                    self.assertEqual(response['status'], 'CACHE_UNAVAILABLE'
                                     if tool == 'read_cache' and not available else 'EXECUTED')
                    self.assertEqual(response['attempt'], number)
                    self.assertEqual(response['remaining_budget'], 60-spent)
                    if number == 2:
                        self.assertGreaterEqual(session.balance, 10)
                with self.assertRaises(RuntimeError): self.call(session, 'run_sql')
                self.assertFalse((session.output / 'attempt-004.pending.json').exists())
                summary = session.close()
                self.assertEqual(summary['spent_declared_units'], spent)
                self.assertEqual(summary['status'], 'TOOL_SESSION_CLOSED')

    def test_factor_isolation_and_private_data_not_in_description(self):
        states = [self.session(a, c).describe() for a, c in ((False,1),(False,20),(True,1),(True,20))]
        for left, right, field in ((0,1,'read_cost'),(2,3,'read_cost'),
                                   (0,2,'materialized_available'),(1,3,'materialized_available')):
            x, y = deepcopy(states[left]), deepcopy(states[right])
            x.pop(field); y.pop(field)
            self.assertEqual(x, y)
        state = states[0]
        self.assertFalse(state['last_attempt_sql_only'])
        self.assertEqual(state['initial_budget'], 60)
        self.assertEqual(state['max_attempts'], 3)
        self.assertNotIn('NOT_PUBLIC', json.dumps(state))
        self.assertNotIn('rows', state)

    def test_startup_and_archive_record_actual_protocol_and_sources(self):
        session = self.session(cost=20)
        started = json.loads((session.output / 'started.json').read_text())
        self.assertEqual(started['configuration'], session.describe())
        self.assertEqual(started['version'], tools.VERSION)
        self.assertEqual(started['source_sha256'], audit.hash_file(session.output/'tool_source.py'))
        self.assertEqual(started['base_source_sha256'], audit.hash_file(session.output/'base_tool_source.py'))
        self.call(session, 'read_cache')
        self.assertEqual(session.close()['model_calls_by_this_component'], 0)
        hashes = json.loads((session.output/'artifact_hashes.json').read_text())
        self.assertIn('base_tool_source.py', hashes)
        for name, digest in hashes.items(): self.assertEqual(audit.hash_file(session.output/name), digest)

    def test_rejects_bad_parameters_and_existing_outputs(self):
        for overrides in ({'cache_read_cost': 8}, {'cache_read_cost': True},
                          {'materialized_available': 1}, {'database_sha256': 'x'},
                          {'history_sql': 'SELECT 99'}, {'input_receipt': []},
                          {'cached_result': self.cache | {'complete': False}}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.session(**overrides)
        session = self.session()
        before = (session.output/'started.json').read_bytes()
        with self.assertRaises((ValueError, FileExistsError)): self.session(output=session.output)
        self.assertEqual((session.output/'started.json').read_bytes(), before)
        self.child.assert_not_called()

    def test_unavailable_read_never_executes_or_materializes_cache(self):
        session = self.session(False, 20)
        response = self.call(session, 'read_cache')
        self.assertEqual(response['charged_units'], 0)
        self.assertNotIn('rows', response)
        self.child.assert_not_called()
        self.call(session, 'run_sql')
        self.assertEqual(self.call(session, 'read_cache')['status'], 'CACHE_UNAVAILABLE')
        self.child.assert_called_once()

    def test_invalid_request_consumes_free_opportunity_without_changing_prices(self):
        session = self.session(cost=20)
        for name, arguments in [('read_cache', {'cache_id':'v000','cost':0}),
                                ('run_sql', {'sql':self.sql,'database':'other'})]:
            response = session.call(name, arguments)
            self.assertEqual(response['status'], 'INVALID_REQUEST')
            self.assertEqual(response['charged_units'], 0)
        self.assertEqual(self.call(session, 'read_cache')['charged_units'], 20)
        self.child.assert_not_called()

    def test_interrupted_execution_keeps_charge_pending_and_no_retry(self):
        session = self.session()
        def interrupted(*args):
            self.assertTrue((session.output/'attempt-001.pending.json').exists())
            self.assertEqual(session.balance, 50)
            raise KeyboardInterrupt('fixture')
        self.child.side_effect = interrupted
        with self.assertRaises(KeyboardInterrupt): self.call(session, 'run_sql')
        with self.assertRaises(RuntimeError): self.call(session, 'run_sql')
        self.child.assert_called_once()
        summary = session.close()
        self.assertEqual(summary['status'], 'UNRESOLVED_ATTEMPTS')
        self.assertEqual(summary['unresolved_attempts'], [1])
        self.assertEqual(summary['spent_declared_units'], 10)

    def test_partial_result_is_private_and_failure_still_charged(self):
        session = self.session()
        self.child.side_effect = lambda *a: dict(status='OUTPUT_LIMIT', complete=False, rows=['PRIVATE'])
        response = self.call(session, 'run_sql')
        self.assertFalse(response['complete'])
        self.assertNotIn('rows', response)
        self.assertEqual(response['charged_units'], 10)
        record = json.loads((session.output/'attempt-001.completed.json').read_text())
        self.assertEqual(record['private']['backend_result']['rows'], ['PRIVATE'])
        self.assertEqual(session.close()['status'], 'TOOL_SESSION_CLOSED')

    def test_pending_write_failure_blocks_dispatch_without_charge(self):
        session = self.session()
        with patch.object(audit, 'save_new', side_effect=OSError('fixture')), self.assertRaises(OSError):
            self.call(session, 'run_sql')
        self.assertEqual(session.balance, 60)
        self.assertEqual(session.attempts, 0)
        self.child.assert_not_called()
        with self.assertRaises(RuntimeError): self.call(session, 'run_sql')

    def test_completion_write_failure_leaves_unresolved_attempt(self):
        session = self.session(cost=20)
        original = audit.save_new
        def save(path, value):
            if path.name.endswith('.completed.json'): raise OSError('fixture')
            return original(path, value)
        with patch.object(audit, 'save_new', side_effect=save), self.assertRaises(OSError):
            self.call(session, 'read_cache')
        self.assertEqual(session.balance, 40)
        self.assertEqual(session.close()['unresolved_attempts'], [1])

    def test_database_tampering_invalidates_before_data_exposure(self):
        session = self.session(cost=20)
        with self.db.open('ab') as handle: handle.write(b'fixture corruption')
        response = self.call(session, 'read_cache')
        self.assertEqual(response['status'], 'ENVIRONMENT_INVALID')
        self.assertEqual(response['charged_units'], 20)
        self.assertNotIn('rows', response)
        self.assertEqual(session.close()['status'], 'ENVIRONMENT_INVALID')

    def test_cache_is_copied_and_sql_does_not_replace_it(self):
        session = self.session(cost=20)
        self.cache['rows'][0][0][1] = '999'
        response = self.call(session, 'read_cache')
        self.assertEqual(response['rows'][0][0][1], '1')
        response['rows'][0][0][1] = '888'
        self.assertEqual(self.call(session, 'read_cache')['rows'][0][0][1], '1')
        self.child.assert_not_called()

    def test_real_readonly_worker_blocks_mutation_and_allows_third_sql(self):
        session = self.session()
        with patch.object(audit, 'run_child', side_effect=REAL_CHILD):
            denied = session.call('run_sql', {'sql':'DELETE FROM example'})
            cached = self.call(session, 'read_cache')
            selected = self.call(session, 'run_sql')
        self.assertEqual(denied['status'], 'DENIED')
        self.assertEqual(denied['charged_units'], 10)
        self.assertEqual(selected['rows'], cached['rows'])
        self.assertEqual(audit.hash_file(self.db), self.db_hash)
        self.assertEqual(session.close()['spent_declared_units'], 21)


if __name__ == '__main__':
    unittest.main()
