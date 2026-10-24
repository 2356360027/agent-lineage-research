"""Offline adversarial tests for the fixed BIRD downloader; no live data used."""
from contextlib import contextmanager
from email.message import Message
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import struct
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile
import zlib

from scripts import fetch_bird_fixed as fetch


SQLITE = b'SQLite format 3\x00' + b'synthetic database bytes\x00' * 100
QUESTIONS = b'[{"db_id":"alpha","question":"Synthetic only"},{"db_id":"beta"}]\n'
README = b'# Synthetic fixture\n'


def zip_bytes(entries, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(output, 'w', compression=compression) as archive:
            for name, payload in entries:
                archive.writestr(name, payload)
    return output.getvalue()


def archive_identity(raw):
    return dict(url=fetch.ARCHIVE_URL, size=len(raw), etag=fetch.ARCHIVE_ETAG,
                last_modified=None, accept_ranges='bytes',
                full_archive_sha256=None, etag_is_content_hash=False)


class FakeResponse(io.BytesIO):
    def __init__(self, body=b'', status=200, headers=None, step=None):
        super().__init__(body)
        self.status, self.headers, self.step = status, dict(headers or {}), step
        self.read_sizes, self.delivered = [], 0

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size < 0:
            raise AssertionError('Remote bodies must never be read without a bound')
        result = super().read(min(size, self.step) if self.step else size)
        self.delivered += len(result)
        return result


class FakeOpener:
    """Serve only explicitly registered bytes, with request/header recording."""
    def __init__(self, raw, mutate=None, step=None):
        self.raw, self.mutate, self.step = raw, mutate, step
        self.requests, self.responses = [], []

    def open(self, request, timeout=None):
        headers = {name.lower(): value for name, value in request.header_items()}
        self.requests.append((request, headers, timeout))
        if headers.get('accept-encoding') != 'identity':
            raise AssertionError('Identity content encoding is required')
        if request.full_url == fetch.ARCHIVE_URL:
            if headers.get('if-match') != fetch.ARCHIVE_ETAG:
                raise AssertionError('Every archive request must pin If-Match')
            response_headers = {'ETag': fetch.ARCHIVE_ETAG,
                                'Accept-Ranges': 'bytes'}
            if request.get_method() == 'HEAD':
                body, status = b'', 200
                response_headers['Content-Length'] = str(len(self.raw))
            else:
                match = re.fullmatch(r'bytes=(\d+)-(\d+)', headers.get('range', ''))
                if not match:
                    raise AssertionError('Full archive GET is forbidden')
                start, end = map(int, match.groups())
                body, status = self.raw[start:end + 1], 206
                response_headers.update({
                    'Content-Length': str(end - start + 1),
                    'Content-Range': f'bytes {start}-{end}/{len(self.raw)}'})
        elif request.full_url in (fetch.QUESTION_URL, fetch.README_URL):
            body = QUESTIONS if request.full_url == fetch.QUESTION_URL else README
            status, response_headers = 200, {'Content-Length': str(len(body))}
        else:
            raise AssertionError(f'Unregistered URL: {request.full_url}')
        response = FakeResponse(body, status, response_headers, self.step)
        if self.mutate:
            self.mutate(request, response)
        self.responses.append(response)
        return response


def forged_zip(payload, *, declared_size=None, crc=None):
    """Alter matching local/central fields, preserving valid ZIP structure."""
    raw = bytearray(zip_bytes([('root/alpha/alpha.sqlite', payload)]))
    central = raw.index(b'PK\x01\x02')
    if declared_size is not None:
        struct.pack_into('<I', raw, 22, declared_size)
        struct.pack_into('<I', raw, central + 24, declared_size)
    if crc is not None:
        struct.pack_into('<I', raw, 14, crc)
        struct.pack_into('<I', raw, central + 16, crc)
    return bytes(raw)


class BirdFetchTests(unittest.TestCase):
    def setUp(self):
        # Any accidental use of the real downloader client fails before I/O.
        self.network = mock.patch('urllib.request.OpenerDirector.open',
                                  side_effect=AssertionError('Live network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.temporary = tempfile.TemporaryDirectory(prefix='bird-fetch-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def reader(self, raw=b'0123456789abcdef', *, mutate=None, budget=None, step=None):
        opener = FakeOpener(raw, mutate=mutate, step=step)
        reader = fetch.HTTPRangeReader(opener, archive_identity(raw),
                                       budget if budget is not None else fetch.Budget())
        self.addCleanup(reader.close)
        return reader, opener

    @contextmanager
    def staged_fixture(self):
        raw = zip_bytes([
            ('minidev/mini_dev_sqlite.json', QUESTIONS),
            ('minidev/databases/alpha/alpha.sqlite', SQLITE),
            ('minidev/databases/beta/beta.sqlite', SQLITE + b'beta'),
        ])
        opener = FakeOpener(raw, step=37)
        original_head = fetch.archive_head

        def small_head(client, **kwargs):
            kwargs['expected_size'] = len(raw)
            return original_head(client, **kwargs)

        with mock.patch.object(fetch, 'ARCHIVE_SIZE', len(raw)), \
             mock.patch.object(fetch, 'make_opener', return_value=opener), \
             mock.patch.object(fetch, 'archive_head', side_effect=small_head):
            yield self.root / 'dataset', opener

    def test_range_reader_seek_short_reads_and_receipts(self):
        budget = fetch.Budget(limit=32)
        reader, opener = self.reader(budget=budget, step=2)
        self.assertEqual(reader.read(5), b'01234')
        self.assertEqual(reader.seek(-3, 2), 13)
        target = bytearray(5)
        self.assertEqual(reader.readinto(target), 3)
        self.assertEqual(target[:3], b'def')
        self.assertEqual(reader.read(1), b'')
        self.assertEqual(budget.used, 8)
        self.assertEqual(sum(r['bytes'] for r in reader.receipts), 8)
        self.assertTrue(all(r['complete'] for r in reader.receipts))
        self.assertTrue(all(response.closed for response in opener.responses))
        self.assertEqual(reader.receipts[0]['sha256'], hashlib.sha256(b'01234').hexdigest())

    def test_range_reader_splits_requests_and_counts_repeated_bytes(self):
        reader, opener = self.reader(budget=fetch.Budget(limit=12))
        with mock.patch.object(fetch, 'RANGE_CHUNK_BYTES', 3):
            self.assertEqual(reader.read(8), b'01234567')
        self.assertEqual(len(opener.requests), 3)
        reader.seek(0)
        self.assertEqual(reader.read(4), b'0123')
        self.assertEqual(reader.budget.used, 12)
        reader.seek(0)
        with self.assertRaises(fetch.FetchError):
            reader.read(1)
        self.assertEqual(len(opener.requests), 4)

    def test_range_rejects_status_and_header_mismatches_before_body_read(self):
        cases = [
            ('status', 200), ('Content-Range', 'bytes 1-4/16'),
            ('Content-Range', 'bytes 0-3/999'), ('Content-Length', '5'),
            ('ETag', '"changed"'), ('ETag', None),
            ('ETag', 'W/' + fetch.ARCHIVE_ETAG), ('Content-Encoding', 'gzip'),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                def mutate(_request, response):
                    if key == 'status':
                        response.status = value
                    elif value is None:
                        response.headers.pop(key, None)
                    else:
                        response.headers[key] = value
                reader, opener = self.reader(mutate=mutate)
                with self.assertRaises(fetch.FetchError):
                    reader.read(4)
                self.assertEqual(reader.budget.used, 0)
                self.assertEqual(opener.responses[0].read_sizes, [])
                self.assertTrue(opener.responses[0].closed)

    def test_truncated_range_consumes_budget_and_records_incomplete_receipt(self):
        def mutate(_request, response):
            response.truncate(2)
        reader, _ = self.reader(mutate=mutate)
        with self.assertRaises(fetch.FetchError):
            reader.read(4)
        self.assertEqual(reader.budget.used, 2)
        self.assertEqual(reader.tell(), 0)
        self.assertFalse(reader.receipts[0]['complete'])
        self.assertEqual(reader.receipts[0]['bytes'], 2)

    def test_read_over_budget_fails_without_request(self):
        reader, opener = self.reader(budget=fetch.Budget(limit=3))
        with self.assertRaises(fetch.FetchError):
            reader.read(4)
        self.assertEqual(opener.requests, [])

    def test_budget_validates_bounds_and_preserves_used_on_rejection(self):
        for kwargs in ({'used': -1}, {'used': fetch.MAX_FETCH_BYTES + 1},
                       {'limit': fetch.MAX_FETCH_BYTES + 1}, {'limit': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(fetch.FetchError):
                fetch.Budget(**kwargs)
        budget = fetch.Budget(limit=10, used=7)
        with self.assertRaises(fetch.FetchError):
            budget.consume(4)
        self.assertEqual(budget.used, 7)
        budget.consume(3)
        self.assertEqual(budget.remaining, 0)

    def test_archive_head_pins_entity_without_reading_body(self):
        opener = FakeOpener(b'abcd')
        result = fetch.archive_head(opener, expected_size=4)
        self.assertEqual(result['etag'], fetch.ARCHIVE_ETAG)
        self.assertIsNone(result['full_archive_sha256'])
        self.assertFalse(result['etag_is_content_hash'])
        self.assertEqual(opener.responses[0].read_sizes, [])

    def test_archive_head_rejects_changed_size_or_etag(self):
        for headers in ({'Content-Length': '5'}, {'ETag': '"wrong"'}):
            with self.subTest(headers=headers):
                opener = FakeOpener(b'abcd', mutate=lambda req, res: res.headers.update(headers))
                with self.assertRaises(fetch.FetchError):
                    fetch.archive_head(opener, expected_size=4)

    def test_redirect_closes_body_without_reading_and_preserves_match(self):
        handler = fetch.NoDrainRedirect()
        parent = mock.Mock()
        handler.add_parent(parent)
        request = fetch.request(fetch.ARCHIVE_URL, {'If-Match': fetch.ARCHIVE_ETAG})
        request.timeout = 7
        body = FakeResponse(b'body must never be drained')
        headers = Message()
        headers['Location'] = 'https://example.invalid/pinned'
        handler.http_error_302(request, body, 302, 'redirect',
                               headers)
        self.assertTrue(body.closed)
        self.assertEqual(body.read_sizes, [])
        redirected = parent.open.call_args.args[0]
        self.assertEqual(redirected.get_header('If-match'), fetch.ARCHIVE_ETAG)

    def test_redirect_rejects_downgrade_or_credentials_without_reading(self):
        for target in ('http://example.invalid/a', 'https://u:p@example.invalid/a', 'file:///tmp/a'):
            with self.subTest(target=target):
                body = FakeResponse(b'untouched')
                with self.assertRaises(fetch.FetchError):
                    fetch.NoDrainRedirect().http_error_302(
                        fetch.request(fetch.ARCHIVE_URL), body, 302, 'redirect', {'Location': target})
                self.assertTrue(body.closed)
                self.assertEqual(body.read_sizes, [])

    def test_proxy_rejects_credentials_and_does_not_inherit_environment(self):
        for proxy in ('http://user:pass@localhost:8888', 'socks5://localhost:8888',
                      'http://localhost:8888/path', 'http://localhost:8888?secret=1'):
            with self.subTest(proxy=proxy), self.assertRaises(fetch.FetchError):
                fetch.make_opener(proxy)
        with mock.patch('urllib.request.build_opener') as build:
            fetch.make_opener()
        self.assertEqual(build.call_args.args[0].proxies, {})

    def test_identifiers_reject_unsafe_and_windows_reserved_names(self):
        for name in ('', '../alpha', 'a/b', 'a\\b', 'a:b', '.', '..', 'alpha ',
                     'CON', 'nul', 'COM1', 'LPT9', 1, None):
            with self.subTest(name=name), self.assertRaises(fetch.FetchError):
                fetch.safe_identifier(name)
        self.assertEqual(fetch.safe_identifier('alpha-12_beta'), 'alpha-12_beta')

    def test_member_names_reject_traversal_nul_and_windows_aliases(self):
        for name in ('../a', '/a', 'C:/a', 'a\\..\\b', 'a//b', 'a/./b',
                     'a./b', 'a /b', 'a:stream', 'CON.txt', 'CON .txt',
                     'COM¹.txt', 'a\x00b', 'a\x01b'):
            with self.subTest(name=name), self.assertRaises(fetch.FetchError):
                fetch.member_parts(zipfile.ZipInfo(name))

    def test_member_rejects_symlink_special_file_and_encryption(self):
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK):
            info = zipfile.ZipInfo('safe/name')
            info.external_attr = (kind | 0o600) << 16
            with self.subTest(kind=kind), self.assertRaises(fetch.FetchError):
                fetch.member_parts(info)
        info = zipfile.ZipInfo('safe/name')
        info.flag_bits |= 1
        with self.assertRaises(fetch.FetchError):
            fetch.member_parts(info)

    def test_inventory_rejects_duplicate_case_and_file_parent_collisions(self):
        for names in (('a/x', 'a/x'), ('A/x', 'a/y'), ('a/x', 'A/X'), ('a', 'a/x')):
            with self.subTest(names=names):
                raw = zip_bytes([(name, b'payload') for name in names])
                with zipfile.ZipFile(io.BytesIO(raw)) as archive, self.assertRaises(fetch.FetchError):
                    fetch.inventory(archive, archive_identity(raw))

    def test_selected_members_require_unique_present_bounded_entries(self):
        raw = zip_bytes([('db/alpha/alpha.sqlite', SQLITE)])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for selection in ([], ['missing'], ['alpha', 'ALPHA']):
                with self.subTest(selection=selection), self.assertRaises(fetch.FetchError):
                    fetch.selected_members(archive, selection)
            archive.infolist()[0].file_size = fetch.MAX_ENTRY_BYTES + 1
            with self.assertRaises(fetch.FetchError):
                fetch.selected_members(archive, ['alpha'])

    def test_range_zip_inventory_and_sqlite_extraction(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(compression=compression):
                raw = zip_bytes([('db/alpha/alpha.sqlite', SQLITE)], compression)
                reader, opener = self.reader(raw, step=31)
                with zipfile.ZipFile(reader) as archive:
                    listing = fetch.inventory(archive, archive_identity(raw))
                    db_id, info = fetch.selected_members(archive, ['alpha'])[0]
                    destination = self.root / str(compression) / db_id / (db_id + '.sqlite')
                    receipt = fetch.extract_database(archive, info, db_id, destination)
                self.assertEqual(destination.read_bytes(), SQLITE)
                self.assertEqual(receipt['sha256'], hashlib.sha256(SQLITE).hexdigest())
                self.assertFalse(receipt['sql_executed'])
                self.assertEqual(len(listing['entries']), 1)
                self.assertFalse(destination.with_name(destination.name + '.partial').exists())
                self.assertEqual(reader.budget.used, sum(r.delivered for r in opener.responses))

    def test_existing_destination_and_partial_are_preserved(self):
        raw = zip_bytes([('alpha/alpha.sqlite', SQLITE)])
        for existing_suffix in ('', '.partial'):
            with self.subTest(existing_suffix=existing_suffix):
                destination = self.root / ('output' + (existing_suffix or '.full') + '.sqlite')
                existing = destination.with_name(destination.name + existing_suffix)
                existing.write_bytes(b'owned by someone else')
                with zipfile.ZipFile(io.BytesIO(raw)) as archive, self.assertRaises(fetch.FetchError):
                    fetch.stream_member(archive, archive.infolist()[0], destination, fetch.MAX_ENTRY_BYTES)
                self.assertEqual(existing.read_bytes(), b'owned by someone else')

    def test_crc_and_forged_size_fail_without_publishing(self):
        variants = [
            forged_zip(SQLITE, crc=zlib.crc32(SQLITE) ^ 1),
            forged_zip(SQLITE, declared_size=16, crc=zlib.crc32(SQLITE[:16])),
            forged_zip(SQLITE, declared_size=len(SQLITE) + 1),
        ]
        for index, raw in enumerate(variants):
            with self.subTest(index=index):
                destination = self.root / f'bad-{index}.sqlite'
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    with self.assertRaises((fetch.FetchError, zipfile.BadZipFile)):
                        fetch.stream_member(archive, archive.infolist()[0], destination, fetch.MAX_ENTRY_BYTES)
                self.assertFalse(destination.exists())

    def test_invalid_sqlite_magic_is_not_published(self):
        raw = zip_bytes([('alpha/alpha.sqlite', b'not a database')])
        destination = self.root / 'bad.sqlite'
        with zipfile.ZipFile(io.BytesIO(raw)) as archive, self.assertRaises(fetch.FetchError):
            fetch.stream_member(archive, archive.infolist()[0], destination, fetch.MAX_ENTRY_BYTES)
        self.assertFalse(destination.exists())

    def test_uncompressed_entry_limit_rejects_before_writing(self):
        raw = zip_bytes([('large.json', b'x' * 33)])
        destination = self.root / 'large.json'
        with zipfile.ZipFile(io.BytesIO(raw)) as archive, self.assertRaises(fetch.FetchError):
            fetch.stream_member(archive, archive.infolist()[0], destination, 32)
        self.assertFalse(destination.exists())
        self.assertFalse(destination.with_name('large.json.partial').exists())

    def test_symlink_or_windows_reparse_ancestor_is_rejected(self):
        target = self.root / 'linked' / 'out.sqlite'
        real_lstat = Path.lstat

        def synthetic_lstat(path):
            if path == target.parent:
                return type('Metadata', (), {'st_mode': stat.S_IFDIR,
                                              'st_file_attributes': 0x400})()
            return real_lstat(path)

        with mock.patch.object(Path, 'lstat', synthetic_lstat), self.assertRaises(fetch.FetchError):
            fetch.absent(target)

    def test_question_formats_and_strict_duplicate_keys(self):
        cases = [(QUESTIONS, 'json_array'),
                 (b'{"db_id":"alpha"}\n{"db_id":"beta"}\n', 'jsonl'),
                 (b'\xef\xbb\xbf{"db_id":"alpha"}', 'json_single_object')]
        for raw, serialization in cases:
            with self.subTest(serialization=serialization):
                records, actual = fetch.question_records(raw)
                self.assertEqual(actual, serialization)
                self.assertEqual(records[0]['db_id'], 'alpha')
        with self.assertRaises(fetch.FetchError):
            fetch.question_records(b'{"db_id":"alpha","db_id":"beta"}')

    def test_fetch_small_obeys_file_limit_and_preserves_existing_file(self):
        destination = self.root / 'small.json'
        opener = FakeOpener(b'')
        with self.assertRaises(fetch.FetchError):
            fetch.fetch_small(opener, fetch.QUESTION_URL, destination, fetch.Budget(), 1, 30)
        self.assertFalse(destination.exists())
        destination.write_bytes(b'existing')
        request_count = len(opener.requests)
        with self.assertRaises(fetch.FetchError):
            fetch.fetch_small(opener, fetch.QUESTION_URL, destination, fetch.Budget(), 1024, 30)
        self.assertEqual(destination.read_bytes(), b'existing')
        self.assertEqual(len(opener.requests), request_count)

    def test_two_stages_preserve_sources_select_only_requested_db_and_reconcile_budget(self):
        with self.staged_fixture() as (directory, opener):
            fetch.metadata_stage(directory)
            source = fetch.validate_metadata(directory)
            self.assertEqual((directory / 'questions.jsonl').read_bytes(), QUESTIONS)
            self.assertEqual((directory / 'README.md').read_bytes(), README)
            self.assertFalse((directory / 'databases').exists())
            self.assertEqual(source['compatibility_status'], 'compatibility_under_audit')
            legacy_names = list(source['legacy_question_files'])
            self.assertEqual(len(legacy_names), 1)
            self.assertEqual((directory / legacy_names[0]).read_bytes(), QUESTIONS)
            initial_used = source['transfer']['fetched_body_bytes']
            self.assertEqual(initial_used, len(QUESTIONS) + len(README) +
                             sum(r['bytes'] for r in source['range_receipts']))
            original_manifest = (directory / 'source_manifest.json').read_bytes()
            fetch.write_json_exclusive(directory / 'selected_db_ids.json', ['alpha'])
            fetch.databases_stage(directory)
            result = fetch.read_json(directory / 'dataset_manifest.json')
            self.assertEqual(result['selected_db_ids'], ['alpha'])
            self.assertEqual((directory / 'databases/alpha/alpha.sqlite').read_bytes(), SQLITE)
            self.assertFalse((directory / 'databases/beta').exists())
            self.assertEqual((directory / 'source_manifest.json').read_bytes(), original_manifest)
            self.assertGreater(result['transfer']['fetched_body_bytes'], initial_used)
            self.assertEqual(result['transfer']['fetched_body_bytes'],
                             sum(response.delivered for response in opener.responses))
            self.assertEqual(result['compatibility_status'], 'compatibility_under_audit')
            self.assertIsNone(result['full_archive_sha256'])

    def test_both_stages_refuse_overwrite_before_network(self):
        with self.staged_fixture() as (directory, opener):
            fetch.metadata_stage(directory)
            count = len(opener.requests)
            with self.assertRaises(fetch.FetchError):
                fetch.metadata_stage(directory)
            self.assertEqual(len(opener.requests), count)
            fetch.write_json_exclusive(directory / 'selected_db_ids.json', ['alpha'])
            occupied = directory / 'database_ranges.jsonl'
            occupied.write_bytes(b'previous run')
            with self.assertRaises(fetch.FetchError):
                fetch.databases_stage(directory)
            self.assertEqual(occupied.read_bytes(), b'previous run')
            self.assertEqual(len(opener.requests), count)

    def test_metadata_hash_tampering_is_rejected(self):
        with self.staged_fixture() as (directory, _):
            fetch.metadata_stage(directory)
            (directory / 'questions.jsonl').write_bytes(QUESTIONS + b' ')
            with self.assertRaises(fetch.FetchError):
                fetch.validate_metadata(directory)

    def test_cross_stage_budget_cannot_be_reset_in_manifest(self):
        with self.staged_fixture() as (directory, _):
            fetch.metadata_stage(directory)
            path = directory / 'source_manifest.json'
            source = fetch.read_json(path)
            source['transfer']['fetched_body_bytes'] = 0
            source['transfer']['remaining_bytes'] = fetch.MAX_FETCH_BYTES
            path.write_text(json.dumps(source), encoding='utf-8')
            with self.assertRaises(fetch.FetchError):
                fetch.validate_metadata(directory)


if __name__ == '__main__':
    unittest.main()
