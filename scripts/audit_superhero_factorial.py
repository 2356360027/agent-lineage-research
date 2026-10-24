"""Standalone offline audit/score/archive of the complete approved batch.

No model/SQL execution. Uses verified frozen execution dependencies and explicitly
identified analysis files. Unsealed/stopped batches require a failure audit and
are never filtered down to successful cells. All output remains private/local.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

REFERENCE_SEAL='164a6dd7fd2662a709ebec4188775a4d4f574de37bc4483ea598056485a21415'
ANALYSIS=('scripts/audit_superhero_factorial.py','runtime/cache_action_trace_audit.py',
          'runtime/factorial_trace_replay.py','runtime/superhero_answer_readout.py')


def require(value,message):
    if not value: raise ValueError(message)


def read(path): return json.loads(Path(path).read_text('utf-8'))


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def no_links(path):
    path=Path(path).absolute()
    for p in (path,*path.parents):
        require(not p.is_symlink() and not p.is_junction(),'Links/junctions not allowed')


def snapshot(root):
    root=Path(root); no_links(root)
    entries=list(root.rglob('*'))
    for p in entries: no_links(p)
    return {p.relative_to(root).as_posix():sha(p) for p in entries if p.is_file()}


def precheck(root,expected_sha):
    root=Path(root); no_links(root)
    require(sha(root/'manifest.json')==expected_sha,'Wrong prepared manifest')
    manifest=read(root/'manifest.json')
    require(snapshot(root/'source')==manifest['source_files'],'Frozen source differs')
    exp=root/'experiment'
    require((exp/'artifact_hashes.json').is_file(),'No completed seal; do not inspect effects')
    files=snapshot(exp); seal=files.pop('artifact_hashes.json')
    require(files==read(exp/'artifact_hashes.json'),'Experiment inventory differs')
    summary=read(exp/'summary.json')
    require(summary.get('status')=='OFFICIAL_DEVELOPMENT_FINISHED' and summary.get('stop_reason') is None,
            'Not a finished official batch; preserve for failure audit')
    claim=read(root/'execution_claim.json')
    require(claim.get('manifest_sha256')==expected_sha and claim.get('approved_cny')==.20
            and claim.get('automatic_retry') is False,'Execution claim differs')
    outcome=read(root/'launch_outcome.json')
    require(outcome.get('status')==summary['status'] and outcome.get('dispatch_attempts')==summary['dispatch_attempts'],
            'Parent completion outcome missing or inconsistent')
    return manifest,seal


def load_analysis(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module; spec.loader.exec_module(module)
    return module


def audit_run(root,expected_sha,reference):
    require(not any(n in ('runtime','scripts') or n.startswith(('runtime.','scripts.')) for n in sys.modules),
            'Use a standalone process, not mixed project imports')
    root=Path(root).resolve(); reference=Path(reference).resolve()
    before=snapshot(root); reference_before=snapshot(reference)
    manifest,seal=precheck(root,expected_sha)
    require(reference_before.get('artifact_hashes.json')==REFERENCE_SEAL,'Reference archive seal differs')
    require({k:v for k,v in reference_before.items() if k!='artifact_hashes.json'}==read(reference/'artifact_hashes.json'),
            'Reference archive bytes differ')
    project=Path(__file__).resolve().parents[1]
    analysis_hashes={name:sha(project/name) for name in ANALYSIS}
    sys.path.insert(0,str(root/'source'))
    from scripts.run_superhero_factorial import verify
    _,plan,cache,counter=verify(root,expected_sha)
    old=load_analysis('runtime.cache_action_trace_audit',project/ANALYSIS[1])
    checker=load_analysis('_factorial_analysis_replay',project/ANALYSIS[2])
    scorer=load_analysis('_factorial_analysis_scorer',project/ANALYSIS[3])
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('runtime.','scripts.')) and getattr(module,'__file__',None):
            location=Path(module.__file__).resolve()
            if module is old:
                require(sha(location)==analysis_hashes[ANALYSIS[1]],'Analysis helper changed')
            else:
                require(location.is_relative_to(root/'source') and
                        location.relative_to(root/'source').as_posix() in manifest['source_files'],
                        'Unverified mixed execution import')
    replay=checker.finished_batch(root/'experiment',plan,cache,counter,
        expected_plan_sha=manifest['plan_sha256'],mode='OFFICIAL_DEVELOPMENT',
        ceiling_micro_cny=manifest['ceiling_micro_cny'],input_rate=2,output_rate=8)
    require(replay['experiment_manifest_sha256']==seal,'Experiment seal changed')
    reference_value=scorer.reference_integer(read(reference/'current_733.json'))
    scored=scorer.summarize(replay['rows'],reference_value)
    verify(root,expected_sha)
    require(snapshot(root)==before and snapshot(reference)==reference_before,'Artifacts changed during audit')
    require(all(sha(project/n)==h for n,h in analysis_hashes.items()),'Analysis code changed during audit')
    result=dict(status='OFFLINE_REPLAY_AND_AUTOMATED_READOUT_COMPLETE',replay=replay,readout=scored,
        prepared_manifest_sha256=expected_sha,reference_archive_sha256=REFERENCE_SEAL,
        analysis_source_files=analysis_hashes,new_API_calls=0,new_SQL_executions=0,
        new_human_reviews=0,C_drift_assessed=False)
    return result,before,reference_before


def archive(root,reference,output,result,before,reference_before):
    root,reference,output=map(lambda p:Path(p).resolve(),(root,reference,output))
    no_links(output)
    require(not output.exists(),'Never overwrite audit output')
    require(not output.is_relative_to(root) and not output.is_relative_to(reference),'Output must be outside inputs')
    require(snapshot(root)==before and snapshot(reference)==reference_before,'Input changed before archive')
    project=Path(__file__).resolve().parents[1]
    sources=result['analysis_source_files']
    require(set(sources)==set(ANALYSIS),'Unexpected analysis source set')
    for name,digest in sources.items():
        no_links(project/name); require(sha(project/name)==digest,'Analysis source changed')
    output.mkdir(parents=True,exist_ok=False)
    zip_path=output/'prepared-results-and-reference.zip'
    expected={**{'prepared/'+n:h for n,h in before.items()},
              **{'reference/'+n:h for n,h in reference_before.items()},
              **{'analysis-source/'+n:h for n,h in sources.items()}}
    with zipfile.ZipFile(zip_path,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for prefix,folder,inventory in (('prepared',root,before),('reference',reference,reference_before),
                                         ('analysis-source',project,sources)):
            for name in sorted(inventory): z.write(folder/name,prefix+'/'+name)
    with zipfile.ZipFile(zip_path) as z:
        require(z.testzip() is None and len(z.namelist())==len(expected) and set(z.namelist())==set(expected),
                'Archive CRC or inventory differs')
        for name,digest in expected.items():
            require(hashlib.sha256(z.read(name)).hexdigest()==digest,'Archived bytes differ')
    require(snapshot(root)==before and snapshot(reference)==reference_before,'Inputs changed during archive')
    result=dict(result,backup=dict(file=zip_path.name,sha256=sha(zip_path),verified_files=len(expected),
        off_device=False,external_dataset_and_tokenizer_included=False))
    with (output/'audit.json').open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
    hashes=snapshot(output)
    with (output/'artifact_hashes.json').open('x',encoding='utf-8') as f: json.dump(hashes,f,sort_keys=True,indent=2)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared',type=Path,required=True); p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--reference',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(); no_links(args.output); require(not args.output.exists(),'Never overwrite audit output')
    result,before,reference_before=audit_run(args.prepared,args.manifest_sha256,args.reference)
    result=archive(args.prepared,args.reference,args.output,result,before,reference_before)
    print(json.dumps(dict(status=result['status'],counts=result['readout']['counts'],
        backup=result['backup'],new_API_calls=0,new_SQL_executions=0,new_human_reviews=0),indent=2))


if __name__=='__main__': main()
