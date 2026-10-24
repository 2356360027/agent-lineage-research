"""Two-stage, stdlib-only BIRD mini SQLite acquisition; never executes SQL.

metadata: save original HF bytes as questions.jsonl and README.md, then inspect
the official ZIP central directory using bounded HTTP ranges. No databases are
extracted; matching small legacy question JSONs are preserved for comparison.
source_manifest.json is immutable and records pinned source URLs,
HF revision, files{name:{bytes,sha256}}, archive{etag,size,last_modified,...},
question serialization/count, legacy_question_files receipts, range receipts,
compatibility_under_audit status, and cumulative transfer budget.

databases --directory DIR: verify metadata hashes and selected_db_ids.json,
revalidate the exact archive entity, then extract only selected SQLite members.
database_ranges.jsonl / database_receipts.jsonl retain incremental receipts;
dataset_manifest.json is written only on complete success. No full ZIP SHA-256
is claimed: its ETag is an opaque HTTP entity validator, not a content hash.

Both stages refuse overwrite. Failed/partial output is left for inspection and
is NOT resumable. --proxy is an optional, nonpersisted HF-only proxy; archive
requests are direct. No credentials or proxy settings are saved in receipts.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import urllib.parse
import urllib.request
import zipfile
import zlib


HF_REPO = 'birdsql/bird_mini_dev'
HF_REVISION = 'f65faf4ae3b638c1fa6df1d3370c8d92c8366301'
HF_BASE = f'https://huggingface.co/datasets/{HF_REPO}/resolve/{HF_REVISION}'
QUESTION_URL = HF_BASE + '/data/mini_dev_sqlite-00000-of-00001.json'
README_URL = HF_BASE + '/README.md'
ARCHIVE_URL = 'https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip'
ARCHIVE_SIZE = 800943648
ARCHIVE_ETAG = '"7BEB6DAB11E65F0FDE563E644A1EA319"'
MAX_FETCH_BYTES = 600 * 1024 * 1024
MAX_ENTRY_BYTES = 1024 * 1024 * 1024
RANGE_CHUNK_BYTES = 8 * 1024 * 1024
SCHEMA = 'bird-mini-fixed-fetch-v1'
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / 'runtime/runs/datasets/bird-mini-fixed-20261005-001'


class FetchError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise FetchError(message)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f'Duplicate JSON key: {key}')
            result[key] = value
        return result

    def constant(value):
        raise FetchError(f'Nonfinite JSON constant: {value}')

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def read_json(path):
    return strict_json(path.read_text(encoding='utf-8'))


def write_json_exclusive(path, value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    with path.open('xb') as stream:
        stream.write(raw)
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def file_receipt(path):
    digest, count = hashlib.sha256(), 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            count += len(chunk)
            digest.update(chunk)
    return dict(bytes=count, sha256=digest.hexdigest())


def no_link_ancestors(path):
    for part in (path, *path.parents):
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(metadata.st_mode) and
                not (getattr(metadata, 'st_file_attributes', 0) & 0x400),
                f'Symlink/reparse-point path is forbidden: {part}')


def absent(path):
    no_link_ancestors(path)
    require(not path.exists(), f'Refuse overwrite: {path}')


def safe_component(value):
    require(value not in ('', '.', '..') and not value.endswith(('.', ' ')), 'Unsafe path component')
    require(not any(ord(c) < 32 or c in ':<>"|?*\\/' for c in value), 'Unsafe path characters')
    device = value.split('.', 1)[0].rstrip(' ').casefold()
    reserved = {'con', 'prn', 'aux', 'nul', 'conin$', 'conout$'}
    reserved.update(p + n for p in ('com', 'lpt') for n in '123456789¹²³')
    require(device not in reserved, 'Reserved Windows path component')


def safe_identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value), 'Unsafe database identifier')
    safe_component(value)
    return value


def member_parts(info):
    # orig_filename detects NUL truncation performed by ZipInfo itself.
    require(info.orig_filename == info.filename, 'ZIP filename was truncated')
    name = info.filename[:-1] if info.is_dir() else info.filename
    parts = name.split('/')
    for part in parts:
        safe_component(part)
    kind = stat.S_IFMT(info.external_attr >> 16)
    require(kind in (0, stat.S_IFREG, stat.S_IFDIR), 'ZIP symlink or special file is forbidden')
    require(kind != stat.S_IFDIR or info.is_dir(), 'ZIP directory type/name mismatch')
    require(not (info.flag_bits & 1), 'Encrypted ZIP members are unsupported')
    return parts


class Budget:
    def __init__(self, limit=MAX_FETCH_BYTES, used=0):
        require(type(limit) is int and 0 < limit <= MAX_FETCH_BYTES and type(used) is int and 0 <= used <= limit,
                'Invalid transfer budget')
        self.limit, self.used = limit, used

    @property
    def remaining(self):
        return self.limit - self.used

    def consume(self, count):
        require(0 <= count <= self.remaining, 'Fetched-body budget exceeded')
        self.used += count

    def receipt(self):
        return dict(limit_bytes=self.limit, fetched_body_bytes=self.used,
                    remaining_bytes=self.remaining, excludes='HTTP headers and transport overhead')


class NoDrainRedirect(urllib.request.HTTPRedirectHandler):
    """urllib's default redirect handler drains bodies without a byte bound."""
    def http_error_302(self, req, fp, code, msg, headers):
        fp.close()
        location = headers.get('Location') or headers.get('URI')
        require(location is not None, 'Redirect lacks a location')
        target = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, location))
        require(target.scheme == 'https' and target.hostname and target.username is None and target.password is None,
                'Only credential-free HTTPS redirects are permitted')
        # Preserve urllib's loop limits/header handling, but give its unbounded
        # drain an empty local stream rather than the remote response.
        return super().http_error_302(req, io.BytesIO(b''), code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def make_opener(proxy=None):
    proxies = {}
    if proxy:
        parsed = urllib.parse.urlsplit(proxy)
        require(parsed.scheme in ('http', 'https') and parsed.hostname and parsed.username is None and
                parsed.password is None and not parsed.query and not parsed.fragment and parsed.path in ('', '/'),
                'Proxy must be an HTTP(S) endpoint without credentials or query parameters')
        proxies = {'http': proxy, 'https': proxy}
    # Do not silently inherit environment/system proxies.
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies), NoDrainRedirect())


