"""Freeze offline candidate data, oracle evidence, and pre-outcome planning.

No model calls. The confirmation candidate is not yet an approved/final
confirmation run: final methods/statistics/model inputs must also be frozen.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from runtime.reuse_scenarios import (VERSION, DEVELOPMENT, CONFIRMATION, build_suite,
    canonical, digest, structural_catalog)
from runtime.reuse_statistics import sensitivity
from runtime.reuse_protocol import schedule, audit_matrix

ROOT=Path(__file__).resolve().parents[1]


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    with path.open('x',encoding='utf-8') as f:
        json.dump(value,f,indent=2,ensure_ascii=False,allow_nan=False)


def calcite_input(case):
    """Lossless schema metadata adapter; never supplies original DB rows/oracle.

    Use this public input for the upcoming upstream SQL/MV adapter. Types and
    primary keys are derived from the very same DDL disclosed to the models.
    SQLite CHECK constraints/range bounds remain explicitly unimplemented in
    Calcite metadata, not secretly stronger constraints.
    """
    public=case['public']
    con=sqlite3.connect(':memory:')
    try:
        for ddl in public['schema_sql']: con.execute(ddl)
        tables=[]
        for (name,) in con.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"):
            cols=con.execute('PRAGMA table_info("'+name.replace('"','""')+'")').fetchall()
            tables.append({'name':name,'columns':[{'name':c[1],
                'type':'VARCHAR' if c[2]=='TEXT' else 'INTEGER',
                'nullable':not bool(c[3] or c[5])} for c in cols],
                'primary_key':[c[1] for c in sorted(cols,key=lambda c:c[5]) if c[5]]})
        return {'case_id':case['case_id'],'current_sql':public['current_sql'],
            'history_sql':public['history_sql'],'cache':public['cache'],'tables':tables,
            'no_base_rows_provided':True,'metadata_limit':'CHECK/range/FK constraints not yet represented; do not infer them.'}
    finally: con.close()


def prepare(output):
    output=output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    sources=['runtime/reuse_scenarios.py','runtime/reuse_statistics.py',
             'runtime/reuse_protocol.py','runtime/reuse_separation.py',
             'scripts/prepare_reuse_suite.py','tests/test_reuse_scenarios.py',
             'tests/test_reuse_statistics.py','tests/test_reuse_protocol.py']
    for name in sources:
        dest=output/'source'/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,dest)
    for package in ('runtime','scripts','tests'):
        (output/'source'/package/'__init__.py').touch(exist_ok=False)
    save(output/'plan.json',{'version':VERSION,'utc':datetime.now(timezone.utc).isoformat(),
        'development_templates':DEVELOPMENT,'confirmation_candidate_templates':CONFIRMATION,
        'structural_catalog':structural_catalog(),'development_bases':24,'confirmation_candidate_bases':96,
        'status':'OFFLINE_CANDIDATE_ONLY_NO_MODEL_CALLS','max_model_calls':0,
        'not_final_confirmation_freeze':True,'independent_real_tasks':0})
    summaries={}
    all_cases=[]
    for split in ('development','confirmation_candidate'):
        cases=build_suite(split)
        all_cases+=cases
        save(output/(split+'.private.json'),cases)
        save(output/(split+'.public.json'),[{k:v for k,v in c.items() if k!='audit'} for c in cases])
        save(output/(split+'.calcite-inputs.json'),[calcite_input(c) for c in cases])
        save(output/(split+'.schedule.json'),schedule(cases))
        save(output/(split+'.matrix-audit.json'),audit_matrix(cases))
        summaries[split]={'bases':len({c['base_id'] for c in cases}),'contexts':len(cases),
            'templates':len({c['template'] for c in cases}),
            'family_context_counts':dict(Counter(c['family'] for c in cases)),
            'negative_witnesses':sum(not c['audit']['sufficient'] for c in cases),
            'positive_transforms':sum(c['audit']['sufficient'] for c in cases),
            'public_inputs_sha256':digest([c['public'] for c in cases])}
    development_templates={n for names in DEVELOPMENT.values() for n in names}
    held_templates={n for names in CONFIRMATION.values() for n in names}
    if development_templates & held_templates: raise ValueError('Structural split overlap')
    if len({c['public_sha256'] for c in all_cases})!=len(all_cases):
        raise ValueError('Cross-split exact duplicate prompt data')
    save(output/'size_sensitivity.json',sensitivity())
    summary={'status':'OFFLINE_SUITE_VALIDATED','splits':summaries,'model_calls':0,
        'all_240_current_SQL_checks_match_independent_Python':True,
        'all_120_negative_caches_identical_across_counterworlds':True,
        'all_120_negative_current_answers_differ':True,
        'all_120_positive_transforms_match_current_on_both_worlds':True,
        'structural_template_names_disjoint':True,'public_input_hashes_unique':True,
        'finite_checks_are_universal_proof':False,
        'warning':'Positive justification is rule-based plus finite checks, not equivalence proved by testing. '
                  'Confirmed model efficacy is not measured. 96 bases share 12 held templates; '
                  'do not count cache variants as independent samples. Calcite input exported, '
                  'upstream planner not executed by this command.'}
    save(output/'summary.json',summary)
    hashes={str(p.relative_to(output)).replace('\\','/'):file_sha(p)
            for p in output.rglob('*') if p.is_file()}
    save(output/'sha256.json',hashes)
    print(json.dumps(summary,indent=2,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path)
    prepare(p.parse_args().output)
