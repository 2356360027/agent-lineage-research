"""Archive local scripted software checks; no model or research DB execution."""
import argparse
import io
from pathlib import Path
import shutil
import tempfile
import unittest

from runtime.bird_readonly_audit import save_new, hash_file
from scripts.fetch_bird_fixed import absent

MODULES = (
    'tests.test_cache_route_symmetric_tools', 'tests.test_cache_route_symmetric_design',
    'tests.test_cache_route_symmetric_loop', 'tests.test_cache_intent_tools',
    'tests.test_cache_route_symmetric_capacity',
    'tests.test_cache_route_symmetric_adapter', 'tests.test_cache_route_symmetric_batch',
    'tests.test_cache_route_symmetric_readout', 'tests.test_superhero_answer_readout',
    'tests.test_cache_route_symmetric_replay',
    'tests.test_cache_action_horizon', 'tests.test_cache_action_factorial_preview',
    'tests.test_factorial_loop',
)


def verify(output):
    root=Path(__file__).resolve().parents[1]
    output=Path(output).resolve()
    absent(output); output.mkdir(parents=True,exist_ok=False)
    fixture_root=output/'temporary-fixtures'; fixture_root.mkdir()
    previous=tempfile.tempdir
    stream=io.StringIO()
    try:
        # Explicit workspace fixture location; do not change OS permissions.
        tempfile.tempdir=str(fixture_root)
        suite=unittest.defaultTestLoader.loadTestsFromNames(MODULES)
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    finally:
        tempfile.tempdir=previous
        save_new(output/'test-log.json',dict(log=stream.getvalue()))
    # Archive loaded project modules, not just top-level test names.
    import sys
    sources={Path(__file__).resolve()}
    for module in tuple(sys.modules.values()):
        filename=getattr(module,'__file__',None)
        if filename:
            path=Path(filename).resolve()
            if path.is_relative_to(root) and path.suffix=='.py': sources.add(path)
    hashes={}
    for path in sorted(sources):
        relative=path.relative_to(root)
        target=output/'source'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
        hashes[relative.as_posix()]=hash_file(target)
        if hashes[relative.as_posix()]!=hash_file(path): raise ValueError('Source changed')
    summary=dict(status='SOFTWARE_TESTS_PASSED' if result.wasSuccessful() else 'SOFTWARE_TESTS_FAILED',
                 tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                 modules=list(MODULES),source_sha256=hashes,model_calls=0,research_samples=0,
                 actual_research_database_used=False,
                 caveat='Scripted software tests do not establish agent behavior, provider fit or method benefit.')
    save_new(output/'summary.json',summary)
    files={p.relative_to(output).as_posix():hash_file(p) for p in sorted(output.rglob('*')) if p.is_file()}
    save_new(output/'artifact_hashes.json',files)
    for relative,digest in files.items():
        if hash_file(output/relative)!=digest: raise ValueError('Archive mismatch')
    print(summary['status'], 'tests=',result.testsRun, 'files=',len(files))
    print('MANIFEST_SHA256:',hash_file(output/'artifact_hashes.json'))
    return result.wasSuccessful()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    raise SystemExit(0 if verify(args.output) else 1)