def request(url, headers=None, method='GET'):
    return urllib.request.Request(url, headers={'Accept-Encoding': 'identity',
        'User-Agent': 'bird-mini-fixed-stdlib/1', **(headers or {})}, method=method)


def identity_encoding(headers):
    require(headers.get('Content-Encoding', 'identity').lower() == 'identity', 'Encoded HTTP response is forbidden')


def archive_head(opener, url=ARCHIVE_URL, expected_size=ARCHIVE_SIZE, expected_etag=ARCHIVE_ETAG, timeout=30):
    require(isinstance(expected_etag, str) and expected_etag.startswith('"') and expected_etag.endswith('"') and
            not expected_etag.startswith('W/'), 'A strong quoted archive ETag is required')
    with opener.open(request(url, {'If-Match': expected_etag}, method='HEAD'), timeout=timeout) as response:
        require(response.status == 200, 'Archive HEAD must return HTTP 200')
        identity_encoding(response.headers)
        require(response.headers.get('ETag') == expected_etag, 'Archive ETag changed')
        require(response.headers.get('Content-Length') == str(expected_size), 'Archive size changed')
        return dict(url=url, size=expected_size, etag=expected_etag,
                    last_modified=response.headers.get('Last-Modified'),
                    accept_ranges=response.headers.get('Accept-Ranges'),
                    full_archive_sha256=None, etag_is_content_hash=False)


