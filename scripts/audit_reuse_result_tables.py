"""Read-only source audit and complete plain-text result appendix (no inference).

Private sealed summaries are required for --verify-private. Public-only table
generation is explicitly distinguished from verified-private generation. This
does not certify manuscript prose, raw inference provenance, or causal claims.
"""
import argparse
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
MODELS = ('qwen', 'mistral')
METHODS = ('M1', 'M2', 'M3', 'M4', 'M5', 'M6')
PRICES = ('1', '10', '20')


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same(actual, expected, context):
    if actual != expected:
        raise ValueError('Mismatch: ' + context)


def percentage(value):
    return str((Decimal(str(value)) * 100).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP))


def bracket(values):
    return '[' + ', '.join(percentage(v) for v in values) + ']'


def strip_rows(contrasts):
    return {k: {field: value for field, value in row.items() if field != 'paired_rows'}
            for k, row in contrasts.items()}


def supplemental_action_bounds(main, contrast):
    """Post-outcome algebraic bounds, NOT an originally exported statistic.

    M1 invalid responses have no action; other methods have deterministic
    fallback. validate_main verifies this correspondence per case first.
    Every template/family has equal size, so count bounds have the frozen weights.
    """
    left, remainder = contrast.split('_minus_')
    right, endpoint = remainder.split('_', 1)
    positive = endpoint == 'useful_reuse'
    if endpoint not in ('unsafe_reuse', 'useful_reuse'):
        raise ValueError('Unknown action endpoint')
    denominator = 'positive_n' if positive else 'negative_n'
    label_key = 'positive_labels' if positive else 'negative_labels'
    rows = main['low_price_actions']
    n = rows[left][denominator]
    same(n, rows[right][denominator], 'paired class denominators')
    def unresolved(method):
        if method == 'M1':
            return main['labels'][method]['1'][label_key].get('invalid', 0)
        same(rows[method]['unresolved_actions'], 0, 'deterministic actions resolved')
        return 0
    observed = rows[left][endpoint] - rows[right][endpoint]
    return [(observed - unresolved(right)) / n, (observed + unresolved(left)) / n]


