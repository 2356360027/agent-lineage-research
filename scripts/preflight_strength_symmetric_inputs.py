"""Fixed local cache/SQL/tokenizer preflight. No network, key or execute mode."""
import argparse
from copy import deepcopy
import itertools
import json
from pathlib import Path
import shutil
import sys

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_tools import valid_result
from runtime.cache_intent_observation_bound import serialize
from runtime.cache_intent_staged_loop import TERMINAL_INSTRUCTION
from runtime.cache_action_preview import payload
from runtime.cache_route_symmetric_capacity import SymmetricCapacity
from runtime.cache_route_symmetric_design import audit as audit_design
from runtime.cache_route_symmetric_tools import ToolSession
from scripts.fetch_bird_fixed import absent, no_link_ancestors, read_json
from scripts.run_cache_action import verify_archive
from scripts.run_cache_workflow import counter_for, OLD_MANIFEST_SHA

DESIGN_SEAL='731d6bc3808543c24d09ee0c397269c7dff52227ad83f33b9e1d8ab890322191'
BINDING_SEAL='ce8129b72256856eb8d15a8775086c0c945d8bb198758ac9b72fbd9bf7dac649'
DATABASE_SHA='75e94a2c3236ee3bb2c01fb97a1c4b4c1c269bcefd4eab1d04be323d2d0825b1'


def typed_bound(raw):
    rows=[[audit.typed(value) for value in row] for row in raw['rows']]
    return dict(status='EXECUTED',complete=True,columns=raw['columns'],rows=rows,
                sql_sha256=audit.digest(raw['sql']),**audit.fingerprints(rows))


