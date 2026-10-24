"""Fetch pinned official TEXT tokenizer reference only; never execute it.

No weights, inference calls, credentials, remote-code import or package install.
This public reference is a candidate counter, not proof of API backend identity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

REVISION = '6821d6ad3681a4b137b066b76094fa82ebd0a380'
REPO = 'deepseek-ai/DeepSeek-V4-Flash-Vision-Exp'
FILES = ('LICENSE','encoding/README.md','encoding/encoding_dsv4.py',
         'tokenizer.json','tokenizer_config.json')


def fetch(output, proxy=None):
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    manifest={'repo':REPO,'revision':REVISION,'files':{},'inference_calls':0,
              'reference_model_is_not_verified_api_revision':True, 'proxy':proxy}
    try:
        for name in FILES:
            url=f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}'
            if proxy:
                raw=subprocess.check_output(['curl.exe','--fail','--location','--silent',
                    '--show-error','--proto','=https','--proto-redir','=https',
                    '--proxy',proxy,'--connect-timeout','20','--max-time','90',
                    '--max-filesize',str(32*1024*1024),url],timeout=95)
            else:
                with urllib.request.urlopen(url,timeout=30) as response:
                    raw=response.read(32*1024*1024+1)
            if len(raw)>32*1024*1024:
                raise ValueError('Reference file exceeds size limit')
            path=output/name
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as stream: stream.write(raw)
            manifest['files'][name]={'url':url,'bytes':len(raw),
                                     'sha256':hashlib.sha256(raw).hexdigest()}
            print(json.dumps({'file':name,**manifest['files'][name]}),flush=True)
        manifest['status']='DOWNLOADED_NOT_EXECUTED'
    except Exception as exc:
        manifest.update(status='FAILED_PARTIAL_RETAINED',error_type=type(exc).__name__)
        raise
    finally:
        with (output/'manifest.json').open('x',encoding='utf-8') as stream:
            json.dump(manifest,stream,indent=2)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--proxy',choices=['http://127.0.0.1:7897'])
    args=parser.parse_args()
    print(json.dumps(fetch(args.output,args.proxy),indent=2))
