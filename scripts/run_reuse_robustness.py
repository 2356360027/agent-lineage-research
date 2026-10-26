"""Freeze, run and audit the fixed144-call/model robustness followup."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts.run_local_separation import save, sha, utc
from scripts.run_reuse_development import (SOURCES as BASE_SOURCES, verify_prepared,
    verify_index, transport_messages)
from runtime.reuse_robustness import schedule, select_cases, messages, parse, audit_design
from runtime.reuse_separation import parse_response, route
from runtime.reuse_scenarios import digest
from runtime.reuse_local_transport import OwnedLocal, verify_assets, validate_usage
from runtime.reuse_mistral_transport import verify_mistral_assets, verify_rendered_instructions

SOURCES = (*BASE_SOURCES, 'runtime/reuse_robustness.py', 'scripts/run_reuse_robustness.py',
           'tests/test_reuse_robustness.py', 'scripts/export_reuse_robustness.py')


def validate(conf):
    if (conf['scope'] != 'PRESELECTED_ROBUSTNESS_ONLY' or conf['planned_calls'] != 144
        or conf['batch_sizes'] != [144] or conf['server_seconds_limit'] != 600
        or conf['context_size'] != 4096 or conf['max_tokens'] != 256
        or conf['temperature'] != 0 or conf['seed'] != 20261027
        or conf['cache_prompt'] or conf['parallel'] != 1 or conf['port'] != 18090
        or not 0 <= conf['max_attempts_new_goal_before'] <= 10000-144
        or not 0 <= conf['service_seconds_new_goal_before'] <= 72000-600
        or conf['model_family'] not in ('qwen', 'mistral')):
        raise ValueError('Robustness/resource configuration violation')


def prepare(e1, e1_manifest, audit_path, resource, output):
    e1, audit_path, resource, output = [p.resolve() for p in (e1, audit_path, resource, output)]
    if not output.is_relative_to(ROOT/'runtime/runs'): raise ValueError('New local run folder required')
    verify_prepared(e1, e1_manifest); verify_index(audit_path)
    summary = json.loads((audit_path/'summary.json').read_bytes())
    if (summary['manifest_sha256'] != e1_manifest or summary['expected_calls'] != 1920
        or summary['responses_recorded'] != 1920 or not summary['all_actual_prompts_match_freeze']
        or not summary['all_started_servers_closed'] or len(summary['terminals']) != 10
        or any(t['status'] != 'COMPLETED' for t in summary['terminals'])):
        raise ValueError('Requires sealed main matrix')
    inputs = json.loads((audit_path/'input_hashes.json').read_bytes())
    cases = json.loads((e1/'cases.private.json').read_bytes())
    design = audit_design(cases); jobs = schedule(cases)
    ids = {c['case_id'] for c in select_cases(cases)}
    baseline = []; chunks = json.loads((e1/'batches.json').read_bytes())
    for i, chunk in enumerate(chunks):
        out = e1/f'batch-{i:02d}'
        verify_index(out)
        if sha(out/'sha256.json') != inputs['batch_indices'][str(i)]:
            raise ValueError('Original matrix changed after audit')
        for j, job in enumerate(chunk):
            if job['case_id'] in ids:
                baseline.append({'job': dict(job, arm='original'),
                                 'raw': json.loads((out/f'{j:03d}.response.json').read_bytes())})
    conf = json.loads((e1/'config.json').read_bytes()); ledger = json.loads(resource.read_bytes())
    # Original Qwen freeze predates explicit model_family metadata. The pinned
    # alias/file, not a new model choice, disambiguates the historical record.
    if 'model_family' not in conf:
        if conf['model_alias'] != 'local-qwen25-7b-q5': raise ValueError('Unknown historical model')
        conf['model_family'] = 'qwen'
    conf.update(scope='PRESELECTED_ROBUSTNESS_ONLY', planned_calls=144, batch_sizes=[144],
                max_attempts_new_goal_before=ledger['goal_new_attempts_total'],
                service_seconds_new_goal_before=ledger['goal_service_seconds_total'],
                source_main_manifest=e1_manifest, source_main_summary_sha256=sha(audit_path/'summary.json'))
    validate(conf)
    output.mkdir(parents=True, exist_ok=False)
    for name in SOURCES:
        dest = output/'source'/name; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/name, dest)
    for pkg in ('scripts', 'runtime', 'tests'): (output/'source'/pkg/'__init__.py').touch(exist_ok=False)
    for name in ('asset_spec.json', 'model_spec.json', 'transport_amendment.txt', 'confirmation_plan.json'):
        if (e1/name).exists(): shutil.copyfile(e1/name, output/name)
    shutil.copyfile(resource, output/'prior_resource_record.json')
    shutil.copyfile(ROOT/'reports/reuse_separation_14day/ROBUSTNESS_SPEC_V1.txt', output/'protocol.txt')
    save(output/'cases.private.json', cases)
    save(output/'cases.public.json', [{k: v for k, v in c.items() if k != 'audit'} for c in select_cases(cases)])
    save(output/'baseline.private.json', baseline); save(output/'jobs.json', jobs)
    save(output/'design_audit.json', design); save(output/'config.json', conf)
    save(output/'manifest.json', {'scope': conf['scope'], 'files': {
        str(p.relative_to(output)).replace('\\', '/'): sha(p) for p in output.rglob('*') if p.is_file()}})
    result = {'status': 'PREPARED_NO_GENERATION', 'prepared': str(output),
              'manifest_sha256': sha(output/'manifest.json'), 'calls': 144}
    print(json.dumps(result, ensure_ascii=False, indent=2)); return result


def execute(prepared, manifest):
    prepared = prepared.resolve(); verify_prepared(prepared, manifest)
    if ROOT != prepared/'source': raise ValueError('Use frozen source')
    conf = json.loads((prepared/'config.json').read_bytes()); validate(conf)
    cases = {c['case_id']: c for c in json.loads((prepared/'cases.public.json').read_bytes())}
    jobs = json.loads((prepared/'jobs.json').read_bytes())
    if len(jobs) != 144: raise ValueError('Fixed batch size mismatch')
    out = prepared/'batch-00'; out.mkdir(exist_ok=False)
    save(out/'started.json', {'utc': utc(), 'manifest': manifest})
    local = OwnedLocal(conf, out); attempts = 0; responses = 0; status = 'FAILED'; start = time.perf_counter()
    try:
        spec = json.loads((prepared/'asset_spec.json').read_bytes())
        if conf['model_family'] == 'mistral':
            verify_mistral_assets(conf, json.loads((prepared/'model_spec.json').read_bytes()), spec, out)
        else: verify_assets(conf, spec, out)
        if time.perf_counter()-start > 3600: raise TimeoutError('Asset preflight limit')
        local.start(); checks = []; prompts = {}
        for job in jobs:
            original = messages(cases[job['case_id']], job); msgs = transport_messages(conf, original)
            rendered = local.request('/apply-template', {'messages': msgs})['prompt']
            if conf['model_family'] == 'mistral': verify_rendered_instructions(original, rendered)
            elif any(m['content'] not in rendered for m in original): raise ValueError('Lost instruction')
            tokens = local.request('/tokenize', {'content': rendered, 'parse_special': True,
                                   'add_special': conf['model_family'] == 'mistral'})['tokens']
            if len(tokens)+256+64 > 4096: raise ValueError('Context would truncate')
            prompts[job['job_id']] = msgs
            checks.append({'job_id': job['job_id'], 'tokens': len(tokens), 'rendered_sha256': digest(rendered)})
        save(out/'token_preflight.json', checks)
        counts = {r['job_id']: r['tokens'] for r in checks}
        for i, job in enumerate(jobs):
            prefix = f'{i:03d}'; count = counts[job['job_id']]
            payload = {'model': conf['model_alias'], 'messages': prompts[job['job_id']], 'max_tokens': 256,
                       'temperature': 0, 'seed': 20261027, 'stream': False, 'cache_prompt': False}
            save(out/(prefix+'.request.json'), payload)
            save(out/(prefix+'.dispatch.json'), {'job_id': job['job_id'], 'utc': utc(),
                'input_sha256': digest(payload), 'preflight_prompt_tokens': count})
            attempts += 1; tick = time.perf_counter()
            raw = local.request('/v1/chat/completions', payload); seconds = time.perf_counter()-tick
            save(out/(prefix+'.response.json'), raw); responses += 1
            usage = validate_usage(raw, count, 4096, 256)
            save(out/(prefix+'.measurement.json'), {'usage': usage, 'seconds': seconds, 'timings': raw.get('timings')})
            print(f'Robustness progress: {i+1}/144', flush=True)
        status = 'COMPLETED'
    except BaseException as exc:
        save(out/'failure.json', {'type': type(exc).__name__, 'detail': str(exc), 'utc': utc()})
    finally:
        terminal = {'status': status, 'planned_calls': 144, 'dispatch_attempts': attempts,
            'responses_recorded': responses, **local.stop(), 'seconds_including_preflight': time.perf_counter()-start,
            'effect_scoring_performed': False, 'actual_API_cny': 0, 'utc': utc()}
        save(out/'terminal.json', terminal)
        save(out/'sha256.json', {p.name: sha(p) for p in out.iterdir() if p.is_file()})
    print(json.dumps(terminal, indent=2)); return terminal


def summarize_rows(rows):
    result = {}
    for arm, method, price in sorted({(r['arm'], r['method'], str(r['price'])) for r in rows}):
        group = [r for r in rows if (r['arm'], r['method'], str(r['price'])) == (arm, method, price)]
        neg = [r for r in group if not r['sufficient']]; pos = [r for r in group if r['sufficient']]
        result[f'{arm}:{method}:p{price}'] = {
            'negative_denominator': len(neg), 'positive_denominator': len(pos),
            'false_applicable': sum(r['admissibility'] == 'applicable' for r in neg),
            'true_applicable': sum(r['admissibility'] == 'applicable' for r in pos),
            'negative_invalid': sum(not r['valid'] for r in neg),
            'positive_invalid': sum(not r['valid'] for r in pos),
            'unknown': sum(r['admissibility'] == 'unknown' for r in group),
            'missing': sum(r.get('failure') == 'missing' for r in group),
            'labels': dict(Counter(r['admissibility'] for r in group))}
        prices = (1, 20) if price == 'None' else (int(price),)
        actions = {}
        for p in prices:
            decisions = [(r, r['action'] if method == 'M1' else route(r['admissibility'], p)) for r in group]
            actions[str(p)] = {'unsafe_reuse': sum(a == 'reuse' and not r['sufficient'] for r,a in decisions),
                              'useful_reuse': sum(a == 'reuse' and r['sufficient'] and p < 10 for r,a in decisions)}
        result[f'{arm}:{method}:p{price}']['actions'] = actions
    return result


def audit(prepared, manifest, output):
    prepared = prepared.resolve(); output = output.resolve(); verify_prepared(prepared, manifest)
    if ROOT != prepared/'source': raise ValueError('Use frozen source')
    out = prepared/'batch-00'
    if not (out/'terminal.json').exists(): raise ValueError('No intermediate effect inspection')
    verify_index(out); terminal = json.loads((out/'terminal.json').read_bytes())
    if not terminal['owned_server_stopped']: raise ValueError('Server remains live')
    conf = json.loads((prepared/'config.json').read_bytes()); validate(conf)
    cases = json.loads((prepared/'cases.private.json').read_bytes()); by_case = {c['case_id']: c for c in cases}
    jobs = json.loads((prepared/'jobs.json').read_bytes())
    if jobs != schedule(cases): raise ValueError('Schedule differs from frozen protocol')
    rows = []; usage = Counter(); seconds = 0; discrepancies = []
    def add(job, raw):
        case = by_case[job['case_id']]
        rows.append(dict(job, template=case['template'], family=case['family'],
                         sufficient=case['audit']['sufficient'], **parse(raw, job)))
    for i, job in enumerate(jobs):
        prefix = f'{i:03d}'; rp = out/(prefix+'.response.json'); raw = None
        req = out/(prefix+'.request.json')
        if req.exists():
            payload = json.loads(req.read_bytes()); dispatch = json.loads((out/(prefix+'.dispatch.json')).read_bytes())
            if (payload['messages'] != transport_messages(conf, messages(by_case[job['case_id']], job))
                or digest(payload) != dispatch['input_sha256'] or dispatch['job_id'] != job['job_id']):
                raise ValueError('Frozen prompt/provenance mismatch')
        if rp.exists():
            if not req.exists(): raise ValueError('Response without request')
            raw = json.loads(rp.read_bytes())
        mp = out/(prefix+'.measurement.json')
        if mp.exists():
            m = json.loads(mp.read_bytes()); usage.update(m['usage']); seconds += m['seconds']
        add(job, raw)
    for record in json.loads((prepared/'baseline.private.json').read_bytes()):
        job, raw = record['job'], record['raw']; add(job, raw)
        if parse(raw, job) != parse_response(raw, job['method']): discrepancies.append(job['job_id'])
    paired = []
    originals = {(r['case_id'], r['method'], r['price']): r for r in rows if r['arm'] == 'original'}
    for row in rows:
        if row['arm'] == 'original': continue
        keys = [(row['case_id'], row['method'], row['price'])] if row['arm'] == 'coded' else [
            (row['case_id'], 'M3', None), *[(row['case_id'], 'M4', p) for p in (1, 10, 20)]]
        for key in keys:
            old = originals[key]
            paired.append({'case_id': row['case_id'], 'template': row['template'], 'sufficient': row['sufficient'],
                'new_job': row['job_id'], 'original_job': old['job_id'],
                'new_label': row['admissibility'], 'original_label': old['admissibility'],
                'changed': row['admissibility'] != old['admissibility']})
    result = {'scope': 'PRESELECTED_ROBUSTNESS_DESCRIPTIVE_ONLY', 'model_family': conf['model_family'],
        'manifest_sha256': manifest, 'terminal': terminal, 'methods': summarize_rows(rows),
        'by_family': {f: summarize_rows([r for r in rows if r['family'] == f]) for f in sorted({r['family'] for r in rows})},
        'by_template': {t: summarize_rows([r for r in rows if r['template'] == t]) for t in sorted({r['template'] for r in rows})},
        'rows': rows, 'pairs': paired, 'strict_original_parser_discrepancies': discrepancies,
        'new_usage': dict(usage), 'new_http_seconds': seconds, 'all_actual_prompts_match_freeze': True,
        'goal_new_attempts_total': conf['max_attempts_new_goal_before']+terminal['dispatch_attempts'],
        'goal_service_seconds_total': conf['service_seconds_new_goal_before']+terminal['owned_server_seconds'],
        'warning': '12 preselected previously exposed bases; not new independent confirmation. Fixed map only. '
                   'Checklist removal is not isolated evidence of internal anchoring. Original main results unchanged.'}
    output.mkdir(parents=True, exist_ok=False); save(output/'summary.json', result)
    save(output/'input_hashes.json', {'manifest': manifest, 'batch_index': sha(out/'sha256.json')})
    save(output/'sha256.json', {p.name: sha(p) for p in output.iterdir() if p.is_file()})
    print(json.dumps({k: result[k] for k in ('scope', 'model_family', 'methods', 'strict_original_parser_discrepancies',
        'goal_new_attempts_total', 'goal_service_seconds_total')}, indent=2)); return result


def main():
    p = argparse.ArgumentParser(description=__doc__); modes = p.add_mutually_exclusive_group(required=True)
    for mode in ('prepare', 'execute', 'audit'): modes.add_argument('--'+mode, action='store_true')
    for key in ('e1', 'audit-path', 'resource', 'prepared', 'output'): p.add_argument('--'+key, type=Path)
    p.add_argument('--e1-manifest-sha256'); p.add_argument('--manifest-sha256'); a = p.parse_args()
    if a.prepare: prepare(a.e1, a.e1_manifest_sha256, a.audit_path, a.resource, a.output)
    elif a.execute:
        if execute(a.prepared, a.manifest_sha256)['status'] != 'COMPLETED': raise SystemExit(1)
    else: audit(a.prepared, a.manifest_sha256, a.output)


if __name__ == '__main__': main()
