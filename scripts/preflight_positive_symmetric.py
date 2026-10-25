"""Local positive-control protocol preflight; scripted responses, no paid API."""
import argparse
from copy import deepcopy
import itertools
import json
from pathlib import Path
import shutil
import sys
import time

from runtime import bird_readonly_audit as audit
from runtime.cache_route_symmetric_tools import ToolSession
from runtime.cache_route_symmetric_capacity import SymmetricCapacity
from runtime.cache_route_symmetric_loop import run as run_loop
from runtime.cache_intent_staged_loop import TERMINAL_INSTRUCTION
from runtime.cache_intent_observation_bound import serialize
from runtime.cache_intent_journal import Journal, verify as verify_journal
from runtime.cache_intent_tools import valid_result
from runtime.cache_action_preview import payload
from scripts.run_cache_action import verify_archive
from scripts.run_cache_workflow import counter_for, OLD_MANIFEST_SHA
from scripts.fetch_bird_fixed import absent, no_link_ancestors, read_json
from scripts.audit_superhero_interface import observation
from scripts.prepare_positive_symmetric_inputs import DATABASE_SHA

INPUT_SEAL = '3c5de085cae60ced24f7615dbf7a2a734e5715520e4c69003ab42cff933331c0'


def scripted_paths(job, common, cache, reference, sql, counter):
    """All eight three-tool sequences; counter-only, never model outputs."""
    cap = SymmetricCapacity(job['envelope'],common,counter)
    context = json.loads(job['envelope']['messages'][1]['content'])['context']
    results = []
    for sequence in itertools.product(('read_cache','run_sql'),repeat=3):
        messages = deepcopy(job['envelope']['messages']); balance = 60; counts = []
        for generation in range(1,5):
            if generation == 4: messages.append(dict(role='user',content=TERMINAL_INSTRUCTION))
            counts.append(cap(payload(messages))['reference_input_tokens'])
            if generation == 4: break
            name = sequence[generation-1]
            action = dict(kind=name,**({'cache_id':'v000'} if name=='read_cache' else {'sql':sql}))
            available = name=='run_sql' or context['materialized_available']
            cost = (10 if name=='run_sql' else context['read_cost']) if available else 0
            balance -= cost
            if available:
                obs = observation(cache if name=='read_cache' else reference,cost,balance,generation)
            else:
                obs = dict(status='CACHE_UNAVAILABLE',complete=False,charged_units=0,
                    remaining_budget=balance,attempt=generation)
            exposed = cap.observation_bound().apply(obs)
            if exposed['observation'] != obs: raise ValueError('Complete observation exceeded bound')
            messages.extend([dict(role='assistant',content=json.dumps(action)),
                             dict(role='user',content=serialize(obs))])
        results.append(dict(sequence=list(sequence),input_counts=counts,remaining_budget=balance,
                            scripted_not_model=True))
    return results


