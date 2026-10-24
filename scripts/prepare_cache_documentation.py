"""Offline provenance check and input freeze. NO execution option or API calls."""
import argparse
import json
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime.cache_documentation_inputs import build, DOCUMENTATION, HISTORIES
from runtime.cache_intent_tools import valid_result
from scripts.prepare_staged_contract_review import load_archive
from scripts.audit_deepseek_cache_controls import INPUT_SHA, PREPARED_SHA
from scripts.run_cache_workflow import counter_for, FILES

ORIGINAL_SHA = '6ee741f4935bcfe2b30688401ecebf83b658a38b847b026b4a360746d7a048e7'


def prepare(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Never overwrite a frozen input package')
    original = project/'runtime/runs/cache-intent-dev-package-20261012-001'
    prior = project/'runtime/runs/deepseek-recovery-prepared-20261024-181730-649450'
    load_archive(original, ORIGINAL_SHA)
    load_archive(prior/'inputs', INPUT_SHA)
    if audit.hash_file(prior/'prepared_manifest.json') != PREPARED_SHA:
        raise ValueError('Prior preparation changed')
    def read(p):
        return json.loads(p.read_text('utf-8'))
    manifest = read(prior/'prepared_manifest.json')
    before = read(original/'started.json')['database_hashes_before']
    after = read(original/'database_hashes_after.json')
    if (before != after or before['toxicology'] != manifest['database_sha256'] or
            audit.hash_file(manifest['database']) != manifest['database_sha256']):
        raise ValueError('Historical/current database fingerprint differs')
    cases = {c['history']['id']:c for c in read(original/'PRIVATE_cases.json')}
    caches = read(prior/'inputs/PRIVATE_caches.json')
    plan = read(prior/'inputs/PRIVATE_plan.json')
    provenance = []
    for hid in HISTORIES:
        result = read(original/(hid+'.result.json'))
        valid_result(result)
        history = cases[hid]['history']
        job, = [j for j in plan['jobs'] if j['history_id']==hid and j['read_price']==1]
        visible = json.loads(job['envelope']['messages'][1]['content'])
        if (result != caches[hid] or result['sql_sha256'] != audit.digest(history['sql']) or
                job['cached_result_sha256'] != audit.digest(result) or
                visible['history'] != {k:history[k] for k in ('historical_request','sql')} or
                visible['context']['database_sha256'] != before['toxicology']):
            raise ValueError('Historical execution-to-prompt binding mismatch')
        provenance.append(dict(history_id=hid, result_file_sha256=audit.hash_file(original/(hid+'.result.json')),
                               sql_sha256=result['sql_sha256'], complete=result['complete'], status=result['status']))
    candidate = build(plan)
    counter = counter_for(manifest)
    sizes = [dict(id=j['id'], input_tokens=counter.chat(j['payload']['messages'])) for j in candidate['jobs']]
    if not all(0 < r['input_tokens'] <= 8192 for r in sizes):
        raise ValueError('Input bound exceeded; never truncate')
    for hid in HISTORIES:
        a, = [j for j in candidate['jobs'] if j['id']==hid+'-original']
        b, = [j for j in candidate['jobs'] if j['id']==hid+'-documented']
        restored = json.loads(json.dumps(b['payload']))
        s = restored['messages'][0]['content']
        if not s.endswith('\n\n'+DOCUMENTATION):
            raise ValueError('Documentation differs')
        restored['messages'][0]['content'] = s[:-len('\n\n'+DOCUMENTATION)]
        if restored != a['payload']:
            raise ValueError('More than documentation changed')
    output.mkdir(parents=True, exist_ok=False)
    audit.save_new(output/'PRIVATE_payloads.json', candidate)
    audit.save_new(output/'provenance.json', dict(status='LOCAL_HISTORICAL_EXECUTION_CHAIN_CHECKED',
        original_manifest_sha256=ORIGINAL_SHA, prior_input_manifest_sha256=INPUT_SHA,
        database_sha256=before['toxicology'], unchanged_before_after=True,
        current_database_rehashed=True, histories=provenance,
        limitations=['Local preserved execution records, not independent execution attestation.',
                    'Complete historical query execution is not a task-specific applicability certificate.',
                    'No new SQL execution or human confirmation performed.']))
    for name in (*FILES, 'runtime/cache_documentation_inputs.py', 'scripts/prepare_cache_documentation.py'):
        target = output/'source'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as f:
            f.write((project/name).read_bytes())
    receipt = dict(status='INPUTS_FROZEN_NOT_EXECUTABLE_NOT_AUTHORIZED', jobs=12,
        prompt_counts=sizes, maximum_calls=12, maximum_seconds=600,
        payloads_sha256=audit.hash_file(output/'PRIVATE_payloads.json'),
        documentation_sha256=audit.digest(DOCUMENTATION), model_calls=0, new_SQL_queries=0,
        historical_rate_assumption_micro_cny=[2,8],
        conditional_token_cost_micro_cny=sum(r['input_tokens']*2+512*8 for r in sizes),
        pricing_status='Historical planning rates only; recheck before execution authorization.',
        remaining=['No new execution launcher is supplied by this input-preparation script.',
                   'Freeze a bounded launcher and test failure/no-retry behavior.',
                   'Recheck official pricing, then obtain fresh fixed-batch approval.'])
    audit.save_new(output/'receipt.json', receipt)
    audit.save_new(output/'artifact_hashes.json', {p.relative_to(output).as_posix():audit.hash_file(p)
        for p in sorted(output.rglob('*')) if p.is_file()})
    return dict(**receipt, artifact_manifest_sha256=audit.hash_file(output/'artifact_hashes.json'))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parents[1], args.output), indent=2))
