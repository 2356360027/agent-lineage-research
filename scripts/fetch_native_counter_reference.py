"""Fetch seven pinned public text references; no remote code execution or API."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

REVISION = '935817c66f5d6e64838f419692545f06787e485f'
BASE = 'https://raw.githubusercontent.com/deepseek-ai/deepseek-recipe/' + REVISION + '/'
FILES = ('LICENSE', 'Cargo.toml', 'deepseek-recipe-encoding/src/v4/dsv41.rs',
         'deepseek-recipe-encoding/src/v4/mod.rs', 'deepseek-recipe-core/src/json.rs',
         'deepseek-recipe-core/src/special_tokens.rs', 'deepseek-recipe-core/src/tools.rs')


def fetch(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'revision': REVISION, 'files': {}, 'new_API_calls': 0,
                'downloaded_code_executed': False}
    try:
        for name in FILES:
            with urllib.request.urlopen(BASE + name, timeout=20) as response:
                data = response.read(256 * 1024 + 1)
            if len(data) > 256 * 1024:
                raise ValueError('Reference file too large')
            data.decode('utf-8')
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(data)
            manifest['files'][name] = {'sha256': hashlib.sha256(data).hexdigest(),
                                       'bytes': len(data), 'url': BASE + name}
        manifest['status'] = 'FETCHED_TEXT_ONLY'
    except Exception as exc:
        manifest.update(status='PARTIAL_FAILURE_RETAINED', error_type=type(exc).__name__)
        raise
    finally:
        with (output / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, indent=2)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    print(json.dumps(fetch(parser.parse_args().output), indent=2))