def validate_main(public, sealed, prepared):
    """Recount every method/price from sealed per-case observations/decisions."""
    for key, source in [('responses', 'responses_recorded'),
                        ('dispatch_attempts', 'dispatch_attempts'),
                        ('planned_bases', 'planned_bases'),
                        ('structural_templates', 'structural_templates'),
                        ('missing', 'missing_calls'), ('server_seconds', 'server_seconds')]:
        same(public[key], sealed[source], key)
    observations, decisions = sealed['observations'], sealed['decisions']
    same(len({r['base_id'] for r in observations}), public['planned_bases'], 'bases')
    same(len({r['family'] for r in observations}), public['families'], 'families')
    same(dict(Counter(r['admissibility'] for r in sealed['physical_judgments'])),
         public['physical_judgment_status_counts'], 'physical labels')
    for method in METHODS[:4]:
        for price in PRICES:
            rows = [r for r in observations if r['method'] == method and r['price'] == int(price)]
            neg = [r for r in rows if not r['sufficient']]
            pos = [r for r in rows if r['sufficient']]
            actual = dict(negative_n=len(neg), positive_n=len(pos),
                          negative_labels=dict(Counter(r['admissibility'] for r in neg)),
                          positive_labels=dict(Counter(r['admissibility'] for r in pos)),
                          false_applicable=sum(r['admissibility'] == 'applicable' for r in neg),
                          positive_applicable=sum(r['admissibility'] == 'applicable' for r in pos))
            same(actual, public['labels'][method][price], f'{method}/{price} labels')
    for method in METHODS:
        rows = [r for r in decisions if r['method'] == method and r['price'] == 1]
        actual = dict(negative_n=sum(not r['sufficient'] for r in rows),
                      positive_n=sum(r['sufficient'] for r in rows),
                      unsafe_reuse=sum(r['unsafe_reuse'] for r in rows),
                      useful_reuse=sum(r['useful_reuse'] for r in rows),
                      unresolved_actions=sum(r['unresolved_action'] for r in rows))
        same(actual, public['low_price_actions'][method], method + ' actions')
        for row in rows:
            expected_unresolved = method == 'M1' and row['admissibility'] == 'invalid'
            same(row['unresolved_action'], expected_unresolved, 'invalid-to-unresolved mapping')
        for family, methods in public['by_family_low_price'].items():
            subset = [r for r in rows if r['family'] == family]
            same(dict(negative_n=sum(not r['sufficient'] for r in subset),
                      positive_n=sum(r['sufficient'] for r in subset),
                      unsafe_reuse=sum(r['unsafe_reuse'] for r in subset),
                      useful_reuse=sum(r['useful_reuse'] for r in subset)),
                 methods[method], family + '/' + method)
    for contrast, result in public['action_contrasts'].items():
        left, remainder = contrast.split('_minus_')
        right, endpoint = remainder.split('_', 1)
        selected = [r for r in decisions if r['price'] == 1
                    and r['sufficient'] == (endpoint == 'useful_reuse')]
        indexed_actions = {(r['base_id'], r['method']): r for r in selected}
        bases = sorted({r['base_id'] for r in selected})
        families = Counter(r['family'] for r in selected if r['method'] == left)
        templates = Counter(r['template'] for r in selected if r['method'] == left)
        same(len(set(families.values())), 1, 'equal family weights for bounds')
        same(len(set(templates.values())), 1, 'equal template weights for bounds')
        lower, upper = [], []
        for base in bases:
            a, b = indexed_actions[base, left], indexed_actions[base, right]
            delta = int(a[endpoint]) - int(b[endpoint])
            lower.append(delta - int(b['unresolved_action']))
            upper.append(delta + int(a['unresolved_action']))
        bounds = [statistics.mean(lower), statistics.mean(upper)]
        same(bounds, supplemental_action_bounds(public, contrast), 'case-level action bounds')
        observed = statistics.mean(int(indexed_actions[base, left][endpoint]) -
                                   int(indexed_actions[base, right][endpoint]) for base in bases)
        same(observed, result['weighted_difference'], 'action difference point estimate')
    for key in ('price_contrasts', 'action_contrasts', 'H2_M3_minus_M2_false_applicability'):
        same(strip_rows(sealed[key]), public[key], key)
    indexed = {(r['case_id'], r['method'], r['price']): r for r in observations}
    verifiers = [r for r in observations if r['method'] == 'M4']
    agreement = sum(r['admissibility'] == indexed[r['case_id'], 'M1', r['price']]['admissibility']
                    for r in verifiers)
    same(agreement, public['M4_M1_label_agreement']['same'], 'verifier agreement')
    same(len(verifiers), public['M4_M1_label_agreement']['planned'], 'verifier denominator')
    classical = read(prepared / 'classical_baseline.json')
    same({k: v for k, v in classical.items() if k != 'rows'}, public['classical'], 'classical')
    usage = Counter()
    measurements = list(prepared.glob('batch-*/*.measurement.json'))
    same(len(measurements), public['dispatch_attempts'], 'measurement coverage')
    for path in measurements:
        record = read(path)['usage']
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            if type(record.get(key)) is not int or record[key] < 0:
                raise ValueError('Invalid token measurement')
            usage[key] += record[key]
        same(record['total_tokens'], record['prompt_tokens'] + record['completion_tokens'], 'tokens')
    same(dict(usage), public['physical_tokens'], 'physical token totals')


def validate_e3(public, sealed):
    for key, value in sealed.items():
        if key not in ('rows', 'prepared'):
            same(value, public[key], 'E3/' + key)
    same(len(sealed['rows']), public['logical_cells'], 'E3 row count')
    same(len({r['cell_id'] for r in sealed['rows']}), public['logical_cells'], 'E3 unique cells')
    same(set(public['groups']), set(METHODS), 'E3 methods')
    for method in METHODS:
        same(set(public['groups'][method]), set(PRICES), 'E3 prices')
        for price in PRICES:
            rows = [r for r in sealed['rows'] if r['method'] == method and r['price'] == int(price)]
            group = public['groups'][method][price]
            counts = dict(n=len(rows), positive_n=sum(r['sufficient'] for r in rows),
                          negative_n=sum(not r['sufficient'] for r in rows),
                          final_correct=sum(r['final_correct'] for r in rows),
                          no_final_answer=sum(not r['has_final_answer'] for r in rows),
                          fallbacks=sum(r['fallback'] for r in rows),
                          cache_first_correct=sum(r['cache_first_correct'] for r in rows),
                          positive_cache_first_correct=sum(r['sufficient'] and r['cache_first_correct'] for r in rows),
                          same_SQL_correct_in_both_test_worlds=sum(r['both_test_worlds_correct'] for r in rows),
                          reuse_selected=sum(r['action'] == 'reuse' for r in rows),
                          negative_reuse_selected=sum(not r['sufficient'] and r['action'] == 'reuse' for r in rows))
            for key, value in counts.items():
                same(value, group[key], f'E3/{method}/{price}/{key}')
            same(dict(Counter(r['first_status'] for r in rows)), group['first_statuses'], 'E3 statuses')
            calls = sum(r['decision_cost']['calls'] + r['transform_cost']['calls'] for r in rows)
            same(calls, group['logical_calls'], 'E3 logical calls')
            for uses in ('1', '10', '100'):
                mean = statistics.mean(r['modeled_total_seconds_by_cache_uses'][uses] for r in rows)
                if not math.isfinite(mean) or not math.isclose(
                        mean, group['mean_modeled_total_seconds_by_cache_uses'][uses], rel_tol=1e-12):
                    raise ValueError('E3 cost mean mismatch')


