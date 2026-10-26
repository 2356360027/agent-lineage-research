"""Clean-directory CPU-only reproduction of sealed study scores and unit tests.

Copies raw archives locally, never publishes them. Runs no inference, API,
training, downloads or model server. Old files are never edited or removed.
The E3 summary's one absolute location field is normalized; all scientific
values and recorded timings must remain identical. No other tolerance allowed.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    ('main-qwen', 'reuse-confirmation-qwen-prepared-20261027-001', 'reuse-confirmation-qwen-audit-20261027-001', 'reuse_confirmation_qwen_20261027.json'),
    ('main-mistral', 'reuse-confirmation-mistral-prepared-20261027-001', 'reuse-confirmation-mistral-audit-20261027-001', 'reuse_confirmation_mistral_20261027.json'),
    ('e3-qwen', 'reuse-e3-qwen-prepared-20261027-001', 'reuse-e3-qwen-audit-20261027-001', 'reuse_e3_qwen_20261027.json'),
    ('e3-mistral', 'reuse-e3-mistral-prepared-20261027-001', 'reuse-e3-mistral-audit-20261027-001', 'reuse_e3_mistral_20261027.json'),
    ('robust-qwen', 'reuse-robustness-qwen-prepared-20261027-001', 'reuse-robustness-qwen-audit-20261027-003', 'reuse_robustness_qwen_20261027.json'),
    ('robust-mistral', 'reuse-robustness-mistral-prepared-20261027-001', 'reuse-robustness-mistral-audit-20261027-001', 'reuse_robustness_mistral_20261027.json'),
)
TESTS = ('test_reuse_scenarios', 'test_reuse_protocol', 'test_reuse_statistics',
         'test_reuse_development', 'test_reuse_execution_protocol', 'test_reuse_readonly_execution',
         'test_reuse_mistral_transport', 'test_inspect_reuse_gguf', 'test_reuse_robustness',
         'test_reuse_robustness_scoring')


def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as f: json.dump(value, f, indent=2, ensure_ascii=False)


def inventory(folder):
    records = {}
    for p in sorted(folder.rglob('*')):
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise ValueError('No linked archival inputs')
        if p.is_file(): records[p.relative_to(folder).as_posix()] = sha(p)
    return records


def normalize_summary(value, suite):
    # Only E3 stores its execution directory at top level. Do not normalize
    # latency, token counts, outputs, errors, manifests or nested content.
    value = dict(value)
    if suite.startswith('e3-'):
        if not isinstance(value.get('prepared'), str): raise ValueError('Missing E3 location')
        value['prepared'] = '<relocated-prepared-directory>'
    return value


def run_logged(command, cwd, log, timeout=600):
    start = time.perf_counter()
    with log.open('xb') as stream:
        process = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
            timeout=timeout, env={k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if process.returncode: raise RuntimeError('CPU reproduction command failed; inspect '+log.name)
    return {'seconds': time.perf_counter()-start, 'returncode': process.returncode,
            'log_sha256': sha(log), 'argv': command}


def execute(output):
    output = output.resolve()
    if not output.is_relative_to(ROOT/'runtime/runs') or output == ROOT/'runtime/runs':
        raise ValueError('Use a new dedicated local run folder')
    output.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter(); report = {'status': 'FAILED', 'new_generations': 0, 'paid_API_calls': 0,
        'training': False, 'model_service_started': False, 'suites': {}, 'tests': {}}
    try:
        git = ['git', '-c', 'safe.directory='+ROOT.as_posix(), '-C', str(ROOT)]
        commit = subprocess.check_output(git+['rev-parse', 'HEAD']).decode().strip()
        blob = subprocess.check_output(git+['archive', '--format=zip', commit, 'runtime', 'scripts', 'tests', 'configs'])
        clean = output/'clean'; clean.mkdir()
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for entry in archive.infolist():
                target = (clean/entry.filename).resolve()
                if not target.is_relative_to(clean) or entry.file_size > 10_000_000:
                    raise ValueError('Unexpected clean-source member')
                if entry.is_dir(): target.mkdir(parents=True, exist_ok=True); continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as f: f.write(archive.read(entry))
        if sha(clean/'scripts/reproduce_reuse_package.py') != sha(Path(__file__)):
            raise ValueError('Commit this orchestrator before executing it')
        report.update(source_commit=commit, source_zip_sha256=hashlib.sha256(blob).hexdigest(),
                      python_version=sys.version, original_archives_unchanged=False)
        originals = {}; plans = []
        for name, directory, audit, record_name in SUITES:
            src = ROOT/'runtime/runs'/directory; dst = output/'archives'/directory
            record = json.loads((ROOT/'configs/run_records'/record_name).read_bytes())
            original_audit = ROOT/'runtime/runs'/audit
            if sha(original_audit/'summary.json') != record['summary_sha256']:
                raise ValueError('Published original summary hash mismatch')
            if sha(src/'manifest.json') != record['manifest_sha256']:
                raise ValueError('Published manifest mismatch')
            before = inventory(src); originals[str(src)] = before
            shutil.copytree(src, dst)
            if inventory(dst) != before: raise ValueError('Copied archive hash mismatch')
            plans.append((name, src, dst, original_audit, record))
            print('Verified local archival copy: '+name, flush=True)
        save(output/'input_inventory.private.json', originals)
        save(output/'plan.json', {'source_commit': commit, 'suites': [p[0] for p in plans],
             'max_seconds_per_command': 600, 'new_generations': 0,
             'E3_only_allowed_summary_difference': 'top-level absolute prepared path'})
        for test in TESTS:
            command = [sys.executable, '-I', '-B', '-X', 'utf8', '-m', 'unittest', 'discover',
                       '-s', str(clean/'tests'), '-t', str(clean), '-p', test+'.py', '-v']
            report['tests'][test] = run_logged(command, clean, output/(test+'.log'))
            print('Clean test module passed: '+test, flush=True)
        for name, src, dst, original_audit, record in plans:
            result = output/(name+'-rescore'); source = dst/'source'
            if name.startswith('main-'):
                command = [sys.executable, '-I', '-B', '-X', 'utf8', str(source/'scripts/run_reuse_development.py'),
                    '--audit', '--prepared', str(dst), '--manifest-sha256', record['manifest_sha256'], '--output', str(result)]
            elif name.startswith('e3-'):
                command = [sys.executable, '-I', '-B', '-X', 'utf8', str(source/'scripts/summarize_reuse_execution.py'),
                    '--prepared', str(dst), '--manifest-sha256', record['manifest_sha256'], '--output', str(result)]
            else:
                adapter = clean/'scripts/score_reuse_robustness_v2.py'
                command = [sys.executable, '-I', '-B', '-X', 'utf8', str(adapter), '--prepared', str(dst),
                    '--manifest-sha256', record['manifest_sha256'], '--scorer-sha256', sha(adapter), '--output', str(result)]
            print('Clean frozen-source rescore started: '+name, flush=True)
            measured = run_logged(command, output, output/(name+'.log'))
            actual = json.loads((result/'summary.json').read_bytes())
            expected = json.loads((original_audit/'summary.json').read_bytes())
            if normalize_summary(actual, name) != normalize_summary(expected, name):
                raise ValueError('Reproduced scientific summary mismatch: '+name)
            if (result/'input_hashes.json').read_bytes() != (original_audit/'input_hashes.json').read_bytes():
                raise ValueError('Input hashes differ after relocation: '+name)
            if inventory(dst) != originals[str(src)]: raise ValueError('Archive mutated during rescore')
            report['suites'][name] = dict(measured, scientific_summary_exact=True,
                summary_byte_identical=sha(result/'summary.json') == sha(original_audit/'summary.json'),
                original_summary_sha256=sha(original_audit/'summary.json'),
                reproduced_summary_sha256=sha(result/'summary.json'),
                input_hashes_exact=True, copied_inputs_unchanged=True,
                allowed_difference='prepared absolute path only' if name.startswith('e3-') else None)
            print('Exact scientific reproduction passed: '+name, flush=True)
        if any(inventory(Path(src)) != before for src, before in originals.items()):
            raise ValueError('Original archival files changed')
        report['original_archives_unchanged'] = True
        report['status'] = 'CLEAN_DIRECTORY_CPU_REPRODUCTION_PASSED'
        report['limits'] = ('Same installed Python/SQLite and machine, not an independent hardware replication. '
            'No model regeneration or rerun of the Java optimizer. E3 rechecks frozen SQL scores and recorded costs, '
            'not new timing benchmarks. Private archival copies remain local.')
    except BaseException as exc:
        report['failure'] = {'type': type(exc).__name__, 'detail': str(exc)}
    finally:
        report['seconds'] = time.perf_counter()-start
        save(output/'report.json', report)
        save(output/'report_index.json', {'report.json': sha(output/'report.json')})
    print(json.dumps({k: report[k] for k in ('status', 'new_generations', 'seconds')}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True); args = parser.parse_args()
    if execute(args.output)['status'] != 'CLEAN_DIRECTORY_CPU_REPRODUCTION_PASSED': raise SystemExit(1)