class HTTPRangeReader(io.RawIOBase):
    """Seekable, bounded remote ZIP view; never accepts a full-response fallback."""
    def __init__(self, opener, archive, budget, timeout=30, receipt_callback=None):
        super().__init__()
        self.opener, self.archive, self.budget, self.timeout = opener, archive, budget, timeout
        self.position, self.receipts, self.receipt_callback = 0, [], receipt_callback

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        self._checkClosed()
        return self.position

    def seek(self, offset, whence=os.SEEK_SET):
        self._checkClosed()
        require(type(offset) is int and whence in (os.SEEK_SET, os.SEEK_CUR, os.SEEK_END), 'Invalid seek')
        base = 0 if whence == os.SEEK_SET else self.position if whence == os.SEEK_CUR else self.archive['size']
        require(base + offset >= 0, 'Negative seek position')
        self.position = base + offset
        return self.position

    def _range(self, start, count):
        require(0 < count <= self.budget.remaining, 'HTTP range would exceed fetched-body budget')
        end = start + count - 1
        req = request(self.archive['url'], {'Range': f'bytes={start}-{end}', 'If-Match': self.archive['etag']})
        with self.opener.open(req, timeout=self.timeout) as response:
            require(response.status == 206, 'Range request must return HTTP 206; full downloads are forbidden')
            identity_encoding(response.headers)
            require(response.headers.get('ETag') == self.archive['etag'], 'Range response ETag changed or missing')
            require(response.headers.get('Content-Range') == f'bytes {start}-{end}/{self.archive["size"]}',
                    'Range response Content-Range mismatch')
            require(response.headers.get('Content-Length') == str(count), 'Range response Content-Length mismatch')
            chunks, fetched = [], 0
            while fetched < count:
                chunk = response.read(min(1024 * 1024, count-fetched))
                if not chunk:
                    break
                self.budget.consume(len(chunk))
                fetched += len(chunk)
                chunks.append(chunk)
            receipt = dict(start=start, end=end, bytes=fetched, etag=self.archive['etag'],
                           sha256=hashlib.sha256(b''.join(chunks)).hexdigest(), complete=fetched == count)
            self.receipts.append(receipt)
            if self.receipt_callback:
                self.receipt_callback(receipt)
            require(fetched == count, 'Truncated range response')
            return b''.join(chunks)

    def read(self, size=-1):
        self._checkClosed()
        require(size is None or type(size) is int, 'Invalid read size')
        remaining = max(0, self.archive['size'] - self.position)
        count = remaining if size is None or size < 0 else min(size, remaining)
        require(count <= self.budget.remaining, 'Read would exceed fetched-body budget')
        output = []
        while count:
            length = min(count, RANGE_CHUNK_BYTES)
            output.append(self._range(self.position, length))
            self.position += length
            count -= length
        return b''.join(output)

    def readinto(self, buffer):
        raw = self.read(len(buffer))
        buffer[:len(raw)] = raw
        return len(raw)


def inventory(zf, archive):
    entries, seen, spellings = [], {}, {}
    for info in zf.infolist():
        parts = member_parts(info)
        key = '/'.join(parts).casefold()
        require(key not in seen, 'Duplicate or case-alias ZIP member')
        seen[key] = info.is_dir()
        for count in range(1, len(parts)+1):
            spelling = '/'.join(parts[:count])
            canonical = spelling.casefold()
            require(canonical not in spellings or spellings[canonical] == spelling, 'Case-alias ZIP directory')
            spellings[canonical] = spelling
        require(0 <= info.header_offset < archive['size'] and info.file_size >= 0 and info.compress_size >= 0,
                'Invalid ZIP entry bounds')
        entries.append(dict(filename=info.filename, directory=info.is_dir(), bytes=info.file_size,
            compressed_bytes=info.compress_size, crc32=f'{info.CRC:08x}', compression=info.compress_type,
            header_offset=info.header_offset, flags=info.flag_bits, external_attr=info.external_attr))
    for entry in entries:
        parts = entry['filename'].rstrip('/').split('/')
        for count in range(1, len(parts)):
            parent = '/'.join(parts[:count]).casefold()
            require(parent not in seen or seen[parent], 'ZIP file is also a parent directory')
    return dict(archive_url=archive['url'], archive_etag=archive['etag'], archive_size=archive['size'], entries=entries)


