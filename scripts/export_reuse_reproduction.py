"""Export a bounded public receipt from a completed LOCAL CPU reproduction.

No raw responses, inputs, commands, absolute paths or credentials are exported.
This validates report/log hashes and test coverage, not a second full rescore.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TESTS = {
    'test_reuse_scenarios', 'test_reuse_protocol', 'test_reuse_statistics',
    'test_reuse_development', 'test_reuse_execution_protocol',
    'test_reuse_readonly_execution', 'test_reuse_mistral_transport',
    'test_inspect_reuse_gguf', 'test_reuse_robustness',
    'test_reuse_robustness_scoring',
}
EXPECTED_SUITES = {'main-qwen', 'main-mistral', 'e3-qwen', 'e3-mistral',
                   'robust-qwen', 'robust-mistral'}


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def build_receipt(run):
    run = run.resolve()
    if not run.is_relative_to(ROOT / 'runtime/runs'):
        raise ValueError('Expected a local archival run')
    report_path = run / 'report.json'
    report = json.loads(report_path.read_bytes())
    index = json.loads((run / 'report_index.json').read_bytes())
    if index != {'report.json': sha(report_path)}:
        raise ValueError('Report hash mismatch')
    if (report['status'] != 'CLEAN_DIRECTORY_CPU_REPRODUCTION_PASSED'
            or report['new_generations'] != 0 or report['paid_API_calls'] != 0
            or report['training'] or report['model_service_started']
            or not report['original_archives_unchanged']):
        raise ValueError('No valid completed CPU-only reproduction')
    if set(report['tests']) != EXPECTED_TESTS or set(report['suites']) != EXPECTED_SUITES:
        raise ValueError('Incomplete coverage')
    tests = {}
    for name, result in report['tests'].items():
        log = run / (name + '.log')
        if result['returncode'] != 0 or sha(log) != result['log_sha256']:
            raise ValueError('Test log mismatch: ' + name)
        text = log.read_text(encoding='utf-8')
        count = re.findall(r'^Ran (\d+) tests? in ', text, flags=re.MULTILINE)
        if len(count) != 1 or not re.search(r'^OK\s*$', text, flags=re.MULTILINE):
            raise ValueError('Missing unittest terminal result: ' + name)
        tests[name] = {'tests_passed': int(count[0]), 'log_sha256': sha(log)}
    suites = {}
    allowed = ('scientific_summary_exact', 'summary_byte_identical',
               'original_summary_sha256', 'reproduced_summary_sha256',
               'input_hashes_exact', 'copied_inputs_unchanged', 'allowed_difference')
    for name, result in report['suites'].items():
        if result['returncode'] != 0 or sha(run / (name + '.log')) != result['log_sha256']:
            raise ValueError('Scoring log mismatch: ' + name)
        if not all(result[k] is True for k in
                   ('scientific_summary_exact', 'input_hashes_exact', 'copied_inputs_unchanged')):
            raise ValueError('Scientific mismatch: ' + name)
        expected_difference = 'prepared absolute path only' if name.startswith('e3-') else None
        if result['allowed_difference'] != expected_difference:
            raise ValueError('Unexpected normalization: ' + name)
        summary = run / (name + '-rescore') / 'summary.json'
        if sha(summary) != result['reproduced_summary_sha256']:
            raise ValueError('Scoring output changed: ' + name)
        suites[name] = {key: result[key] for key in allowed}
    return {
        'status': report['status'], 'date': '2026-10-27',
        'run_directory': run.relative_to(ROOT).as_posix(),
        'report_sha256': sha(report_path), 'source_commit': report['source_commit'],
        'source_zip_sha256': report['source_zip_sha256'],
        'test_modules': len(tests),
        'tests_passed': sum(item['tests_passed'] for item in tests.values()),
        'tests': tests, 'suites': suites,
        'original_archives_unchanged': True,
        'new_generations': 0, 'paid_API_calls': 0,
        'training': False, 'model_service_started': False,
        'seconds': report['seconds'], 'limits': report['limits'],
        'prior_failed_attempt': 'runtime/runs/reuse-clean-reproduction-20261027-001',
        'receipt_scope': 'Hash-checked report, test logs and reproduced summaries; '
                         'not independent model regeneration or another full archival rescore.',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    receipt = build_receipt(args.run)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'status': receipt['status'], 'tests_passed': receipt['tests_passed'],
                      'suites': len(receipt['suites'])}))
