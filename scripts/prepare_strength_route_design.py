"""Offline source-bound proposal only. No credentials, network or execute mode."""
import argparse
import hashlib
import json
from pathlib import Path
from runtime.cache_route_symmetric_design import build,audit,VERSION

DRAFT_SHA='3172efd672168824195ac05d62e07a97c2f0b2cfafaf8f5b7953f63372f015d5'
BINDING_SEAL='ce8129b72256856eb8d15a8775086c0c945d8bb198758ac9b72fbd9bf7dac649'
SCHEMA_SHA='352bb81ba26df7b6ed461a01def44db37ae670af23d18c788cd6f7b13411844a'


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(project,output):
    project,output=Path(project),Path(output)
    if output.exists(): raise ValueError('Refuse overwrite')
    draft=project/'reports/cache_relation_review_20261025/CLARIFIED_PAIR_REVIEW_V1.txt'
    binding=project/'runtime/runs/strength-clarified-binding-20261025-001'
    schema_file=project/'runtime/runs/cache-full-schema-witnesses-20261025-001/766-786.json'
    if sha(draft)!=DRAFT_SHA or sha(binding/'artifact_hashes.json')!=BINDING_SEAL or sha(schema_file)!=SCHEMA_SHA:
        raise ValueError('Source identity mismatch')
    seals=json.loads((binding/'artifact_hashes.json').read_text('utf-8'))
    if any(sha(binding/name)!=value for name,value in seals.items()):
        raise ValueError('Binding archive modified')
    text=draft.read_text('utf-8')
    sections=['一、两个新任务共用的明确约定','二、历史任务：','三、当前任务：','四、拟议使用方式及限制']
    starts=[text.index(s) for s in sections]
    if starts!=sorted(starts) or any(text.count(s)!=1 for s in sections):
        raise ValueError('Ambiguous draft sections')
    bound=json.loads((binding/'design.json').read_text('utf-8'))
    common=dict(shared_contract=text[starts[0]:starts[1]].strip(),
        history_contract=text[starts[1]:starts[2]].strip(),current_contract=text[starts[2]:starts[3]].strip(),
        schema=json.loads(schema_file.read_text('utf-8'))['schema'],
        history_sql=bound['queries']['history'],database_sha256=bound['database_sha256'])
    cells=build(common); checks=audit(cells,common)
    seed='strength-symmetric-route-development-v1'
    jobs=[dict(id=f'cell-{i}',cell_index=i,envelope=c) for i,c in enumerate(cells)]
    jobs.sort(key=lambda j:hashlib.sha256((seed+j['id']).encode()).hexdigest())
    policy=dict(stage='DEVELOPMENT_INTERFACE_CHECK_NOT_CONFIRMATION',independent_task_pairs=1,
        dependent_conditions=4,primary_descriptive_endpoints=['first_action','final_answer_status'],
        secondary=['cache_read_count','SQL_attempt_count','declared_tool_units','model_usage','latency','failure_and_oversize'],
        final_answer_rule='Exact unambiguous canonical scalar equality to bound private reference; complex/prose answers pending human review, not automatically wrong',
        include_all_conditions=True,failed_or_missing_not_imputed=True,inferential_statistics=False,
        c_type_requirement_drift_assessed=False,prior_task_contract_elicited=False,
        failures_preserved=True,automatic_retry=False,automatic_followup=False,
        old_positive_control_comparison='Context only; changed horizon/cost levels prohibit pooling as matched replication',
        interpretation='No-route-change/all-correct outcomes retained. Any error is not itself demand reinterpretation or novel method evidence.')
    plan=dict(status='OFFLINE_DESIGN_ONLY_NOT_EXECUTABLE',protocol=VERSION,seed=seed,jobs=jobs,
        source_draft_sha256=DRAFT_SHA,binding_seal=BINDING_SEAL,checks=checks,readout=policy,
        limits=dict(max_calls=16,max_tool_attempts=12,max_generations_per_cell=4,
            max_output_tokens_proposed=512,observation_tokens_proposed=1024,model_seconds_proposed=600),
        monetary_limit_cny=None,model_and_endpoint='NOT_FROZEN',paid_execution_authorized=False,
        remaining=['Symmetric real ToolSession and loop with durable logging',
            'Complete-cache observation fit and token/fee certificate',
            'Frozen official model/settings, current pricing, launcher and approval',
            'Read-only, safety, partial-failure and replay tests'])
    output.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        with (output/name).open('x',encoding='utf-8') as f: json.dump(value,f,ensure_ascii=False,indent=2)
    save('PRIVATE_common.json',common);save('PRIVATE_plan.json',plan);save('audit.json',checks)
    for name in ('scripts/prepare_strength_route_design.py','runtime/cache_route_symmetric_design.py','tests/test_cache_route_symmetric_design.py'):
        dest=output/'source'/name;dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes((project/name).read_bytes())
    save('artifact_hashes.json',{p.relative_to(output).as_posix():sha(p) for p in sorted(output.rglob('*')) if p.is_file()})
    return dict(status=plan['status'],cells=len(cells),factor_audit=checks['status'],model_calls=0,
        plan_sha256=sha(output/'PRIVATE_plan.json'),seal_sha256=sha(output/'artifact_hashes.json'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();print(json.dumps(run(Path(__file__).resolve().parents[1],args.output),indent=2))