def selected_members(zf, db_ids):
    require(isinstance(db_ids, list) and bool(db_ids), 'selected_db_ids.json must contain a nonempty JSON list')
    for db_id in db_ids:
        safe_identifier(db_id)
    require(len({value.casefold() for value in db_ids}) == len(db_ids), 'Duplicate/case-alias selected database IDs')
    selected = []
    for db_id in db_ids:
        matches = [info for info in zf.infolist() if not info.is_dir() and
                   info.filename.split('/')[-2:] == [db_id, db_id + '.sqlite']]
        require(len(matches) == 1, f'Expected exactly one ZIP member for database: {db_id}')
        info = matches[0]
        member_parts(info)
        require(info.file_size <= MAX_ENTRY_BYTES, f'Selected database exceeds 1 GiB: {db_id}')
        selected.append((db_id, info))
    return selected


def question_records(raw):
    text = raw.decode('utf-8-sig')
    try:
        value = strict_json(text)
    except json.JSONDecodeError:
        value = [strict_json(line) for line in text.splitlines() if line.strip()]
        serialization = 'jsonl'
    else:
        serialization = 'json_array' if isinstance(value, list) else 'json_single_object'
        if isinstance(value, dict):
            value = [value]
    require(isinstance(value, list) and bool(value) and all(isinstance(row, dict) for row in value), 'Question source is not JSON/JSONL records')
    for row in value:
        safe_identifier(row.get('db_id'))
    return value, serialization


def fetch_small(opener, url, destination, budget, limit, timeout):
    absent(destination)
    count, digest = 0, hashlib.sha256()
    with opener.open(request(url), timeout=timeout) as response:
        require(response.status == 200, 'Metadata download must return HTTP 200')
        identity_encoding(response.headers)
        advertised = response.headers.get('Content-Length')
        if advertised is not None:
            require(advertised.isdigit() and int(advertised) <= min(limit, budget.remaining), 'Metadata response exceeds size/budget limit')
        with destination.open('xb') as output:
            while True:
                require(budget.remaining > 0, 'Fetched-body budget exhausted')
                chunk = response.read(min(128 * 1024, budget.remaining, limit-count+1))
                if not chunk:
                    break
                budget.consume(len(chunk))
                count += len(chunk)
                require(count <= limit, 'Metadata download exceeds file limit')
                digest.update(chunk)
                output.write(chunk)
        require(advertised is None or count == int(advertised), 'Truncated metadata response')
        return dict(url=url, bytes=count, sha256=digest.hexdigest(), etag=response.headers.get('ETag'),
                    last_modified=response.headers.get('Last-Modified'))


