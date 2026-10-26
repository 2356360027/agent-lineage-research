"""24 fixed local-only first responses: 6 families x sufficient/lossy x prices.
Formal synthetic contracts, no human-NL benchmark or novel-method comparison.
Owns and stops its local llama server. No credentials, network downloads or API fees.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

MODEL = 'local-qwen25-7b-q5'
PORT = 18089
BASE = f'http://127.0.0.1:{PORT}'
SYSTEM = ('Choose a plan for the CURRENT request, not the historical request. '
    'All cache provenance and snapshots are trusted. The cache is exactly the '
    'specified historical result: no hidden columns, metadata or extra constraints. '
    'Decide whether it suffices for ALL databases permitted by the stated contracts, '
    'not merely by coincidence in one snapshot. Correctness takes priority. '
    'Among guaranteed-correct plans choose the lower declared total cost. '
    'RECOMPUTE runs a trusted correct current query; REUSE uses only the cached '
    'result with exact local arithmetic/filtering at zero extra declared cost. '
    'Return JSON only: {"action":"REUSE" or "RECOMPUTE","reason":"short justification"}. '
    'Costs are experimental units, not measured latency or money.')


def cases():
    pairs = [
      ('entity_grain', 'Count all qualifying entities by unique nonnull ID.',
       'Complete DISTINCT names of qualifying entities. Different IDs may share names.',
       'Complete DISTINCT IDs of all qualifying entities.'),
      ('null_denominator', 'AVG(score) across all posts; ignore NULL score; empty nonnull set returns NULL.',
       'COUNT(*) of all posts; score is nullable and unrestricted numeric.',
       'SUM(score) and COUNT(score) over all posts; exact numeric arithmetic; score nullable.'),
      ('group_coverage', 'Count all Gold-group rows; group is a nonnull label.',
       'Only the single largest group and its count, ties resolved lexicographically. Other groups are omitted.',
       'Complete counts for EVERY group, including Gold if present. Missing group means zero rows.'),
      ('time_scope', 'Count rows whose year is 2026.',
       'Complete count of rows whose year is 2025. Years are unconstrained integers.',
       'Complete row counts grouped by year for ALL years. Missing year means zero rows.'),
      ('join_multiplicity', 'Count DISTINCT members attending any event named X. Event names need not be unique.',
       'COUNT(*) of matching attendance edges. An edge is unique per event/member; a member can attend several X events.',
       'Complete DISTINCT member IDs attending any event named X; no truncation.'),
      ('maximum_ties', 'Return ALL IDs tied at the greatest nonnull score; empty list if no nonnull score.',
       'One ID at the greatest nonnull score (ties broken by smallest ID), or empty. Scores can tie.',
       'Complete list of unique ID and nullable numeric score for all entities; no truncation.'),
    ]
    result=[]
    for family, current, lossy, sufficient in pairs:
        for available_info, history in ((False,lossy),(True,sufficient)):
            for price in (1,20):
                key=f'{family}-s{int(available_info)}-p{price}'
                data=dict(current_request=current, historical_result=history,
                          cache_available=True, cache_total_cost=price, recompute_total_cost=10)
                result.append(dict(id=key,family=family,sufficient=available_info,
                    expected='REUSE' if available_info and price<10 else 'RECOMPUTE',
                    messages=[dict(role='system',content=SYSTEM),
                              dict(role='user',content=json.dumps(data))]))
    return sorted(result,key=lambda c:hashlib.sha256(('local-calibration-v1'+c['id']).encode()).hexdigest())


def sha_file(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(4*1024**2),b''):h.update(block)
    return h.hexdigest()


def run(assets,runtime,output):
    jobs=cases()
    if len(jobs)!=24:raise ValueError('Fixed scope violated')
    output.mkdir(parents=True,exist_ok=False)
    def save(name,obj):
        with (output/name).open('x',encoding='utf-8') as f:json.dump(obj,f,indent=2)
    save('plan.json',dict(created_at=datetime.now(timezone.utc).isoformat(),cases=jobs,
        max_calls=24,max_tokens=128,temperature=0,seed=20261027,deadline_seconds=600,
        synthetic_formal_contracts=True,novel_method_test=False,API_cost_CNY=0,
        one_shot=True,automatic_retry=False,backend='llama.cpp b11850 Q5_K_M'))
    (output/'source.py').write_bytes(Path(__file__).read_bytes())
    manifest=json.loads((assets/'manifest.json').read_bytes())
    verified={}
    for a in manifest['assets']:
        path=assets/a['name']
        actual=sha_file(path)
        if actual!=a['sha256']:raise ValueError('Asset SHA256 mismatch')
        verified[a['name']]=actual
    save('verified_assets.json',verified)
    executable=runtime/'llama-server.exe'
    if not executable.is_file():raise ValueError('Missing local runtime')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',PORT))
    model=assets/'qwen2.5-7b-instruct-q5_k_m-00001-of-00002.gguf'
    cmd=[str(executable.resolve()),'--model',str(model.resolve()),'--alias',MODEL,
         '--host','127.0.0.1','--port',str(PORT),'--ctx-size','4096',
         '--gpu-layers','99','--parallel','1','--no-webui']
    save('launch.json',dict(command=cmd,executable_sha256=sha_file(executable)))
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path,payload=None,timeout=5):
        req=urllib.request.Request(BASE+path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=timeout) as response:return json.load(response)
    observations=[]
    started=time.monotonic()
    status='FAILED'
    with (output/'server.log').open('xb') as log:
        proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,
             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            ready=False
            while time.monotonic()-started<120:
                if proc.poll() is not None:raise RuntimeError('Local server exited')
                try:
                    ready=request('/health').get('status')=='ok'
                except (OSError,ValueError):pass
                if ready:break
                time.sleep(0.5)
            if not ready:raise TimeoutError('Startup limit')
            models=request('/v1/models')
            save('models.json',models)
            if MODEL not in [m['id'] for m in models['data']]:raise ValueError('Wrong model')
            for i,job in enumerate(jobs):
                remaining=600-(time.monotonic()-started)
                if remaining<=0:raise TimeoutError('Batch limit')
                payload=dict(model=MODEL,messages=job['messages'],max_tokens=128,
                             temperature=0,seed=20261027,stream=False)
                save(f'{i:02d}.request.json',payload)
                t=time.monotonic()
                raw=request('/v1/chat/completions',payload,timeout=min(60,remaining))
                save(f'{i:02d}.response.json',raw)
                choice=raw['choices'][0]
                try:
                    parsed=json.loads(choice['message']['content'])
                    action=parsed['action']
                    if action not in ('REUSE','RECOMPUTE'):raise ValueError('Invalid action')
                except (TypeError,KeyError,ValueError):action='INVALID'
                if choice.get('finish_reason')!='stop':action='TRUNCATED_OR_NONSTOP'
                observations.append(dict(id=job['id'],family=job['family'],
                    sufficient=job['sufficient'],expected=job['expected'],action=action,
                    correct=action==job['expected'],seconds=time.monotonic()-t,
                    usage=raw.get('usage')))
                save(f'{i:02d}.score.json',observations[-1])
                print(f'Completed {i+1}/24 {job["id"]}',flush=True)
            status='COMPLETED_LOCAL_MODEL_CALIBRATION'
        except Exception as exc:
            save('failure.json',dict(error_type=type(exc).__name__,detail=str(exc)))
        finally:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
    summary=dict(status=status,completed_calls=len(observations),rows=observations,
        correct=sum(r['correct'] for r in observations),
        unsafe_reuse=sum(r['action']=='REUSE' and not r['sufficient'] for r in observations),
        api_cost_cny=0,elapsed_seconds=time.monotonic()-started,owned_server_stopped=True,
        independent_human_tasks=0,interpretation='Synthetic formal interface/economic calibration only. '
        'Not evidence of natural-language drift or a new method improvement.')
    save('summary.json',summary)
    save('sha256.json',{p.name:sha_file(p) for p in output.iterdir() if p.is_file()})
    print(json.dumps({k:v for k,v in summary.items() if k!='rows'},indent=2))
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets',required=True,type=Path)
    p.add_argument('--runtime',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    run(a.assets,a.runtime,a.output)
