"""Download public model and require exact content/package match before use.

No inference or vLLM launch. Run with a PRIVATE old native configuration.
The ModelScope master revision is mutable: content hashes, not its name,
decide whether the download matches the previously used weights.
"""
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from runtime.native import fingerprint
from runtime.e1 import digest

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--reference',required=True)
    p.add_argument('--cache-dir',required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args();out=Path(a.output)
    if out.exists():raise FileExistsError('Preserve previous migration record')
    old=json.loads(Path(a.reference).read_text())
    if digest(old['model_files'])!=old['model_content_sha256']:raise ValueError('Reference fingerprint invalid')
    from modelscope import snapshot_download
    from modelscope.hub.api import HubApi
    api=HubApi();before=api.get_valid_revision_detail('Qwen/Qwen2.5-7B-Instruct',revision='master')
    print('MODEL_REVISION_BEFORE',before,flush=True)
    started=time.time()
    path=snapshot_download(model_id='Qwen/Qwen2.5-7B-Instruct',revision='master',cache_dir=a.cache_dir)
    after=api.get_valid_revision_detail('Qwen/Qwen2.5-7B-Instruct',revision='master')
    files=fingerprint(path)
    packages={k:importlib.metadata.version(k) for k in old['packages']}
    result=dict(started_unix=started,finished_unix=time.time(),model_path=str(Path(path).resolve()),
        revision_before=before,revision_after=after,revision_stable=before==after,
        model_files=files,model_content_sha256=digest(files),reference_content_sha256=old['model_content_sha256'],
        content_matches=files==old['model_files'],packages=packages,packages_match=packages==old['packages'],
        python=sys.version,status='CHECKED_NOT_YET_SERVED')
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ('model_files','revision_before','revision_after')},indent=2),flush=True)
    if not (result['content_matches'] and result['packages_match'] and result['revision_stable']):
        raise ValueError('Migration mismatch; do not start continuation experiment')

if __name__=='__main__':main()
