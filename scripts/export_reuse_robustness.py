"""Export aggregate-only robustness record after two identical frozen audits."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts.run_local_separation import save, sha
from scripts.run_reuse_development import verify_index, verify_prepared


def export(prepared, first, second, output, report):
    prepared, first, second, output, report = [p.resolve() for p in (prepared, first, second, output, report)]
    if (not output.is_relative_to(ROOT/'configs/run_records')
        or not report.is_relative_to(ROOT/'reports/reuse_separation_14day')):
        raise ValueError('Use scoped public aggregate destinations')
    for path in (first, second): verify_index(path)
    for name in ('summary.json', 'input_hashes.json'):
        if sha(first/name) != sha(second/name): raise ValueError('Audits disagree')
    summary = json.loads((first/'summary.json').read_bytes())
    verify_prepared(prepared, summary['manifest_sha256'])
    if summary['terminal']['status'] != 'COMPLETED' or not summary['terminal']['owned_server_stopped']:
        raise ValueError('Successful fully terminated batch required for this exporter')
    record = {k: summary[k] for k in ('scope', 'model_family', 'manifest_sha256', 'methods', 'by_family',
        'by_template', 'new_usage', 'new_http_seconds', 'goal_new_attempts_total',
        'goal_service_seconds_total', 'warning', 'all_actual_prompts_match_freeze')}
    record.update(prepared=str(prepared.relative_to(ROOT)).replace('\\', '/'),
        summary_sha256=sha(first/'summary.json'), input_hashes_sha256=sha(first/'input_hashes.json'),
        source_rescoring_repeated=True, dispatch_attempts=summary['terminal']['dispatch_attempts'],
        owned_service_seconds=summary['terminal']['owned_server_seconds'],
        cold_start_seconds=summary['terminal']['cold_start_seconds'], all_services_closed=True,
        actual_API_cny=0, energy_measured=False,
        strict_original_parser_discrepancies=len(summary['strict_original_parser_discrepancies']))
    record['analysis_repair'] = summary.get('analysis_repair')
    lookup = {r['job_id']: r for r in summary['rows']}
    comparisons = {}
    for pair in summary['pairs']:
        new, old = lookup[pair['new_job']], lookup[pair['original_job']]
        key = f'{new["arm"]}:{new["method"]}:p{new["price"]} vs original:{old["method"]}:p{old["price"]}'
        c = comparisons.setdefault(key, dict(contexts=0, changed_labels=0,
            negative_denominator=0, new_false_applicable=0, original_false_applicable=0,
            positive_denominator=0, new_true_applicable=0, original_true_applicable=0,
            new_invalid=0, original_invalid=0))
        c['contexts'] += 1; c['changed_labels'] += pair['changed']
        c['new_invalid'] += not new['valid']; c['original_invalid'] += not old['valid']
        if pair['sufficient']:
            c['positive_denominator'] += 1
            c['new_true_applicable'] += new['admissibility'] == 'applicable'
            c['original_true_applicable'] += old['admissibility'] == 'applicable'
        else:
            c['negative_denominator'] += 1
            c['new_false_applicable'] += new['admissibility'] == 'applicable'
            c['original_false_applicable'] += old['admissibility'] == 'applicable'
    record['paired_comparisons'] = comparisons
    record['price_contrasts'] = {}
    for method in ('M1', 'M2'):
        d = {}
        for arm in ('original', 'coded'):
            low = record['methods'][f'{arm}:{method}:p1']; high = record['methods'][f'{arm}:{method}:p20']
            d[arm] = {'low_minus_high_false_applicable_count': low['false_applicable']-high['false_applicable'],
                'negative_denominator': 12,
                'low_invalid': low['negative_invalid'], 'high_invalid': high['negative_invalid']}
        record['price_contrasts'][method] = d
    if output.exists() or report.exists(): raise ValueError('Do not overwrite prior exports')
    save(output, record)
    lines = [f'{summary["model_family"]} -- preselected robustness readout v1', '',
        '144/144 separate local calls,12 previously exposed bases/12 templates/24 contexts.',
        'Strict original-parser differences: '+str(record['strict_original_parser_discrepancies']),
        'Rows are counts, not rates. Each semantic class denominator is12.',
        'Invalid judgments are not successful semantics; shared blind judgments are not independent.', '',
        'arm/method/price | negative false-applicable/12 | positive applicable/12 | invalid/24 | unknown/24']
    for name, m in record['methods'].items():
        lines.append(f'{name} | {m["false_applicable"]} | {m["true_applicable"]} | '
                     f'{m["negative_invalid"]+m["positive_invalid"]} | {m["unknown"]}')
    lines += ['', 'Paired low-minus-high price contrasts (negative count difference/12):',
              json.dumps(record['price_contrasts'], ensure_ascii=False, indent=2), '',
              'Paired coding and checklist comparisons:', json.dumps(comparisons, ensure_ascii=False, indent=2), '',
              'Limits: fixed code mapping and small preselected subset; not a new held-out sample.',
              'Checklist/proposal contrast does not alone identify an internal anchoring mechanism.',
              'Original M4, failed transforms and full main results remain unchanged.',
              'No new SQL execution or final-answer success evidence in this diagnostic.',
              f'Summary SHA256: {record["summary_sha256"]}',
              f'Inputs SHA256: {record["input_hashes_sha256"]}',
              f'Cumulative calls: {record["goal_new_attempts_total"]}; owned-service seconds: {record["goal_service_seconds_total"]}',
              'All owned services stopped. API cost0; energy not measured.']
    report.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({k: record[k] for k in ('model_family', 'price_contrasts', 'strict_original_parser_discrepancies',
        'goal_new_attempts_total', 'goal_service_seconds_total')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('prepared', 'first', 'second', 'output', 'report'): parser.add_argument('--'+key, type=Path, required=True)
    a = parser.parse_args(); export(a.prepared, a.first, a.second, a.output, a.report)
