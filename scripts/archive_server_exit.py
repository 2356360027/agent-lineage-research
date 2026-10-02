"""Create a private, allow-listed experiment backup before server expiry.

No home directories, credentials, shell history, Git config, or model weights.
Run on the research server. Keep the resulting archive PRIVATE.
"""
import argparse
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time

CODE_DIRS = ('agent-lineage-research', 'dependency-probe-1fc99df',
    'semantic-probe-4336c4f', 'timing-probe-b001d86', 'routing-probe-cd05595',
    'comprehension-probe-0aaf1fb', 'comprehension-holdout-595d919',
    'format-diagnostic-v1', 'format-diagnostic-v2', 'readiness-pipeline-v1',
    'order-diagnostic-v1', 'id-wording-v1', 'stage-diagnostic-v1', 'scaffold-v1',
    'tool-relay-v1', 'duplicate-control-v1', 'lineage-utility-v1',
    'origin-representation-v1', 'prompt-id-bridge-v1', 'tool-input-audit-v1', 'tool-input-repair-v1', 'anchored-communication-v1', 'decision-interface-v1', 'interface-replication-v1')
MODEL_FILES = ('config.json','configuration.json','generation_config.json',
    'merges.txt','model.safetensors.index.json','tokenizer.json',
    'tokenizer_config.json','vocab.json','LICENSE','README.md')

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();base=Path(a.base).resolve();out=Path(a.output)
    if out.exists(): raise FileExistsError('Refuse archive overwrite')
    chosen=set()
    for name in CODE_DIRS:
        root=base/name
        if not root.is_dir(): continue
        for part in ('runtime','tests','scripts','configs'):
            if (root/part).exists(): chosen.update(x for x in (root/part).rglob('*') if x.is_file())
        chosen.update(root.glob('EXPERIMENT*.md'))
        for small in ('.gitignore','.gitattributes'):
            if (root/small).is_file():chosen.add(root/small)
    prefixes=('dependency-','semantic-','timing-','routing-','comprehension-',
        'format-','readiness-','order-','id-wording-','stage-','scaffold-',
        'tool-relay-','duplicate-control-','lineage-utility-','origin-representation-','prompt-id-bridge-','tool-input-audit-','tool-input-repair-','anchored-communication-','decision-interface-','interface-replication-')
    chosen.update(x for x in base.glob('*.tar.gz') if x.name.startswith(prefixes))
    model=base/'model-cache/Qwen/Qwen2.5-7B-Instruct'
    for name in MODEL_FILES:
        if (model/name).is_file():chosen.add(model/name)
    manifest={}; skipped=[]
    for path in sorted(chosen):
        rel=path.relative_to(base).as_posix()
        if '__pycache__' in path.parts or path.suffix in ('.pyc','.key','.pem') or any(s.startswith('.env') for s in path.parts):
            skipped.append(rel);continue
        if path.is_symlink(): raise ValueError('Unexpected file symlink: '+rel)
        if not path.resolve().is_relative_to(base): raise ValueError('Outside backup base')
        manifest[rel]=dict(size=path.stat().st_size,sha256=sha(path))
    metadata=dict(created_unix=time.time(),python=sys.version,
        packages=sorted([dict(name=d.metadata.get('Name','unknown'),version=d.version) for d in importlib.metadata.distributions()],key=lambda x:x['name']),
        scope='Private experiment data, frozen source, launch logs, source archives, model config and tokenizers; no model weights or credentials',
        files=manifest,skipped=skipped)
    for command,name in ((['nvidia-smi'],'gpu'),(['uname','-a'],'os')):
        metadata[name]=subprocess.run(command,text=True,capture_output=True,check=False).stdout
    def add_json(tf,name,obj):
        raw=json.dumps(obj,indent=2,ensure_ascii=False).encode();info=tarfile.TarInfo(name);info.size=len(raw);tf.addfile(info,io.BytesIO(raw))
    with tarfile.open(out,'x:gz') as tf:
        for rel,rec in manifest.items():
            path=base/rel
            if path.stat().st_size!=rec['size'] or sha(path)!=rec['sha256']: raise RuntimeError('File changed: '+rel)
            tf.add(path,arcname='server-exit/'+rel,recursive=False)
        add_json(tf,'server-exit/MANIFEST.json',metadata)
    print(json.dumps(dict(archive=out.name,sha256=sha(out),files=len(manifest),uncompressed_bytes=sum(r['size'] for r in manifest.values()),archive_bytes=out.stat().st_size)))

if __name__=='__main__':main()