def run(project, output):
    project = Path(project).resolve(); output = Path(output).absolute()
    no_link_ancestors(output); absent(output)
    inputs = project/'runtime/runs/positive-symmetric-inputs-20261025-001'
    verify_archive(inputs,INPUT_SEAL)
    common = read_json(inputs/'PRIVATE_common.json'); plan = read_json(inputs/'PRIVATE_plan.json')
    cache = read_json(inputs/'PRIVATE_cache.json'); reference = read_json(inputs/'PRIVATE_reference.json')
    queries = read_json(inputs/'PRIVATE_source_queries.json')
    valid_result(cache); valid_result(reference)
    db = project/'runtime/runs/datasets/superhero-fixed-20261025-002/databases/superhero/superhero.sqlite'
    no_link_ancestors(db)
    if audit.hash_file(db)!=DATABASE_SHA: raise ValueError('Database changed')
    oldpath = project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450/prepared_manifest.json'
    if audit.hash_file(oldpath)!=OLD_MANIFEST_SHA: raise ValueError('Tokenizer metadata changed')
    old = read_json(oldpath); counter = counter_for(old)
    output.mkdir(parents=True,exist_ok=False)
    sql_attempts = 0; rows = []; paths = []
    try:
        audit.save_new(output/'binding.json',dict(input_seal=INPUT_SEAL,database_sha256=DATABASE_SHA,
            tokenizer_manifest_sha256=OLD_MANIFEST_SHA,tokenizer_files={k:old[k] for k in
            ('tokenizer','tokenizer_dependencies','tokenizer_package_files')},model_calls=0))
        audit.save_new(output/'history.pending.json',dict(sql=common['history_sql'],automatic_retry=False))
        sql_attempts += 1
        history = audit.run_child(db,common['history_sql'])
        audit.save_new(output/'history.response.json',history); valid_result(history)
        for field in ('columns','rows','bag_sha256','sequence_sha256'):
            if history[field]!=cache[field]: raise ValueError('Cache differs from actual readonly query')
        for job in plan['jobs']:
            context = json.loads(job['envelope']['messages'][1]['content'])['context']
            cap = SymmetricCapacity(job['envelope'],common,counter)
            paths.append(dict(id=job['id'],paths=scripted_paths(job,common,cache,reference,queries['current_733'],counter)))
            for suffix,sequence in [('third-cache',('read_cache','run_sql','read_cache')),
                                    ('third-sql',('run_sql','read_cache','run_sql'))]:
                directory = output/'scripted'/job['id']/suffix
                trace = Journal(directory/'trace')
                session = ToolSession(database_path=db,database_sha256=DATABASE_SHA,
                    history_sql=common['history_sql'],cached_result=cache,input_receipt=dict(input_seal=INPUT_SEAL,scripted_not_model=True),
                    output=directory/'tools',materialized_available=context['materialized_available'],cache_read_cost=context['read_cost'])
                def generate(request):
                    n = int(request['generation_id'][1:])
                    cap(payload(request['messages']))
                    if n==4: action=dict(kind='abstain',text='Scripted software preflight, not a model answer.')
                    else:
                        tool=sequence[n-1]
                        action=dict(kind=tool,**({'cache_id':'v000'} if tool=='read_cache' else {'sql':queries['current_733']}))
                    return dict(content=json.dumps(action),finish_reason='stop')
                try:
                    result = run_loop(job['envelope'],common,session,generate,trace,cap.observation_bound(),deadline=time.monotonic()+60)
                finally:
                    closure=session.close(); sql_attempts+=closure['sql_calls']; trace.seal()
                verify_journal(directory/'tools'); verify_journal(directory/'trace')
                if closure['status']!='TOOL_SESSION_CLOSED' or closure['attempts']!=3:
                    raise ValueError('Scripted session did not complete all three opportunities')
                for n,tool in enumerate(sequence,1):
                    obs=read_json(directory/'trace'/f't{n:03d}.response.json')['observation']
                    if tool=='run_sql' or context['materialized_available']:
                        expected=reference if tool=='run_sql' else cache
                        if obs['status']!='EXECUTED' or obs['columns']!=expected['columns'] or obs['rows']!=expected['rows']:
                            raise ValueError('Live tool result differs from sealed expectation')
                    elif obs['status']!='CACHE_UNAVAILABLE' or 'rows' in obs:
                        raise ValueError('Unavailable cache leaked content')
                rows.append(dict(id=job['id'],route=suffix,closure=closure,loop=result,scripted_not_model=True))
        if audit.hash_file(db)!=DATABASE_SHA: raise ValueError('Database changed during preflight')
        audit.save_new(output/'scripted-capacity-paths.json',paths)
        summary=dict(status='LOCAL_POSITIVE_SYMMETRIC_PREFLIGHT_PASSED',model_calls=0,research_samples=0,
            readonly_SQL_attempts=sql_attempts,scripted_tool_attempts=sum(r['closure']['attempts'] for r in rows),
            scripted_live_sessions=len(rows),counter_only_sequences=sum(len(p['paths']) for p in paths),
            database_unchanged=True,complete_observations=True,paid_execution_authorized=False,rows=rows)
        audit.save_new(output/'summary.json',summary)
        return {k:v for k,v in summary.items() if k!='rows'}
    except BaseException as exc:
        audit.save_new(output/'failure.json',dict(error_type=type(exc).__name__,model_calls=0,automatic_retry=False))
        raise
    finally:
        sources={Path(__file__).resolve()}
        for module in tuple(sys.modules.values()):
            name=getattr(module,'__file__',None)
            if name:
                path=Path(name).resolve()
                if path.is_relative_to(project) and path.suffix=='.py':sources.add(path)
        for path in sources:
            dest=output/'source'/path.relative_to(project);dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,dest)
        audit.save_new(output/'artifact_hashes.json',{p.relative_to(output).as_posix():audit.hash_file(p)
            for p in sorted(output.rglob('*')) if p.is_file()})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],args.output),indent=2))