def load_sources(root, verify_private=False):
    records, evidence = {}, []
    for kind in ('confirmation', 'e3', 'robustness'):
        for model in MODELS:
            path = root / f'configs/run_records/reuse_{kind}_{model}_20261027.json'
            public = read(path)
            item = dict(record=str(path.relative_to(root)).replace('\\', '/'),
                        record_sha256=sha(path), sealed_summary_sha256=public['summary_sha256'])
            if verify_private:
                suffix = '004' if (kind, model) == ('robustness', 'qwen') else '002'
                audit = root / f'runtime/runs/reuse-{kind}-{model}-audit-20261027-{suffix}'
                same(sha(audit / 'summary.json'), public['summary_sha256'], 'sealed summary digest')
                same(sha(audit / 'input_hashes.json'), public['input_hashes_sha256'], 'input hash ledger digest')
                sealed = read(audit / 'summary.json')
                same(sealed['manifest_sha256'], public['manifest_sha256'], 'manifest linkage')
                prepared = root / public['prepared']
                same(sha(prepared / 'manifest.json'), public['manifest_sha256'], 'prepared manifest digest')
                if kind == 'confirmation':
                    validate_main(public, sealed, prepared)
                elif kind == 'e3':
                    validate_e3(public, sealed)
                else:
                    for key in ('methods', 'by_family', 'by_template', 'new_usage', 'new_http_seconds'):
                        same(sealed[key], public[key], 'robustness/' + key)
                    same(len(sealed['strict_original_parser_discrepancies']),
                         public['strict_original_parser_discrepancies'], 'parser discrepancy count')
                item['private_summary_and_aggregate_checks'] = 'PASS'
            records[kind, model] = public
            evidence.append(item)
    return records, evidence