def stream_member(zf, info, destination, limit):
    """Bound complete stored/deflate streams, then publish an exclusive link.

    zipfile validates local headers/overlap. Its ZipExtFile output alone cannot
    prove full decompressed length: it truncates to the central-directory size.
    Therefore independently decompress the complete compressed payload, checking
    EOF, trailing data, actual length and CRC, without that silent truncation.
    """
    member_parts(info)
    require(info.file_size <= limit, 'ZIP member exceeds uncompressed size limit')
    require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'Only stored/deflate extraction is supported')
    if info.compress_type == zipfile.ZIP_STORED:
        require(info.compress_size == info.file_size, 'Stored member size mismatch')
    partial = destination.with_name(destination.name + '.partial')
    absent(destination)
    absent(partial)
    digest, checksum, count, first = hashlib.sha256(), 0, 0, b''
    # No concurrent readers are used. After open() validates and consumes the
    # local header, the underlying seekable source is at the payload start.
    with zf.open(info, 'r'):
        payload_offset = zf.fp.tell()
    zf.fp.seek(payload_offset)
    inflater = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
    with partial.open('xb') as outgoing:
        def emit(chunk):
            nonlocal count, checksum, first
            count += len(chunk)
            require(count <= info.file_size and count <= limit, 'Uncompressed entry exceeds declared size/limit')
            if len(first) < 16:
                first += chunk[:16-len(first)]
            digest.update(chunk)
            checksum = zlib.crc32(chunk, checksum)
            outgoing.write(chunk)
        compressed_left = info.compress_size
        while compressed_left:
            raw = zf.fp.read(min(1024*1024, compressed_left))
            require(bool(raw), 'Truncated compressed member')
            compressed_left -= len(raw)
            if inflater is None:
                emit(raw)
                continue
            pending = raw
            while pending:
                emit(inflater.decompress(pending, min(1024*1024, limit-count+1)))
                require(not inflater.unused_data, 'Trailing bytes after deflate stream')
                pending = inflater.unconsumed_tail
            require(not inflater.eof or compressed_left == 0, 'Deflate EOF precedes declared compressed size')
        if inflater is not None:
            while not inflater.eof:
                tail = inflater.decompress(b'', min(1024*1024, limit-count+1))
                require(bool(tail), 'Truncated deflate stream')
                emit(tail)
            require(not inflater.unused_data and not inflater.unconsumed_tail, 'Unconsumed compressed member data')
    require(count == info.file_size and checksum == info.CRC, 'ZIP member size/CRC mismatch')
    if destination.suffix == '.sqlite':
        require(first == b'SQLite format 3\x00', 'Selected member lacks SQLite file signature')
    # Both paths are in the same newly created artifact directory. link() is
    # exclusive on Windows/POSIX; unlike rename(), it cannot replace a target.
    os.link(partial, destination)
    partial.unlink()  # Remove only our owned temporary directory entry.
    return dict(archive_member=info.filename, bytes=count, compressed_bytes=info.compress_size,
                sha256=digest.hexdigest(), crc32=f'{checksum:08x}')


def legacy_question_members(zf):
    matches = []
    for info in zf.infolist():
        name = info.filename.rsplit('/', 1)[-1].lower()
        if (not info.is_dir() and name.endswith('.json') and
                not any(dialect in name for dialect in ('mysql', 'postgres', '_pg')) and
                re.fullmatch(r'(?:mini[_-]?dev(?:[_-]sqlite)?(?:[-_].*)?|sqlite[_-]mini[_-]?dev.*)\.json', name)):
            require(info.file_size <= 8*1024*1024, 'Legacy question JSON exceeds 8 MiB')
            matches.append(info)
    require(len(matches) <= 8, 'Too many candidate legacy question files')
    return sorted(matches, key=lambda info: info.filename)


def metadata_stage(directory, proxy=None, timeout=30):
    directory = Path(directory).absolute()
    absent(directory)
    directory.mkdir(parents=True, exist_ok=False)
    budget, hf = Budget(), make_opener(proxy)
    questions = fetch_small(hf, QUESTION_URL, directory/'questions.jsonl', budget, 32*1024*1024, timeout)
    readme = fetch_small(hf, README_URL, directory/'README.md', budget, 1024*1024, timeout)
    records, serialization = question_records((directory/'questions.jsonl').read_bytes())
    direct = make_opener()
    archive = archive_head(direct, timeout=timeout)
    with HTTPRangeReader(direct, archive, budget, timeout) as reader, zipfile.ZipFile(reader) as zf:
        listing = inventory(zf, archive)
        inventory_receipt = write_json_exclusive(directory/'zip_inventory.json', listing)
        legacy = {}
        for i, info in enumerate(legacy_question_members(zf), 1):
            filename = f'legacy_questions.{i:03d}.json'
            legacy[filename] = stream_member(zf, info, directory/filename, 8*1024*1024)
        ranges = list(reader.receipts)
    manifest = dict(schema=SCHEMA, stage='metadata_complete', hf_repo=HF_REPO, hf_revision=HF_REVISION,
        files={'questions.jsonl': questions, 'README.md': readme, 'zip_inventory.json': inventory_receipt},
        question_serialization=serialization, question_count=len(records), archive=archive,
        transfer=budget.receipt(), range_receipts=ranges, legacy_question_files=legacy,
        compatibility_status='compatibility_under_audit',
        database_selection='Not performed; prepare selected_db_ids.json before databases stage',
        database_version_caution='This pins the legacy official OSS minidev.zip object, not minidev_0703.zip or a claim of their equivalence.')
    write_json_exclusive(directory/'source_manifest.json', manifest)
    return dict(stage=manifest['stage'], directory=str(directory), questions=len(records), zip_entries=len(listing['entries']), transfer=budget.receipt())


