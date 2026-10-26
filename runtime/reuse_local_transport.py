"""Owned, loopback-only inference lifecycle; no credentials, external API or retry."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
import zipfile

from scripts.run_local_separation import sha,save,utc
from runtime.reuse_separation import canonical


def verify_assets(conf,spec,output):
    assets=Path(conf['assets']); runtime=Path(conf['runtime'])
    checked={}
    for entry in spec['assets']:
        path=assets/entry['name']
        if sha(path)!=entry['sha256']: raise ValueError('Pinned asset hash mismatch')
        checked[path.name]=entry['sha256']
    save(output/'assets_verified.json',checked)
    actual={p.name:sha(p) for p in sorted(runtime.iterdir())
            if p.is_file() and p.suffix.lower() in ('.exe','.dll')}
    expected={}
    for entry in spec['assets']:
        if not entry['name'].endswith('.zip'): continue
        with zipfile.ZipFile(assets/entry['name']) as archive:
            for member in archive.infolist():
                name=Path(member.filename).name
                if name not in actual: continue
                with archive.open(member) as stream:
                    digest=hashlib.file_digest(stream,'sha256').hexdigest()
                if name in expected and expected[name]!=digest:
                    raise ValueError('Conflicting runtime archives')
                expected[name]=digest
    if actual!=expected or 'llama-server.exe' not in actual:
        raise ValueError('Extracted runtime differs from pinned ZIPs')
    save(output/'runtime_verified.json',actual)


class OwnedLocal:
    def __init__(self,conf,output):
        self.conf=conf; self.output=Path(output)
        self.proc=None; self.log=None; self.started=None; self.ready=None; self.ended=None
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self,path,payload=None,timeout=60):
        if not path.startswith('/') or '://' in path: raise ValueError('Loopback path only')
        remaining=self.conf['server_seconds_limit']-(time.perf_counter()-self.started)
        if remaining<=0: raise TimeoutError('Owned service time budget exhausted')
        req=urllib.request.Request(f'http://127.0.0.1:{self.conf["port"]}'+path,
            data=None if payload is None else canonical(payload).encode('utf-8'),
            headers={'Content-Type':'application/json'})
        with self.opener.open(req,timeout=min(timeout,remaining)) as response:
            return json.load(response)

    def start(self):
        conf=self.conf; exe=Path(conf['runtime'])/'llama-server.exe'
        with socket.socket() as sock: sock.bind(('127.0.0.1',conf['port']))
        for name,cmd in (
            ('version',[str(exe),'--version']),
            ('gpu_before',['nvidia-smi','--query-gpu=name,memory.total,memory.used,driver_version','--format=csv,noheader'])):
            p=subprocess.run(cmd,capture_output=True,timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            save(self.output/(name+'.json'),{'returncode':p.returncode,
                'stdout':p.stdout.decode(errors='replace'),'stderr':p.stderr.decode(errors='replace')})
            if p.returncode: raise RuntimeError(name+' preflight failed')
        command=[str(exe),'--model',str(Path(conf['assets'])/conf['model_file']),
            '--alias',conf['model_alias'],'--host','127.0.0.1','--port',str(conf['port']),
            '--ctx-size',str(conf['context_size']),'--gpu-layers','99','--parallel','1',
            '--no-webui','--no-context-shift','--no-cache-prompt','--cache-ram','0']
        save(self.output/'launch.json',{'command':command,'utc':utc()})
        self.log=(self.output/'server.log').open('xb')
        self.started=time.perf_counter()
        self.proc=subprocess.Popen(command,stdout=self.log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        while time.perf_counter()-self.started<120:
            if self.proc.poll() is not None: raise RuntimeError('Owned service exited in startup')
            try: healthy=self.request('/health',timeout=2).get('status')=='ok'
            except (OSError,ValueError): healthy=False
            if healthy: break
            time.sleep(.5)
        else: raise TimeoutError('Startup deadline')
        self.ready=time.perf_counter()
        models=self.request('/v1/models');save(self.output/'models.json',models)
        save(self.output/'props.json',self.request('/props'))
        if conf['model_alias'] not in [m['id'] for m in models['data']]:
            raise ValueError('Served alias mismatch')
        gpu=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,memory.used','--format=csv,noheader'],
            capture_output=True,timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        save(self.output/'gpu_loaded.json',{'returncode':gpu.returncode,
            'stdout':gpu.stdout.decode(errors='replace'),'stderr':gpu.stderr.decode(errors='replace'),
            'measurement':'Snapshot after model load, not peak memory or energy'})
        if gpu.returncode:raise RuntimeError('Loaded GPU inspection failed')

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill();self.proc.wait(timeout=5)
        self.ended=time.perf_counter()
        if self.log is not None:self.log.close()
        return {'owned_server_stopped':self.proc is None or self.proc.poll() is not None,
            'owned_server_seconds':0 if self.started is None else self.ended-self.started,
            'cold_start_seconds':None if self.ready is None else self.ready-self.started,
            'energy_measured':False,'prefix_cache_requested':False}


def validate_usage(raw,prompt_tokens,context_size,max_tokens):
    usage=raw.get('usage',{})
    if type(usage.get('prompt_tokens')) is not int or usage['prompt_tokens']!=prompt_tokens:
        raise ValueError('Token count differs from preflight; stop, do not truncate/retry')
    if type(usage.get('completion_tokens')) is not int or not 0<=usage['completion_tokens']<=max_tokens:
        raise ValueError('Invalid completion usage')
    if prompt_tokens+max_tokens+64>context_size: raise ValueError('Context overflow')
    if raw.get('timings',{}).get('cache_n',0)!=0:
        raise ValueError('Unexpected shared prompt caching')
    return usage
