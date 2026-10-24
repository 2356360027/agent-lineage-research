"""Standalone offline audit/archive of a sealed, finished action batch.

Never executes the model launcher, performs inference or runs SQL. Interrupted
or unsealed runs are retained and refused, not filtered to successful prefixes.
Raw answers remain in the private output directory; console output is compact.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile


def require(value, message):
    if not value: raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def no_links(path):
    path=Path(path).absolute()
    for item in (path,*path.parents):
        require(not item.is_symlink() and not item.is_junction(), 'Links/junctions are not audit inputs')


def snapshot(root):
    root=Path(root); no_links(root)
    entries=list(root.rglob('*'))
    for path in entries: no_links(path)
    return {p.relative_to(root).as_posix():sha(p) for p in entries if p.is_file()}


def precheck(root, expected_sha):
    """Check identity/seal/status before loading research effect records."""
    root=Path(root); no_links(root)
    require(sha(root/'manifest.json')==expected_sha, 'Wrong prepared manifest')
    manifest=read(root/'manifest.json')
    require(snapshot(root/'source')==manifest['source_files'], 'Frozen source inventory differs')
    exp=root/'experiment'
    require((exp/'artifact_hashes.json').is_file(), 'No completed seal; do not inspect effects')
    actual=snapshot(exp); seal=actual.pop('artifact_hashes.json')
    require(actual==read(exp/'artifact_hashes.json'), 'Experiment manifest differs')
    summary=read(exp/'summary.json')
    require(summary.get('status')=='OFFICIAL_DEVELOPMENT_FINISHED' and summary.get('stop_reason') is None,
            'Not a finished official batch; preserve for separate failure audit')
    claim=read(root/'execution_claim.json')
    require(claim.get('manifest_sha256')==expected_sha and claim.get('approved_cny')==.50
            and claim.get('automatic_retry') is False, 'Execution claim differs')
    return manifest,seal


def audit_run(root, expected_sha):
    root=Path(root).absolute()
    require(not any(n=='runtime' or n=='scripts' or n.startswith(('runtime.','scripts.')) for n in sys.modules),
            'Use the standalone script to avoid mixed research-code imports')
    before=snapshot(root)
    manifest,seal=precheck(root,expected_sha)
    sys.path.insert(0,str(root/'source'))
    from scripts.run_cache_action import verify
    _,plan,caches,counter=verify(root,expected_sha)
    # The new offline checker is analysis code, not part of the frozen runner.
    # Load only this file from the working project; all its runtime dependencies
    # must come from the already verified frozen source tree.
    checker=Path(__file__).absolute().parents[1]/'runtime/cache_action_trace_audit.py'
    spec=importlib.util.spec_from_file_location('_action_offline_trace_audit',checker)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    for name,loaded in tuple(sys.modules.items()):
        if name.startswith(('runtime.','scripts.')) and getattr(loaded,'__file__',None):
            location=Path(loaded.__file__).resolve()
            require(location.is_relative_to((root/'source').resolve()), 'Mixed execution-source imports')
            require(location.relative_to(root/'source').as_posix() in manifest['source_files'],
                    'Unlisted execution dependency')
    result=module.finished_batch(root/'experiment',plan,caches,counter,
        expected_plan_sha256=manifest['plan_sha256'],mode='OFFICIAL_DEVELOPMENT')
    require(result['experiment_manifest_sha256']==seal, 'Seal changed during replay')
    verify(root,expected_sha)
    require(snapshot(root)==before, 'Prepared artifacts changed during audit')
    result.update(prepared_manifest_sha256=expected_sha,
        analysis_source_files={'scripts/audit_cache_action.py':sha(__file__),
                               'runtime/cache_action_trace_audit.py':sha(checker)},
        source_and_inputs_verified=True,
        audit_scope='Finished-batch local consistency and replay; final answers not semantically graded.')
    return result,before


def archive(root, output, result, expected_snapshot):
    root,output=Path(root).absolute(),Path(output).absolute()
    no_links(root); no_links(output)
    require(not output.exists(), 'Never overwrite audit output')
    require(not output.is_relative_to(root), 'Audit output must be outside the prepared batch')
    require(snapshot(root)==expected_snapshot, 'Artifacts changed before archive')
    output.mkdir(parents=True,exist_ok=False)
    destination=output/'prepared-and-results.zip'
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name in sorted(expected_snapshot): z.write(root/name,name)
    with zipfile.ZipFile(destination) as z:
        require(z.testzip() is None, 'Archive CRC mismatch')
        require(len(z.namelist())==len(expected_snapshot) and set(z.namelist())==set(expected_snapshot),
                'Archive inventory mismatch')
        for name,digest in expected_snapshot.items():
            require(hashlib.sha256(z.read(name)).hexdigest()==digest, 'Archived bytes differ')
    require(snapshot(root)==expected_snapshot, 'Artifacts changed during archive')
    result=dict(result,backup=dict(file=destination.name,sha256=sha(destination),
        verified_files=len(expected_snapshot),off_device=False,
        external_dataset_and_tokenizer_included=False))
    project=Path(__file__).absolute().parents[1]
    for name,digest in result.get('analysis_source_files',{}).items():
        require(name in ('scripts/audit_cache_action.py','runtime/cache_action_trace_audit.py',
                        'scripts/audit_cache_action_failure.py'),
                'Unexpected analysis source path')
        source=project/name; no_links(source)
        require(sha(source)==digest,'Analysis source changed before archival')
        target=output/'analysis-source'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as handle: handle.write(source.read_bytes())
        require(sha(target)==digest,'Analysis source copy differs')
    with (output/'audit.json').open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
    output_hashes=snapshot(output)
    with (output/'artifact_hashes.json').open('x',encoding='utf-8') as f:
        json.dump(output_hashes,f,sort_keys=True,indent=2)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,required=True)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    no_links(args.output)
    require(not args.output.exists(), 'Never overwrite audit output')
    result,before=audit_run(args.prepared,args.manifest_sha256)
    result=archive(args.prepared,args.output,result,before)
    print(json.dumps(dict(status=result['status'],output=str(args.output),
        trajectories=len(result['rows']),cost_guard=result['cost_guard'],
        semantic_labels=0,new_API_calls=0,new_SQL_executions=0,backup=result['backup']),indent=2))


if __name__=='__main__': main()