def validate_metadata(directory):
    no_link_ancestors(directory)
    require(directory.is_dir(), 'Metadata artifact directory does not exist')
    no_link_ancestors(directory/'source_manifest.json')
    source = read_json(directory/'source_manifest.json')
    require(source['schema'] == SCHEMA and source['stage'] == 'metadata_complete' and
            source['hf_repo'] == HF_REPO and source['hf_revision'] == HF_REVISION, 'Source manifest identity mismatch')
    require(set(source['files']) == {'questions.jsonl', 'README.md', 'zip_inventory.json'}, 'Source manifest file coverage mismatch')
    for name, expected in source['files'].items():
        no_link_ancestors(directory/name)
        observed = file_receipt(directory/name)
        require(all(observed[k] == expected[k] for k in ('bytes', 'sha256')), f'Metadata hash/size mismatch: {name}')
    for name, expected in source.get('legacy_question_files', {}).items():
        require(re.fullmatch(r'legacy_questions\.[0-9]{3}\.json', name), 'Unexpected legacy question filename')
        no_link_ancestors(directory/name)
        observed = file_receipt(directory/name)
        require(all(observed[k] == expected[k] for k in ('bytes', 'sha256')), f'Legacy question hash/size mismatch: {name}')
    archive = source['archive']
    require(archive['url'] == ARCHIVE_URL and archive['size'] == ARCHIVE_SIZE and archive['etag'] == ARCHIVE_ETAG,
            'Pinned archive identity mismatch')
    require(source['transfer']['limit_bytes'] == MAX_FETCH_BYTES, 'Transfer limit mismatch')
    ranges = source['range_receipts']
    require(isinstance(ranges, list), 'Missing metadata range receipts')
    for receipt in ranges:
        require(receipt['complete'] is True and receipt['etag'] == archive['etag'] and
                type(receipt['start']) is int and type(receipt['end']) is int and
                0 <= receipt['start'] <= receipt['end'] < archive['size'] and
                type(receipt['bytes']) is int and receipt['bytes'] == receipt['end']-receipt['start']+1 and
                re.fullmatch(r'[0-9a-f]{64}', receipt['sha256']), 'Invalid metadata range receipt')
    accounted = sum(source['files'][name]['bytes'] for name in ('questions.jsonl', 'README.md')) + sum(r['bytes'] for r in ranges)
    require(source['transfer']['fetched_body_bytes'] == accounted and
            source['transfer']['remaining_bytes'] == MAX_FETCH_BYTES-accounted, 'Cumulative metadata transfer accounting mismatch')
    Budget(used=source['transfer']['fetched_body_bytes'])
    return source


def extract_database(zf, info, db_id, destination):
    safe_identifier(db_id)
    member_parts(info)
    require(info.file_size <= MAX_ENTRY_BYTES, 'Selected entry exceeds 1 GiB')
    absent(destination)
    destination.parent.mkdir(parents=True, exist_ok=False)
    receipt = stream_member(zf, info, destination, MAX_ENTRY_BYTES)
    return dict(receipt, db_id=db_id, path=f'databases/{db_id}/{db_id}.sqlite',
                sqlite_header_verified=True, sql_executed=False)


