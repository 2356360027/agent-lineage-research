"""Advisory-value vs controlled verdict susceptibility, using real model calls.

Generation never uses oracle posteriors or gold to choose peer verdicts. Artificial
0/1 verdict treatments are logged as interventions, never as natural peer outputs.
"""
import argparse
import json
import random
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
from .e1 import Client, canonical, digest, save, world, messages
from .native import verify


SYSTEM = (
    "Infer a hidden binary state Y with prior P(Y=1)=0.5. Each source emits "
    "one binary observation equal to Y with its stated reliability, symmetrically "
    "for Y=0 and Y=1. Distinct sources are conditionally independent given Y. "
    "Repeated observation IDs are the same observation, not new evidence. "
    "Peer assessments are advisory judgments, may contain mistakes, and do not "
    "include observations beyond those in the observations list. "
    "Return only a JSON object with answer (integer 0 or 1), p_state_1 (number "
    "between 0 and 1), and citations (list of visible observation IDs)."
)

ARMS = ['hidden', 'natural', 'injected_zero', 'injected_one', 'hidden_repeat']
PRIMARY_ENDPOINTS = ['natural_minus_hidden_oracle_squared_error',
                     'injected_one_minus_zero_probability']


def validate_design(design):
    if design.get('protocol') != 'dependency-probe-v1':
        raise ValueError('Unsupported protocol')
    if design.get('arms') != ARMS or design.get('primary_endpoints') != PRIMARY_ENDPOINTS:
        raise ValueError('Protocol arms/endpoints changed; requires a new protocol')
    for stage in ['explore', 'validate']:
        phase = design[stage]
        if any(type(phase[k]) is not int or phase[k] < 1 for k in ['items', 'repetitions']):
            raise ValueError('Positive integer items/repetitions required')
        if type(phase['world_seed']) is not int:
            raise ValueError('Integer world seed required')
    if design['explore']['world_seed'] == design['validate']['world_seed']:
        raise ValueError('Exploration and validation require distinct world seeds')
    if type(design['bootstrap_resamples']) is not int or design['bootstrap_resamples'] < 100:
        raise ValueError('At least 100 bootstrap resamples required')
    if type(design['bootstrap_seed']) is not int:
        raise ValueError('Integer bootstrap seed required')
    if not 0 <= design['temperature'] <= 2 or not 0 < design['top_p'] <= 1:
        raise ValueError('Invalid sampling settings')
    if type(design['max_tokens']) is not int or design['max_tokens'] < 1:
        raise ValueError('Invalid token limit')


def configure(native, design, stage):
    validate_design(design)
    if native.get('deployment') != 'native':
        raise ValueError('Requires native fingerprinted configuration')
    phase = design[stage]
    return dict(native, items=phase['items'], repetitions=phase['repetitions'],
                seed=phase['world_seed'], arms=design['arms'],
                temperature=design['temperature'], top_p=design['top_p'],
                max_tokens=design['max_tokens'], status=f'DEPENDENCY_PROBE_{stage.upper()}')


def check_server(cfg, model_list):
    matches = [m for m in model_list['data'] if m['id'] == cfg['model']]
    if len(matches) != 1 or Path(matches[0]['root']).resolve() != Path(cfg['model_path']).resolve():
        raise ValueError('Live model endpoint/path mismatch')
    if matches[0].get('max_model_len') != cfg['server_settings_reported']['max_model_len']:
        raise ValueError('Live context length mismatch')


def final_messages(observations, previous, peers):
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': canonical({
        'observations': observations, 'your_previous_assessment': previous,
        'peer_assessments': peers})}]


def call_seed(seed, item, rep, role):
    return int(digest(['dependency-probe-v1', seed, item, rep, role])[:7], 16)


