"""Fingerprint local weights and prepare a one-item native-vLLM smoke run.

Hashes identify local content, not a verified upstream commit or proof of which
weights a running process loaded. Retain server startup logs independently.
"""
import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
import urllib.request
from .e1 import digest, save


def fingerprint(root):
    root = Path(root).resolve()
    index = json.loads((root / 'model.safetensors.index.json').read_text())
    required = {'config.json', 'tokenizer_config.json', 'tokenizer.json',
                'model.safetensors.index.json'} | set(index['weight_map'].values())
    for name in required:
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f'Missing or unsafe required model file: {name}')
    files = {}
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix not in {'.json', '.safetensors', '.txt', '.model'}:
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError('Model file symlink escapes model root')
        h = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b''):
                h.update(chunk)
        files[path.relative_to(root).as_posix()] = {'sha256': h.hexdigest(), 'size': path.stat().st_size}
        print('Hashed:', path.name, flush=True)
    return files


def verify(config):
    if digest(config['model_files']) != config['model_content_sha256']:
        raise ValueError('Model manifest digest mismatch')
    if fingerprint(config['model_path']) != config['model_files']:
        raise ValueError('Local model files changed')
    for package, expected in config['packages'].items():
        if version(package) != expected:
            raise ValueError(f'Package version changed: {package}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-path', required=True)
    p.add_argument('--output', default='runtime/runs/native-smoke-config.json')
    args = p.parse_args()
    output = Path(args.output)
    if output.exists():
        p.error('Config already exists; preserve it and select a new output path')
    root = Path(args.model_path).resolve()
    config = json.loads(Path('configs/e1_pilot.json').read_text())
    # Read-only endpoint check: no inference requests.
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models', timeout=10) as r:
        models = json.load(r)
    found = [x for x in models['data'] if x['id'] == config['model']]
    if len(found) != 1 or Path(found[0]['root']).resolve() != root:
        raise ValueError('Served model name/path does not match local weights')
    files = fingerprint(root)
    config.update(deployment='native', revision=None, server_image=None,
                  items=1, status='ENGINEERING_SMOKE_NOT_EFFICACY_EVIDENCE',
                  model_path=str(root), model_files=files, model_content_sha256=digest(files),
                  packages={k: version(k) for k in ['torch', 'vllm', 'transformers', 'modelscope']},
                  python=sys.version, model_source='ModelScope/Qwen/Qwen2.5-7B-Instruct',
                  source_revision='master (mutable; local content fingerprint used)',
                  served_model_metadata=found[0],
                  server_settings_reported={'dtype': 'bfloat16', 'max_model_len': 4096,
                      'max_num_seqs': 4, 'gpu_memory_utilization': 0.85,
                      'enforce_eager': True, 'generation_config': 'vllm', 'seed': 20261001},
                  provenance_note='Server settings are declared, not independently attested. Retain startup logs.')
    config['gpu_environment'] = subprocess.check_output(['nvidia-smi'], text=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, config)
    print('CONFIG:', output)
    print('CONTENT_SHA256:', config['model_content_sha256'])
    print('Prepared 1 item / 7 calls. No inference performed by this command.')


if __name__ == '__main__':
    main()
