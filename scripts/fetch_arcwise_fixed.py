"""Acquire three pinned Arcwise-Plat text artifacts, never databases or code.

The CLI is a dry plan unless --execute is supplied. Existing output directories
are refused, including partial failures. TLS validation remains enabled. Neither
proxy configuration nor exception messages (which may contain credentials) are
persisted. This performs structural validation, not human task approval.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from scripts.fetch_bird_fixed import (
    Budget, FetchError, absent, fetch_small, make_opener, require,
    safe_identifier, strict_json, write_json_exclusive,
)


REPOSITORY = 'uiuc-kang-lab/text_to_sql_benchmarks'
REVISION = 'fe766045c55b6875a43b30e9ac7683df5582f8cf'
REVISION_HISTORY_URL = f'https://github.com/{REPOSITORY}/commit/{REVISION}'
SOURCE_BASE = f'https://raw.githubusercontent.com/{REPOSITORY}/{REVISION}'
SOURCES = {
    'README.md': SOURCE_BASE + '/README.md',
    'LICENSE': SOURCE_BASE + '/LICENSE',
    'arcwise_plat_full_with_diff.json': SOURCE_BASE + '/data/arcwise_plat_full_with_diff.json',
}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
SCHEMA = 'arcwise-plat-fixed-text-fetch-v1'
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / 'runtime/runs/datasets/arcwise-plat-fixed-20261012-001'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def validate_questions(raw):
    """Return structural counts only; never certify SQL or task semantics."""
    records = strict_json(raw.decode('utf-8-sig'))
    require(isinstance(records, list) and bool(records), 'Expected a nonempty JSON array')
    seen, databases, fields = set(), set(), set()
    for row in records:
        require(isinstance(row, dict), 'Question entry must be an object')
        require({'question_id', 'db_id', 'question', 'evidence', 'SQL'} <= set(row),
                'Question entry lacks a required field')
        qid = row['question_id']
        require((isinstance(qid, str) and bool(qid.strip())) or
                (type(qid) is int and qid >= 0), 'Invalid question ID')
        require(str(qid) not in seen, 'Duplicate question ID')
        seen.add(str(qid))
        databases.add(safe_identifier(row['db_id']))
        for field in ('question', 'SQL'):
            require(isinstance(row[field], str) and bool(row[field].strip()),
                    'Question and SQL must be nonempty strings')
        require(row['evidence'] is None or isinstance(row['evidence'], str),
                'Evidence must be text or null')
        fields.update(row)
    return {
        'record_count': len(records), 'database_ids': sorted(databases),
        'field_names': sorted(fields), 'serialization': 'json_array',
        'validation_scope': 'structure_and_unique_ids_only',
        'human_confirmation_count': 0, 'sql_executed': False,
        'database_compatibility_verified': False,
        'cache_intervention_labels_present': False,
    }


def plan(directory=DEFAULT_DIRECTORY):
    require(re.fullmatch(r'[0-9a-f]{40}', REVISION) is not None, 'Revision must be a full Git SHA')
    return {
        'schema': SCHEMA, 'stage': 'plan_no_download',
        'directory': str(Path(directory).absolute()), 'repository': REPOSITORY,
        'revision': REVISION, 'revision_history_url': REVISION_HISTORY_URL,
        'sources': dict(SOURCES), 'per_file_limit_bytes': MAX_FILE_BYTES,
        'total_body_limit_bytes': MAX_TOTAL_BYTES,
        'sql_executed': False, 'source_code_executed': False,
        'note': 'Explicit --execute is required. Existing output is never resumed or overwritten.',
    }


def fetch(directory=DEFAULT_DIRECTORY, proxy=None, timeout=30):
    """Download text to a new directory and return the complete manifest.

    Success files: README.md, LICENSE, arcwise_plat_full_with_diff.json,
    started.json and source_manifest.json. Failure preserves downloaded bytes,
    started.json and failure.json; no complete manifest is published.
    """
    specification = plan(directory)
    require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
            'Timeout must be finite and positive')
    directory = Path(directory).absolute()
    absent(directory)
    opener = make_opener(proxy)
    budget = Budget(limit=MAX_TOTAL_BYTES)
    directory.mkdir(parents=True, exist_ok=False)
    started = utc_now()
    stage, files = 'initializing', {}
    try:
        write_json_exclusive(directory / 'started.json', {
            **specification, 'stage': 'started', 'started_at_utc': started,
            'tls_verification': True,
            'human_confirmation_count': 0,
        })
        for name, url in SOURCES.items():
            stage = name
            receipt = fetch_small(opener, url, directory / name, budget,
                                  MAX_FILE_BYTES, timeout)
            receipt['retrieved_at_utc'] = utc_now()
            files[name] = receipt
            raw = (directory / name).read_bytes()
            text = raw.decode('utf-8-sig')
            require(bool(text.strip()) and '\x00' not in text, 'Expected nonempty UTF-8 text')
        stage = 'json_structure_validation'
        structural = validate_questions((directory / 'arcwise_plat_full_with_diff.json').read_bytes())
        final = {
            'schema': SCHEMA, 'stage': 'text_complete',
            'repository': REPOSITORY, 'revision': REVISION,
            'revision_history_url': REVISION_HISTORY_URL,
            'started_at_utc': started, 'completed_at_utc': utc_now(),
            'files': files, 'transfer': budget.receipt(),
            'per_file_limit_bytes': MAX_FILE_BYTES,
            'structural_validation': structural,
            'tls_verification': True, 'sql_executed': False,
            'source_code_executed': False,
            'compatibility_status': 'not_yet_checked',
            'note': 'Source annotations are not independent human confirmations by this project. No databases or schema files were downloaded.',
        }
        stage = 'manifest_publication'
        write_json_exclusive(directory / 'source_manifest.json', final)
        return final
    except BaseException as error:
        # Do not persist repr(error), request headers, proxy or authentication.
        # Already fetched bytes stay in place, including truncated artifacts.
        write_json_exclusive(directory / 'failure.json', {
            'schema': SCHEMA, 'stage': 'failed', 'revision': REVISION,
            'started_at_utc': started, 'failed_at_utc': utc_now(),
            'failed_step': stage, 'error_type': type(error).__name__,
            'completed_transfers': files, 'transfer': budget.receipt(),
            'resumable': False, 'human_confirmation_count': 0,
        })
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument('--proxy', help='Optional explicit HTTP(S) proxy without credentials; never persisted')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--execute', action='store_true', help='Explicitly acquire the three fixed text files')
    args = parser.parse_args(argv)
    try:
        result = fetch(args.directory, args.proxy, args.timeout) if args.execute else plan(args.directory)
    except Exception as error:
        print(json.dumps({'stage': 'failed', 'error_type': type(error).__name__,
                          'note': 'Inspect failure.json if an output directory was created; no automatic retry.'}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
