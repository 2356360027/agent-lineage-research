"""One bounded download of the already selected public model artifact.

No credentials, mirrors, TLS overrides or automatic retry. Keep partial bytes.
This is transport, not a model experiment. Existing verified copies are reused.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def fetch(spec_path,output,proxy):
    spec_path=spec_path.resolve();output=output.resolve()
    spec=json.loads(spec_path.read_bytes())
    if (spec['artifact_repo']!='bartowski/Mistral-7B-Instruct-v0.3-GGUF'
        or spec['artifact_revision']!='61fd4167fff3ab01ee1cfe0da183fa27a944db48'
        or spec['filename']!='Mistral-7B-Instruct-v0.3-Q5_K_M.gguf'
        or spec['sha256']!='0374072546aaf1257d1e34610edb11b691730d8ef7d44b5629693a2dd8e8d472'
        or spec['bytes']!=5136175328):raise ValueError('Unapproved artifact identity')
    if not output.is_relative_to(ROOT/'runtime/runs'):raise ValueError('Local run directory only')
    if shutil.disk_usage(ROOT).free<spec['bytes']+2*1024**3:raise ValueError('Insufficient free space')
    output.mkdir(parents=True,exist_ok=False)
    (output/'candidate_spec.json').write_bytes(spec_path.read_bytes())
    shutil.copyfile(__file__,output/'fetch_source.py')
    target=output/spec['filename'];part=output/(spec['filename']+'.part')
    url=f'https://huggingface.co/{spec["artifact_repo"]}/resolve/{spec["artifact_revision"]}/{spec["filename"]}'
    command=['curl.exe','--fail','--location','--silent','--show-error',
        '--proto','=https','--proto-redir','=https','--connect-timeout','20','--max-time','1200',
        '--max-filesize',str(spec['bytes']),'--output',str(part)]
    if proxy:command+=['--proxy',proxy]
    command+=[url]
    started=time.monotonic();status='FAILED_PARTIAL_RETAINED';error=None
    with (output/'plan.json').open('x',encoding='utf-8') as f:
        json.dump({'command':command,'automatic_retry':False,'max_new_bytes':spec['bytes'],
                   'model_calls':0,'new_goal_download_cap_bytes':20*1024**3},f,indent=2)
    try:
        prior=next((p for p in (ROOT/'runtime/runs').glob('reuse-mistral-assets-*/*')
                    if p.name==spec['filename'] and p.is_file() and sha(p)==spec['sha256']),None)
        if prior:
            # The target does not exist yet. Return its verified location, no duplicate download.
            target=prior;status='EXISTING_VERIFIED_ASSET'
        else:
            with (output/'transport.log').open('xb') as log:
                process=subprocess.run(command,stdout=log,stderr=log,timeout=1210,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if process.returncode:raise RuntimeError('curl exit '+str(process.returncode))
            if part.stat().st_size!=spec['bytes'] or sha(part)!=spec['sha256']:
                raise ValueError('Downloaded size or SHA256 mismatch')
            part.rename(target);status='SHA256_VERIFIED'
    except Exception as exc:error=type(exc).__name__+': '+str(exc)
    receipt={'status':status,'wall_seconds':time.monotonic()-started,'error':error,
        'artifact':str(target) if target.exists() else None,
        'sha256':sha(target) if target.exists() else None,
        'verified_bytes':target.stat().st_size if target.exists() else 0,
        'partial_bytes':part.stat().st_size if part.exists() else 0,'model_calls':0,
        'automatic_retry':False,'new_download':status=='SHA256_VERIFIED'}
    with (output/'receipt.json').open('x',encoding='utf-8') as f:json.dump(receipt,f,indent=2)
    with (output/'index.json').open('x',encoding='utf-8') as f:
        json.dump({p.name:sha(p) for p in output.iterdir() if p.is_file() and p.name!='index.json'},f,indent=2)
    print(json.dumps(receipt,indent=2))
    if status=='FAILED_PARTIAL_RETAINED':raise SystemExit(1)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--proxy',choices=['http://127.0.0.1:7897'])
    a=p.parse_args();fetch(a.spec,a.output,a.proxy)
