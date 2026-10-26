"""Prepare and run a frozen 32-generation local-only Day-1 calibration.

No downloads, credentials, paid endpoints, SQL from models, retries or resume.
Only researcher-authored historical SQL is executed on in-memory databases.
Run from the frozen source directory after --prepare.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
from runtime.reuse_separation import (VERSION, audit_design, calibration_cases, canonical,
    digest, jobs, messages, parse_response, summarize)

MODEL = 'local-qwen25-7b-q5'
PORT = 18090
SOURCES = ('runtime/reuse_separation.py', 'scripts/run_local_separation.py',
           'tests/test_reuse_separation.py', 'configs/local_gguf_assets_20261024.json')


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def prepare(output, assets, runtime):
    design = audit_design()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in SOURCES:
        dest = output / 'source' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE_ROOT / name, dest)
    # Explicit regular packages prevent unrelated installed modules taking precedence.
    for package in ('runtime', 'scripts', 'tests'):
        (output / 'source' / package / '__init__.py').touch(exist_ok=False)
    save(output / 'cases.private.json', calibration_cases())
    save(output / 'jobs.json', jobs())
    save(output / 'design_audit.json', design)
    # Freeze trusted asset hashes before generation, not only a mutable cache path.
    shutil.copyfile(SOURCE_ROOT / 'configs/local_gguf_assets_20261024.json', output / 'asset_spec.json')
    config = {'version': VERSION, 'created_at': utc(), 'assets': str(assets.resolve()),
              'runtime': str(runtime.resolve()), 'model_alias': MODEL,
              'max_calls': 32, 'max_tokens': 256, 'temperature': 0, 'seed': 20261027,
              'context_size': 4096, 'deadline_server_seconds': 600,
              'port': PORT, 'parallel': 1, 'cache_prompt': False,
              'max_run_seconds_including_hashes': 3600, 'max_http_seconds': 60,
              'max_cleanup_seconds': 15, 'automatic_retry': False,
              'json_grammar_enforced': False, 'extra_warmup_calls': 0,
              'first_generation_includes_warmup': True,
              'confirmation_data': False, 'API_cost_cny': 0,
              'runtime_api_reference': 'https://github.com/ggml-org/llama.cpp/blob/b11850/tools/server/README.md'}
    save(output / 'config.json', config)
    files = {str(p.relative_to(output)).replace('\\', '/'): sha(p)
             for p in output.rglob('*') if p.is_file()}
    save(output / 'manifest.json', {'files': files, 'scope': 'DEVELOPMENT_CALIBRATION_ONLY'})
    result = {'status': 'PREPARED_NO_INFERENCE', 'prepared': str(output),
              'manifest_sha256': sha(output / 'manifest.json'), **design}
    result['status'] = 'PREPARED_NO_INFERENCE'
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def execute(prepared, expected_manifest):
    overall_started = time.perf_counter()
    prepared = prepared.resolve()
    if sha(prepared / 'manifest.json') != expected_manifest:
        raise ValueError('Manifest hash mismatch')
    manifest = json.loads((prepared / 'manifest.json').read_bytes())
    for name, expected in manifest['files'].items():
        path = (prepared / name).resolve()
        if not path.is_relative_to(prepared) or sha(path) != expected:
            raise ValueError('Frozen input/source mismatch')
    if SOURCE_ROOT != prepared / 'source':
        raise ValueError('Execute the frozen source/scripts/run_local_separation.py')
    conf = json.loads((prepared / 'config.json').read_bytes())
    cases = json.loads((prepared / 'cases.private.json').read_bytes())
    schedule = json.loads((prepared / 'jobs.json').read_bytes())
    if cases != json.loads(canonical(calibration_cases())) or schedule != jobs():
        raise ValueError('Generated inputs differ from frozen inputs')
    if conf['max_calls'] != 32 or conf['deadline_server_seconds'] > 600:
        raise ValueError('Scope changed')
    # Never overwrite, silently resume or retry any attempted run.
    save(prepared / 'execution_started.json', {'utc': utc(), 'manifest_sha256': expected_manifest})
    out = prepared / 'experiment'
    out.mkdir(exist_ok=False)
    responses, measurements = {}, []
    attempts = 0
    server_started = None
    server_ready = None
    proc = None
    stopped = True
    status = 'FAILED'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        assets = Path(conf['assets'])
        asset_manifest = json.loads((prepared / 'asset_spec.json').read_bytes())
        checked = {}
        for entry in asset_manifest['assets']:
            path = assets / entry['name']
            actual = sha(path)
            if actual != entry['sha256']:
                raise ValueError('Model/runtime asset hash mismatch')
            checked[path.name] = actual
        save(out / 'verified_assets.json', checked)
        runtime = Path(conf['runtime'])
        exe = runtime / 'llama-server.exe'
        runtime_hashes = {p.name: sha(p) for p in sorted(runtime.iterdir())
                          if p.is_file() and p.suffix.lower() in ('.exe', '.dll')}
        archived_hashes = {}
        for entry in asset_manifest['assets']:
            if not entry['name'].endswith('.zip'):
                continue
            with zipfile.ZipFile(assets / entry['name']) as archive:
                for member in archive.infolist():
                    basename = Path(member.filename).name
                    if basename not in runtime_hashes:
                        continue
                    h = hashlib.sha256()
                    with archive.open(member) as stream:
                        for block in iter(lambda: stream.read(4 * 1024**2), b''):
                            h.update(block)
                    if basename in archived_hashes and archived_hashes[basename] != h.hexdigest():
                        raise ValueError('Conflicting pinned runtime archives')
                    archived_hashes[basename] = h.hexdigest()
        if runtime_hashes != archived_hashes:
            raise ValueError('Extracted runtime does not match pinned archives')
        save(out / 'runtime_files_sha256.json', runtime_hashes)
        version = subprocess.run([str(exe), '--version'], capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        save(out / 'version.json', {'returncode': version.returncode,
            'stdout': version.stdout.decode(errors='replace'), 'stderr': version.stderr.decode(errors='replace')})
        gpu = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,memory.used,driver_version',
                              '--format=csv,noheader'], capture_output=True, timeout=15)
        save(out / 'gpu_before.json', {'returncode': gpu.returncode,
                                      'stdout': gpu.stdout.decode(errors='replace')})
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', PORT))
        if time.perf_counter() - overall_started > conf['max_run_seconds_including_hashes']:
            raise TimeoutError('Preflight time limit')
        cmd = [str(exe.resolve()), '--model',
            str((assets / 'qwen2.5-7b-instruct-q5_k_m-00001-of-00002.gguf').resolve()),
            '--alias', MODEL, '--host', '127.0.0.1', '--port', str(PORT), '--ctx-size', '4096',
            '--gpu-layers', '99', '--parallel', '1', '--no-webui', '--no-context-shift',
            '--no-cache-prompt', '--cache-ram', '0']
        save(out / 'launch.json', {'command': cmd, 'utc': utc()})
        server_started = time.perf_counter()

        def request(path, payload=None, timeout=60):
            remaining = conf['deadline_server_seconds'] - (time.perf_counter() - server_started)
            if remaining <= 0:
                raise TimeoutError('Batch time limit')
            req = urllib.request.Request(f'http://127.0.0.1:{PORT}' + path,
                data=None if payload is None else canonical(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'})
            with opener.open(req, timeout=min(timeout, remaining)) as response:
                return json.load(response)

        with (out / 'server.log').open('xb') as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            stopped = False
            ready = False
            while time.perf_counter() - server_started < 120:
                if proc.poll() is not None:
                    raise RuntimeError('Owned server exited during startup')
                try:
                    ready = request('/health', timeout=2).get('status') == 'ok'
                except (OSError, ValueError):
                    pass
                if ready:
                    break
                time.sleep(0.5)
            if not ready:
                raise TimeoutError('Server startup timeout')
            server_ready = time.perf_counter()
            model_list = request('/v1/models')
            save(out / 'models.json', model_list)
            save(out / 'props.json', request('/props'))
            if MODEL not in [m['id'] for m in model_list['data']]:
                raise ValueError('Model alias mismatch')
            by_case = {c['id']: c for c in cases}
            for index, job in enumerate(schedule):
                proposal = None
                if 'proposal_id' in job:
                    proposal = parse_response(responses[job['proposal_id']], 'M1')['admissibility']
                msgs = messages(by_case[job['case_id']], job, proposal)
                # Template and tokenize without generation; refuse context overflow.
                templated = request('/apply-template', {'messages': msgs})
                tokenized = request('/tokenize', {'content': templated['prompt'],
                                                  'add_special': False, 'parse_special': True})
                prompt_tokens = len(tokenized['tokens'])
                if prompt_tokens + conf['max_tokens'] + 64 > conf['context_size']:
                    raise ValueError('Context exceeds fixed budget; do not truncate')
                payload = {'model': MODEL, 'messages': msgs, 'max_tokens': conf['max_tokens'],
                    'temperature': 0, 'seed': conf['seed'], 'stream': False, 'cache_prompt': False}
                prefix = f'{index:02d}'
                save(out / (prefix + '.request.json'), payload)
                save(out / (prefix + '.dispatch.json'), {'job_id': job['id'], 'utc': utc(),
                    'input_sha256': digest(payload), 'preflight_prompt_tokens': prompt_tokens,
                    'model_generation_index': index, 'includes_first_generation_warmup': index == 0})
                attempts += 1
                started = time.perf_counter()
                raw = request('/v1/chat/completions', payload)
                duration = time.perf_counter() - started
                save(out / (prefix + '.response.json'), raw)
                responses[job['id']] = raw
                record = {'job_id': job['id'], 'seconds': duration, 'usage': raw.get('usage'),
                          'timings': raw.get('timings'), 'preflight_prompt_tokens': prompt_tokens}
                measurements.append(record)
                save(out / (prefix + '.measurement.json'), record)
                used = raw.get('usage', {}).get('prompt_tokens')
                if not isinstance(used, int) or used + conf['max_tokens'] > conf['context_size']:
                    raise ValueError('Missing or out-of-bounds prompt usage')
                # Transport errors stop the entire batch. Invalid outputs are retained;
                # effect scoring is performed only after all scheduled calls finish.
                print(f'Completed {index + 1}/32', flush=True)
            status = 'COMPLETED'
    except BaseException as exc:
        save(out / 'failure.json', {'type': type(exc).__name__, 'detail': str(exc), 'utc': utc()})
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            stopped = proc.poll() is not None
        summary = summarize(responses)
        summary.update(execution_status=status, dispatch_attempts=attempts,
            measurements=measurements, manifest_sha256=expected_manifest,
            server_seconds=None if server_started is None else time.perf_counter()-server_started,
            cold_start_seconds=None if server_ready is None else server_ready-server_started,
            total_seconds_including_preflight=time.perf_counter()-overall_started,
            owned_server_stopped=stopped, api_cny=0, energy_measured=False,
            logical_workflow_call_counts={'M1': 12, 'M2': 12, 'M3': 4,
                'M4': '4 reused M1 proposals + 4 independent verifier calls; shared across prices'},
            prefix_cache_requested=False, no_current_SQL_or_model_transform_executed=True)
        save(out / 'summary.json', summary)
        save(out / 'sha256.json', {p.name: sha(p) for p in out.iterdir() if p.is_file()})
    print(json.dumps({k: v for k, v in summary.items()
        if k not in ('judgments', 'decisions', 'measurements')}, ensure_ascii=False, indent=2))
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--execute', action='store_true')
    p.add_argument('--output', type=Path)
    p.add_argument('--assets', type=Path)
    p.add_argument('--runtime', type=Path)
    p.add_argument('--prepared', type=Path)
    p.add_argument('--manifest-sha256')
    a = p.parse_args()
    if a.prepare:
        if not all((a.output, a.assets, a.runtime)):
            p.error('prepare requires output, assets, runtime')
        prepare(a.output, a.assets, a.runtime)
    else:
        if not a.prepared or not a.manifest_sha256:
            p.error('execute requires prepared and manifest SHA256')
        result = execute(a.prepared, a.manifest_sha256)
        if result['execution_status'] != 'COMPLETED':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