def run_item(client, cfg, item, directory):
    obs, _ = world(cfg['seed'], item)
    focal = item % 3
    other = [a for a in range(3) if a != focal]
    initial = [client.call(f'i{item}-s0-{a}', messages([o]),
                          call_seed(cfg['seed'],item,-1,f's0-{a}'), {o['id']})
               for a,o in enumerate(obs)]
    evidence = [obs[focal]] + [obs[a] for a in other]
    natural = [{'agent': f'peer{k}', 'answer': initial[a]['answer']}
               for k,a in enumerate(other)]
    peers = {'hidden': [], 'hidden_repeat': [], 'natural': natural,
             'injected_zero': [{'agent': f'peer{k}', 'answer': 0} for k in range(2)],
             'injected_one': [{'agent': f'peer{k}', 'answer': 1} for k in range(2)]}
    schedules = [(r,a) for r in range(cfg['repetitions']) for a in cfg['arms']]
    random.Random(call_seed(cfg['seed'],item,-1,'schedule')).shuffle(schedules)
    results = {str(r): {} for r in range(cfg['repetitions'])}
    for rep,arm in schedules:
        # Matched seeds across treatments; the repeat control deliberately uses
        # an independent seed. This does not guarantee GPU determinism.
        role = 'repeat' if arm == 'hidden_repeat' else 'paired'
        answer = client.call(f'i{item}-r{rep}-{arm}',
            final_messages(evidence,initial[focal],peers[arm]),
            call_seed(cfg['seed'],item,rep,role), {o['id'] for o in evidence})
        results[str(rep)][arm] = answer
    result = {'item': item, 'focal': focal, 'initial': initial,
              'observations': evidence, 'evidence_hash': digest(evidence),
              'natural_peers': natural, 'schedule': schedules, 'results': results}
    save(directory / f'result-{item}.json',result)
    return result


def sources():
    return {p.name: p.read_text(encoding='utf-8') for p in sorted(Path(__file__).parent.glob('*.py'))}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native-config', default='runtime/runs/native-smoke-config.json')
    p.add_argument('--design', default='configs/dependency_probe_v1.json')
    p.add_argument('--stage', choices=['explore','validate'], default='explore')
    p.add_argument('--output', default='runtime/runs/dependency-explore-001')
    p.add_argument('--shards',type=int,default=1)
    p.add_argument('--shard',type=int,default=0)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--confirm-frozen-validation',action='store_true')
    args=p.parse_args()
    if not 0 <= args.shard < args.shards: p.error('Invalid shard')
    design=json.loads(Path(args.design).read_text(encoding='utf-8'))
    cfg=configure(json.loads(Path(args.native_config).read_text(encoding='utf-8')),design,args.stage)
    if args.shards > cfg['items']: p.error('More workers than items')
    indices=list(range(args.shard,cfg['items'],args.shards))
    planned=len(indices)*(3+len(cfg['arms'])*cfg['repetitions'])
    print(json.dumps({'mode':'execute' if args.execute else 'plan_no_inference',
        'stage':args.stage,'items_on_worker':len(indices),'planned_calls':planned,
        'design_sha256':digest(design)},indent=2),flush=True)
    if not args.execute: return
    if args.stage=='validate' and not args.confirm_frozen_validation:
        p.error('Inspect the frozen design first; validation requires --confirm-frozen-validation')
    directory=Path(args.output)/f'shard-{args.shard:03d}'
    directory.mkdir(parents=True,exist_ok=True)
    lock=directory/'.running.lock'
    with lock.open('x') as f: f.write(str(time.time()))
    try:
        source_bundle=sources()
        run_spec={'config':cfg,'design':design,'stage':args.stage,'shards':args.shards,
                  'source_sha256':digest(source_bundle)}
        manifest_path=directory/'manifest.json'
        if manifest_path.exists():
            manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
            if manifest['run_spec']!=run_spec or manifest['shard']!=args.shard:
                raise ValueError('Configuration/source changed; preserve this run and choose a new directory')
        else:
            manifest={'run_spec':run_spec,'shard':args.shard,'python':sys.version,
                      'created_unix':time.time(),'source_bundle':source_bundle}
            for label,command in [('git_commit',['git','rev-parse','HEAD']),
                                  ('git_status',['git','status','--porcelain']),
                                  ('gpu',['nvidia-smi'])]:
                try: manifest[label]=subprocess.check_output(command,text=True,stderr=subprocess.STDOUT)
                except (OSError,subprocess.CalledProcessError): manifest[label]='unavailable'
            save(manifest_path,manifest)
        verify(cfg)
        with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as r:
            model_list=json.load(r)
        check_server(cfg,model_list)
        if not (directory/'server_metadata.json').exists(): save(directory/'server_metadata.json',model_list)
        # Each entry is a check of reported metadata, not an attestation of memory
        # contents. Keep server logs as separate provenance evidence.
        metadata_dir=directory/'endpoint_checks'
        metadata_dir.mkdir(exist_ok=True)
        save(metadata_dir/f'{time.time_ns()}.json',{'checked_unix':time.time(),'metadata':model_list})
        client=Client(cfg,'http://127.0.0.1:8000/v1',directory)
        for item in indices:
            run_item(client,cfg,item,directory)
            print(f'Completed item {item}; planned worker items={len(indices)}',flush=True)
        print('Worker completed. Run python -m runtime.probe_score --run '+args.output,flush=True)
    finally:
        lock.unlink()


if __name__=='__main__': main()
