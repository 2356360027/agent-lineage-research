"""Save offline first-request token receipts; never calls a provider or SQL."""
import argparse
from pathlib import Path
import shutil
import sys

from runtime import bird_readonly_audit as audit
from runtime.student_club_input_admission import load
from runtime.student_club_definition_history import DefinitionHistory
from runtime.student_club_native_counter import Counter
from runtime.student_club_native_wire import payload
from scripts.audit_cache_action import snapshot, no_links
from scripts.check_student_club_reference import sealed
from scripts.fetch_bird_fixed import read_json
from scripts.run_cache_workflow import counter_for

ASSET_MANIFEST = 'runtime/runs/native-full-prepared-20261026-001/manifest.json'
ASSET_PIN = 'd099f838f25962b1b397d60a51e15caee8d53908e7136a3526e3840bf75559ee'


def run(project, output):
    project,output=Path(project).absolute(),Path(output).absolute()
    no_links(project);no_links(output)
    if output.exists() or not output.is_relative_to(project/'runtime/runs'):
        raise ValueError('New private output directory required')
    manifest=project/ASSET_MANIFEST
    if audit.hash_file(manifest)!=ASSET_PIN:
        raise ValueError('Pinned tokenizer dependency manifest differs')
    m=read_json(manifest)
    counter_for(m)  # Verify existing dependency hashes before adding import path.
    inputs=load(project)
    counter=Counter(project/'runtime/runs/native-counter-reference-20261025-002',m['tokenizer'])
    records=[]
    for job in inputs['design']['jobs']:
        request=DefinitionHistory(inputs['common'],job,lambda *a:None).begin()
        receipt=counter.inspect(request)
        if not 0<receipt['reference_input_tokens']<=8192:
            raise ValueError('First request exceeds fixed context admission cap')
        records.append(dict(job_id=job['id'],request=request,wire=payload(request),receipt=receipt))
    output.mkdir(parents=True,exist_ok=False)
    audit.save_new(output/'PRIVATE_first_requests.json',records)
    audit.save_new(output/'input_binding.json',inputs['receipt'])
    audit.save_new(output/'asset_binding.json',dict(asset_manifest_sha256=ASSET_PIN,
        **{k:m[k] for k in ('tokenizer','tokenizer_dependencies','tokenizer_package_files')},python=sys.version))
    for rel in ('scripts/check_student_club_native_tokens.py','runtime/student_club_native_counter.py',
                'runtime/student_club_native_wire.py','runtime/student_club_definition_history.py',
                'runtime/student_club_diagnostic_design.py','runtime/student_club_input_admission.py'):
        dest=output/'source'/rel;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(project/rel,dest)
    summary=dict(status='OFFLINE_FIRST_REQUEST_REFERENCE_COUNTS_ONLY',model_calls=0,database_queries=0,
        counts={r['job_id']:r['receipt']['reference_input_tokens'] for r in records},
        provider_prompt_parity_verified=False,full_horizon_capacity_proven=False,
        inference_authorized=False,source_snapshot_scope='Changed modules only, not a live runtime freeze')
    audit.save_new(output/'summary.json',summary)
    audit.save_new(output/'artifact_hashes.json',snapshot(output))
    sealed(output,'artifact_hashes.json')
    return summary|dict(artifact_sha256=audit.hash_file(output/'artifact_hashes.json'))


if __name__=='__main__':
    import json
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],a.output),indent=2))