def databases_stage(directory, timeout=30):
    directory = Path(directory).absolute()
    source = validate_metadata(directory)
    selection = directory/'selected_db_ids.json'
    no_link_ancestors(selection)
    db_ids = read_json(selection)
    require(isinstance(db_ids, list) and bool(db_ids), 'Selected IDs must be a nonempty list')
    for db_id in db_ids:
        safe_identifier(db_id)
    questions, _ = question_records((directory/'questions.jsonl').read_bytes())
    require(set(db_ids) <= {row['db_id'] for row in questions}, 'Selected ID is absent from pinned questions')
    for name in ('databases', 'database_ranges.jsonl', 'database_receipts.jsonl', 'dataset_manifest.json'):
        absent(directory/name)
    budget = Budget(used=source['transfer']['fetched_body_bytes'])
    direct = make_opener()
    archive = archive_head(direct, expected_size=source['archive']['size'], expected_etag=source['archive']['etag'], timeout=timeout)
    receipts = []
    with (directory/'database_ranges.jsonl').open('x', encoding='utf-8') as range_log, \
         (directory/'database_receipts.jsonl').open('x', encoding='utf-8') as db_log:
        def journal(receipt):
            range_log.write(json.dumps(receipt, sort_keys=True)+'\n')
            range_log.flush()
        with HTTPRangeReader(direct, archive, budget, timeout, journal) as reader, zipfile.ZipFile(reader) as zf:
            require(inventory(zf, archive) == read_json(directory/'zip_inventory.json'), 'ZIP central directory changed')
            selected = selected_members(zf, db_ids)
            require(sum(info.compress_size for _, info in selected) <= budget.remaining, 'Selected compressed data exceeds remaining transfer budget')
            (directory/'databases').mkdir(exist_ok=False)
            for db_id, info in selected:
                receipt = extract_database(zf, info, db_id, directory/'databases'/db_id/(db_id+'.sqlite'))
                receipts.append(receipt)
                db_log.write(json.dumps(receipt, sort_keys=True)+'\n')
                db_log.flush()
    final = dict(schema=SCHEMA, stage='databases_complete', source_manifest=file_receipt(directory/'source_manifest.json'),
        selected_db_ids=db_ids, selection_file=file_receipt(selection), archive=archive, databases=receipts,
        transfer=budget.receipt(), per_entry_limit_bytes=MAX_ENTRY_BYTES,
        receipt_files={name: file_receipt(directory/name) for name in ('database_ranges.jsonl', 'database_receipts.jsonl')},
        full_archive_sha256=None, sql_executed=False, compatibility_status='compatibility_under_audit',
        note='Only selected members fetched through verified HTTP ranges; no complete archive digest was computed.')
    write_json_exclusive(directory/'dataset_manifest.json', final)
    return dict(stage=final['stage'], directory=str(directory), databases=len(receipts), transfer=budget.receipt())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='stage', required=True)
    metadata = sub.add_parser('metadata', help='Fetch pinned HF bytes, ZIP inventory and small legacy question JSONs; no databases')
    metadata.add_argument('--directory', type=Path, default=DEFAULT_DIRECTORY)
    metadata.add_argument('--proxy', help='Optional HF-only HTTP(S) proxy; never persisted')
    databases = sub.add_parser('databases', help='Extract only IDs in selected_db_ids.json')
    databases.add_argument('--directory', type=Path, required=True)
    for child in (metadata, databases):
        child.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args(argv)
    require(args.timeout > 0, 'Timeout must be positive')
    result = metadata_stage(args.directory, args.proxy, args.timeout) if args.stage == 'metadata' else databases_stage(args.directory, args.timeout)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