def appendix(records, private_verified):
    lines = ['COMPLETE CONFIRMATION / EXECUTION / ROBUSTNESS RESULT TABLES',
             'Generated from sealed aggregate records; no inference, retries or new SQL execution.',
             'Private source audit: ' + ('PASS' if private_verified else 'NOT RUN (public aggregates only)'),
             'This is NOT a completed manuscript-number or semantic-claim audit.',
             'Models are separate. Price variants are not independent tasks.',
             'All differences/bands/bounds below are percentage points, not relative improvements.',
             'Resampling bands concern selected templates/bases, not population coverage.',
             'Unresolved reassignment bounds are sensitivity ranges, not confidence intervals.',
             'Counters retain unknown/invalid outputs; neither is a correct rejection.', '']
    for model in MODELS:
        main = records['confirmation', model]
        lines += [model.upper() + ': E1/E2',
                  f"bases={main['planned_bases']}; templates={main['structural_templates']}; families={main['families']}; calls={main['dispatch_attempts']}",
                  'Physical labels: ' + json.dumps(main['physical_judgment_status_counts'], sort_keys=True),
                  'method price false_applicable/negative_n accepted_positive/positive_n negative_statuses positive_statuses']
        for method, prices in main['labels'].items():
            for price in PRICES:
                r = prices[price]
                lines.append(f"{method} {price} {r['false_applicable']}/{r['negative_n']} {r['positive_applicable']}/{r['positive_n']} "
                             + json.dumps(r['negative_labels'], sort_keys=True) + ' ' + json.dumps(r['positive_labels'], sort_keys=True))
        lines += ['', 'Low-price actions: method unsafe/negative_n useful/positive_n unresolved_actions(all classes)']
        for method, r in main['low_price_actions'].items():
            lines.append(f"{method} {r['unsafe_reuse']}/{r['negative_n']} {r['useful_reuse']}/{r['positive_n']} {r['unresolved_actions']}")
        for title, key in [('Primary price contrast (low minus high)', 'price_contrasts'),
                           ('Secondary hidden minus visible label contrast, by price', 'H2_M3_minus_M2_false_applicability'),
                           ('Primary low-price action contrasts: M3 minus comparator', 'action_contrasts')]:
            lines += ['', title, 'contrast difference resampling_band unresolved_reassignment_bounds']
            for name, r in main[key].items():
                bounds = r.get('unresolved_worst_case_difference_bounds', r.get('unresolved_worst_case_bounds'))
                posthoc = bounds is None and key == 'action_contrasts'
                if posthoc:
                    bounds = supplemental_action_bounds(main, name)
                if bounds is None:
                    raise ValueError('Sensitivity bounds absent: ' + name)
                lines.append(f"{name} {percentage(r['weighted_difference'])} {bracket(r['bootstrap_95_resampling_band'])} {bracket(bounds)}")
                if posthoc:
                    lines.append('  Bounds are a POST-OUTCOME reporting supplement, not an originally exported frozen statistic.')
                lines.append('  family differences: ' + json.dumps({k: percentage(v) for k, v in r['family_differences'].items()}, sort_keys=True))
                lines.append('  template differences: ' + json.dumps({k: percentage(v) for k, v in r['template_differences'].items()}, sort_keys=True))
        lines += ['', 'Family low-price actions (class denominators retained):']
        for family, methods in main['by_family_low_price'].items():
            for method, r in methods.items():
                lines.append(f"{family} {method} unsafe={r['unsafe_reuse']}/{r['negative_n']} useful={r['useful_reuse']}/{r['positive_n']}")
        lines += ['Classical audit: ' + json.dumps(main['classical'], sort_keys=True),
                  'M4/M1 agreement: ' + json.dumps(main['M4_M1_label_agreement'], sort_keys=True),
                  'Physical token usage: ' + json.dumps(main['physical_tokens'], sort_keys=True),
                  f"Owned service seconds (not electricity): {main['server_seconds']}", '']
        e3 = records['e3', model]
        lines += [model.upper() + ': E3',
                  f"bases={e3['bases']}; templates={e3['templates']}; logical_cells={e3['logical_cells']}",
                  'method price n final_correct cache_first_correct fallbacks no_answer logical_calls cost_seconds_at_1_10_100_cache_uses first_statuses']
        for method in METHODS:
            for price in PRICES:
                r = e3['groups'][method][price]
                lines.append(' '.join([method, price] + [str(r[k]) for k in ('n', 'final_correct', 'cache_first_correct', 'fallbacks', 'no_final_answer', 'logical_calls')])
                             + ' ' + json.dumps(r['mean_modeled_total_seconds_by_cache_uses'], sort_keys=True)
                             + ' ' + json.dumps(r['first_statuses'], sort_keys=True))
        lines += ['Physical new conversion usage: ' + json.dumps(e3['physical_new_transform_usage'], sort_keys=True),
                  e3['cost_warning'], e3['scope_warning'], '']
        robust = records['robustness', model]
        lines += [model.upper() + ': FIXED-SUBSET ROBUSTNESS (not new independent confirmation)',
                  'condition false_applicable/negative_n accepted_positive/positive_n negative_invalid positive_invalid unknown missing']
        for name, r in robust['methods'].items():
            lines.append(f"{name} {r['false_applicable']}/{r['negative_denominator']} {r['true_applicable']}/{r['positive_denominator']} "
                         + ' '.join(str(r[k]) for k in ('negative_invalid', 'positive_invalid', 'unknown', 'missing')))
        lines += ['Physical new usage: ' + json.dumps(robust['new_usage'], sort_keys=True), '']
    lines += ['SCOPE: Tables preserve all listed methods/prices and both models. No raw response text is exported.',
              'Source audit recounts main per-case labels/actions and E3 per-cell outcomes/costs; it checks',
              'sealed hashes and exported robustness aggregates. It does not rerun the bootstrap, reconstruct',
              'raw model responses, verify causal interpretation, or certify every prose numeral.',
              'Action reassignment bounds are an explicitly post-outcome supplement: unresolved actions',
              'are assigned endpoint 0 or 1; deterministic fallback actions remain observed, not unresolved.',
              'This supplements an incomplete uncertainty report; it does not retroactively meet a freeze requirement.',
              'Frozen-source rescoring and clean-directory reproduction are separate evidence.']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--verify-private', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Refuse to overwrite an earlier audit: ' + str(args.output))
    records, evidence = load_sources(args.root, args.verify_private)
    text = appendix(records, args.verify_private)
    args.output.mkdir(parents=True, exist_ok=False)
    table = args.output / 'RESULT_TABLES.txt'
    table.write_text(text, encoding='utf-8', newline='\n')
    report = dict(status='PRIVATE_SOURCE_TABLE_AUDIT_PASSED' if args.verify_private else 'PUBLIC_TABLES_ONLY',
                  manuscript_claim_audit_complete=False, new_model_calls=0,
                  new_sql_execution=False, sources=evidence, table_sha256=sha(table))
    (args.output / 'audit.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(status=report['status'], sources=len(evidence), output=str(args.output))))


if __name__ == '__main__':
    main()
