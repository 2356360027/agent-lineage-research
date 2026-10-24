"""Offline-only tests for the three-text-file Arcwise-Plat acquisition."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import fetch_arcwise_fixed as fetch


QUESTION = {'question_id': '234', 'db_id': 'synthetic', 'question': 'Fixture only?',
            'evidence': None, 'SQL': 'SELECT 1', 'original_SQL': 'SELECT 0'}
QUESTIONS = json.dumps([QUESTION]).encode('utf-8')


class Response(io.BytesIO):
    def __init__(self, body, headers=None, status=200):
        super().__init__(body)
        self.headers = {'Content-Length': str(len(body)), **(headers or {})}
        self.status = status
        self.read_sizes = []

    def read(self, size=-1):
        if size < 0:
            raise AssertionError('Unbounded network read')
        self.read_sizes.append(size)
        return super().read(size)


class Opener:
    def __init__(self, bodies=None, mutate=None, fail_at=None):
        self.bodies = {'README.md': b'# Synthetic fixture\n', 'LICENSE': b'Synthetic license text\n',
                       'arcwise_plat_full_with_diff.json': QUESTIONS}
        self.bodies.update(bodies or {})
        self.mutate, self.fail_at = mutate, fail_at
        self.requests, self.responses = [], []

    def open(self, request, timeout=None):
        self.requests.append(request)
        urls = {url: name for name, url in fetch.SOURCES.items()}
        if request.full_url not in urls:
            raise AssertionError('Request outside fixed source list')
        name = urls[request.full_url]
        if name == self.fail_at:
            raise OSError('https://user:secret@example.invalid:8080 transport detail')
        response = Response(self.bodies[name])
        if self.mutate:
            self.mutate(name, response)
        self.responses.append(response)
        return response


class Tests(unittest.TestCase):
    def setUp(self):
        self.network = mock.patch('urllib.request.OpenerDirector.open',
                                  side_effect=AssertionError('No live network in tests'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix='arcwise-text-test-')
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'new-dataset'

    def run_fixture(self, opener=None, **kwargs):
        opener = opener or Opener()
        with mock.patch.object(fetch, 'make_opener', return_value=opener):
            result = fetch.fetch(self.directory, **kwargs)
        return result, opener

    def test_revision_and_exact_source_paths(self):
        self.assertRegex(fetch.REVISION, r'^[0-9a-f]{40}$')
        self.assertEqual(len(fetch.SOURCES), 3)
        self.assertTrue(all('/' + fetch.REVISION + '/' in url for url in fetch.SOURCES.values()))
        self.assertTrue(all(url.startswith('https://raw.githubusercontent.com/uiuc-kang-lab/')
                            for url in fetch.SOURCES.values()))

    def test_cli_default_is_plan_without_output_or_network(self):
        with mock.patch.object(fetch, 'make_opener', side_effect=AssertionError('No opener in plan')), \
             mock.patch('sys.stdout', new_callable=io.StringIO) as stdout:
            self.assertEqual(fetch.main(['--directory', str(self.directory)]), 0)
        self.assertEqual(json.loads(stdout.getvalue())['stage'], 'plan_no_download')
        self.assertFalse(self.directory.exists())

    def test_success_preserves_exact_bytes_and_receipts_without_approval(self):
        result, opener = self.run_fixture()
        self.assertEqual(result['stage'], 'text_complete')
        self.assertEqual(result['revision'], fetch.REVISION)
        for name, raw in opener.bodies.items():
            self.assertEqual((self.directory / name).read_bytes(), raw)
            receipt = result['files'][name]
            self.assertEqual(receipt['url'], fetch.SOURCES[name])
            self.assertEqual(receipt['bytes'], len(raw))
            self.assertEqual(receipt['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertIn('retrieved_at_utc', receipt)
        self.assertEqual(result['transfer']['fetched_body_bytes'], sum(map(len, opener.bodies.values())))
        self.assertEqual(result['structural_validation']['record_count'], 1)
        self.assertEqual(result['structural_validation']['human_confirmation_count'], 0)
        self.assertFalse(result['structural_validation']['database_compatibility_verified'])
        self.assertFalse(result['sql_executed'])
        self.assertFalse((self.directory / 'failure.json').exists())
        self.assertTrue(all(response.closed for response in opener.responses))
        self.assertEqual(json.loads((self.directory / 'source_manifest.json').read_text('utf-8')), result)

    def test_existing_directory_refused_before_network_and_preserved(self):
        self.directory.mkdir()
        sentinel = self.directory / 'previous.txt'
        sentinel.write_bytes(b'preserve')
        with mock.patch.object(fetch, 'make_opener') as factory, self.assertRaises(fetch.FetchError):
            fetch.fetch(self.directory)
        factory.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b'preserve')

    def test_failure_preserves_partial_state_without_error_secrets(self):
        opener = Opener(fail_at='LICENSE')
        with self.assertRaises(OSError):
            self.run_fixture(opener)
        self.assertTrue((self.directory / 'README.md').exists())
        self.assertFalse((self.directory / 'source_manifest.json').exists())
        failure_raw = (self.directory / 'failure.json').read_text('utf-8')
        failure = json.loads(failure_raw)
        self.assertEqual(failure['error_type'], 'OSError')
        self.assertEqual(failure['failed_step'], 'LICENSE')
        self.assertFalse(failure['resumable'])
        self.assertNotIn('secret', failure_raw)
        self.assertNotIn('example.invalid', failure_raw)
        with self.assertRaises(fetch.FetchError):
            self.run_fixture(opener)

    def test_invalid_json_is_retained_but_never_published_complete(self):
        opener = Opener({'arcwise_plat_full_with_diff.json': b'[{"question_id":"x","question_id":"y"}]'})
        with self.assertRaises(fetch.FetchError):
            self.run_fixture(opener)
        self.assertTrue((self.directory / 'arcwise_plat_full_with_diff.json').exists())
        self.assertFalse((self.directory / 'source_manifest.json').exists())
        self.assertTrue((self.directory / 'failure.json').exists())

    def test_structural_validation_rejects_invalid_records(self):
        cases = [[], {}, [QUESTION, QUESTION], [{**QUESTION, 'question_id': True}],
                 [{**QUESTION, 'db_id': '../escape'}], [{**QUESTION, 'question': ''}],
                 [{**QUESTION, 'SQL': None}], [{**QUESTION, 'evidence': []}],
                 [{'question_id': '1'}]]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(fetch.FetchError):
                fetch.validate_questions(json.dumps(data).encode())
        with self.assertRaises(fetch.FetchError):
            fetch.validate_questions(b'[{"v":NaN}]')

    def test_file_limit_rejects_advertised_oversize_without_reading(self):
        def mutate(name, response):
            response.headers['Content-Length'] = str(fetch.MAX_FILE_BYTES + 1)
        opener = Opener(mutate=mutate)
        with self.assertRaises(fetch.FetchError):
            self.run_fixture(opener)
        self.assertEqual(opener.responses[0].read_sizes, [])
        self.assertFalse((self.directory / 'source_manifest.json').exists())

    def test_total_budget_is_cumulative_not_per_file(self):
        opener = Opener({'README.md': b'x' * 20, 'LICENSE': b'y' * 20})
        with mock.patch.object(fetch, 'MAX_TOTAL_BYTES', 30), self.assertRaises(fetch.FetchError):
            self.run_fixture(opener)
        failure = json.loads((self.directory / 'failure.json').read_text('utf-8'))
        self.assertEqual(failure['transfer']['fetched_body_bytes'], 20)
        self.assertEqual(failure['transfer']['limit_bytes'], 30)
        self.assertEqual(opener.responses[1].read_sizes, [])

    def test_truncated_or_compressed_response_cannot_succeed(self):
        for headers in ({'Content-Length': '999'}, {'Content-Encoding': 'gzip'}):
            self.directory = Path(self.tmp.name) / ('variant-' + str(len(list(Path(self.tmp.name).iterdir()))))
            opener = Opener(mutate=lambda name, response: response.headers.update(headers))
            with self.subTest(headers=headers), self.assertRaises(fetch.FetchError):
                self.run_fixture(opener)
            self.assertFalse((self.directory / 'source_manifest.json').exists())

    def test_timeout_is_finite_positive_and_proxy_credentials_rejected(self):
        for timeout in (0, -1, float('inf'), float('nan'), True):
            with self.subTest(timeout=timeout), self.assertRaises(fetch.FetchError):
                fetch.fetch(self.directory, timeout=timeout)
        with self.assertRaises(fetch.FetchError):
            fetch.fetch(self.directory, proxy='http://user:secret@localhost:8080')
        self.assertFalse(self.directory.exists())

    def test_cli_execute_uses_explicit_proxy_but_never_logs_it(self):
        with mock.patch.object(fetch, 'make_opener', return_value=Opener()) as factory, \
             mock.patch('sys.stdout', new_callable=io.StringIO) as stdout:
            result = fetch.main(['--directory', str(self.directory), '--execute',
                                 '--proxy', 'http://localhost:8123'])
        self.assertEqual(result, 0)
        factory.assert_called_once_with('http://localhost:8123')
        self.assertNotIn('8123', stdout.getvalue())
        self.assertNotIn('8123', (self.directory / 'started.json').read_text('utf-8'))


if __name__ == '__main__':
    unittest.main()