def run(project,output):
    project=Path(project).resolve();output=Path(output).absolute()
    no_link_ancestors(output);absent(output)
    design=project/'runtime/runs/strength-route-design-20261025-001'
    binding=project/'runtime/runs/strength-clarified-binding-20261025-001'
    verify_archive(design,DESIGN_SEAL);verify_archive(binding,BINDING_SEAL)
    common=read_json(design/'PRIVATE_common.json');plan=read_json(design/'PRIVATE_plan.json')
    jobs=plan['jobs']
    if sorted(j['cell_index'] for j in jobs)!=[0,1,2,3]:raise ValueError('Cell identities changed')
    audit_design([j['envelope'] for j in sorted(jobs,key=lambda j:j['cell_index'])],common)
    definition=read_json(binding/'design.json')
    raw_history=read_json(binding/'history.json');raw_current=read_json(binding/'current.json')
    if (common['history_sql']!=raw_history['sql'] or
            raw_history['sql']!=definition['queries']['history'] or
            raw_current['sql']!=definition['queries']['current'] or
            common['database_sha256']!=DATABASE_SHA or definition['database_sha256']!=DATABASE_SHA):
        raise ValueError('SQL or snapshot binding differs')
    reference=typed_bound(raw_current);cache=typed_bound(raw_history)
    valid_result(cache);valid_result(reference)
    db=project/'runtime/runs/datasets/superhero-fixed-20261025-002/databases/superhero/superhero.sqlite'
    no_link_ancestors(db)
    if audit.hash_file(db)!=DATABASE_SHA or any(Path(str(db)+s).exists() for s in ('-wal','-shm','-journal')):
        raise ValueError('Database changed or sidecar present')
    tokenizer_manifest=project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450/prepared_manifest.json'
    if audit.hash_file(tokenizer_manifest)!=OLD_MANIFEST_SHA:raise ValueError('Tokenizer manifest changed')
    old=read_json(tokenizer_manifest);counter=counter_for(old)
    capacities=[dict(id=j['id'],**SymmetricCapacity(j['envelope'],common,counter).certificate()) for j in jobs]
    output.mkdir(parents=True,exist_ok=False)
    queries=0
    try:
        audit.save_new(output/'inputs.json',dict(design_seal=DESIGN_SEAL,binding_seal=BINDING_SEAL,
            plan_sha256=audit.hash_file(design/'PRIVATE_plan.json'),database_sha256=DATABASE_SHA,
            tokenizer_manifest_sha256=OLD_MANIFEST_SHA,
            tokenizer_files={k:old[k] for k in ('tokenizer','tokenizer_dependencies','tokenizer_package_files')},
            purpose='LOCAL_INTERFACE_PREFLIGHT_NOT_MODEL_RESEARCH',model_calls=0))
        audit.save_new(output/'PRIVATE_common.json',common)
        audit.save_new(output/'PRIVATE_plan.json',plan)
        audit.save_new(output/'PRIVATE_cache.json',cache)
        audit.save_new(output/'PRIVATE_reference.json',reference)
        for label,raw,expected in [('history',raw_history,cache),('current',raw_current,reference)]:
            audit.save_new(output/(label+'.pending.json'),dict(sql=raw['sql'],automatic_retry=False))
            queries+=1
            result=audit.run_child(db,raw['sql'])
            audit.save_new(output/(label+'.response.json'),result)
            valid_result(result)
            if any(result[key]!=expected[key] for key in ('columns','rows','bag_sha256','sequence_sha256')):
                raise ValueError('Isolated read-only worker differs from bound output')
        fits=[];scripted_histories=[]
        for job in jobs:
            envelope=job['envelope'];state=json.loads(envelope['messages'][1]['content'])['context']
            cap=SymmetricCapacity(envelope,common,counter)
            session=ToolSession(database_path=db,database_sha256=DATABASE_SHA,
                history_sql=common['history_sql'],cached_result=cache,
                input_receipt={'design_seal':DESIGN_SEAL,'binding_seal':BINDING_SEAL},
                output=output/'tools'/job['id'],materialized_available=state['materialized_available'],
                cache_read_cost=state['read_cost'])
            try:
                observed=session.call('read_cache',{'cache_id':'v000'})
                exposed=cap.observation_bound().apply(observed)
                audit.save_new(output/(job['id']+'.exposure.json'),exposed)
                if state['materialized_available'] and exposed['observation']!=observed:
                    raise ValueError('Complete cache does not fit; preserve failed preflight')
                fits.append(dict(id=job['id'],available=state['materialized_available'],
                    framed_reference_tokens=exposed['exposed_framed_tokens'],
                    complete=exposed['observation']['complete'],truncated=False))
            finally:
                closure=session.close()
            if closure['status']!='TOOL_SESSION_CLOSED':raise ValueError('Tool preflight not sealed')
            # Counter-only scripted paths. These are not model outputs or live
            # tool dispatches; include all 8 sequences, no selection by effect.
            for sequence in itertools.product(('read_cache','run_sql'),repeat=3):
                messages=deepcopy(envelope['messages']);balance=60;counts=[]
                for generation in range(1,5):
                    if generation==4:messages.append(dict(role='user',content=TERMINAL_INSTRUCTION))
                    counts.append(cap(payload(messages))['reference_input_tokens'])
                    if generation==4:break
                    tool=sequence[generation-1]
                    action=dict(kind=tool,**({'cache_id':'v000'} if tool=='read_cache' else {'sql':raw_current['sql']}))
                    content=json.dumps(action,ensure_ascii=False)
                    available=tool=='run_sql' or state['materialized_available']
                    cost=(10 if tool=='run_sql' else state['read_cost']) if available else 0
                    balance-=cost
                    obs=dict(status='EXECUTED' if available else 'CACHE_UNAVAILABLE',complete=available,
                             charged_units=cost,remaining_budget=balance,attempt=generation)
                    if available:
                        result=reference if tool=='run_sql' else cache
                        obs.update(columns=result['columns'],rows=result['rows'])
                    exposure=cap.observation_bound().apply(obs)
                    messages.extend([dict(role='assistant',content=content),
                                     dict(role='user',content=serialize(exposure['observation']))])
                scripted_histories.append(dict(cell=job['id'],sequence=sequence,counts=counts,
                                               scripted_not_model=True))
        if audit.hash_file(db)!=DATABASE_SHA:raise ValueError('Database changed during preflight')
        audit.save_new(output/'capacities.json',capacities)
        audit.save_new(output/'scripted-history-counts.json',scripted_histories)
        summary=dict(status='LOCAL_INPUT_TOOL_TOKEN_PREFLIGHT_PASSED',
            readonly_SQL_attempts=queries,cache_tool_attempts=4,model_calls=0,research_samples=0,
            real_cache_rows=len(cache['rows']),cache_fits=fits,scripted_paths=len(scripted_histories),
            total_conditional_input_tokens=sum(c['total_input_bound'] for c in capacities),
            total_conditional_output_tokens=sum(c['total_output_bound'] for c in capacities),
            maximum_history_plus_output=max(c['full_history_with_output_bound'] for c in capacities),
            local_window=32768,provider_tokenizer_identity_proven=False,
            fee_quote_frozen=False,paid_execution_authorized=False,
            limits='Conditional reference-token checks; no evidence about model behavior or method improvement.')
        audit.save_new(output/'summary.json',summary)
        return summary
    except BaseException as exc:
        audit.save_new(output/'failure.json',dict(error_type=type(exc).__name__,
                       readonly_SQL_attempts=queries,model_calls=0,automatic_retry=False))
        raise
    finally:
        sources={Path(__file__).resolve()}
        for module in tuple(sys.modules.values()):
            filename=getattr(module,'__file__',None)
            if filename:
                path=Path(filename).resolve()
                if path.is_relative_to(project) and path.suffix=='.py':sources.add(path)
        for source in sources:
            target=output/'source'/source.relative_to(project)
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        audit.save_new(output/'artifact_hashes.json',{
            p.relative_to(output).as_posix():audit.hash_file(p) for p in sorted(output.rglob('*')) if p.is_file()})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],args.output),indent=2))
