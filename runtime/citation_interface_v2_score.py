"""Offline audited scoring for the bounded-grammar citation-interface v2 probe.

All planned outputs remain in denominators. Model format violations score zero;
missing or infrastructure-failed calls block scoring. This is an eight-question
interface diagnostic, not a DICE validation or a 64-independent-sample study.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import mean, median

from .citation_interface_v2 import (
    Audit, JOBS, run_item, validate_design, validate_engine_evidence,
)
from .citation_interface_score import _fields, _sha256, score_output
from .dependency_probe import check_server, sources
from .e1 import digest, save
from .natural_qa_data import OFFICIAL_METRIC_NAMES, seeded_order
from .natural_repetition_score import _file_hash, _finite_nonnegative, _read
from .probe_score import check, interval


ITEMS, CALLS = 8, 64
PRIMARY_CONTRASTS = {
    'explicit_minus_original_format_valid_json_object_full': (
        'full__explicit__json_object', 'full__original__json_object', 'format_valid_count'),
    'guided_minus_json_format_valid_explicit_full': (
        'full__explicit__guided_grammar', 'full__explicit__json_object', 'format_valid_count'),
}
EXPLORATORY_CONTRASTS, CONTEXT_CONTRASTS = {}, {}
for _label, _metric in (('answer_f1', 'f1'), ('joint_f1', 'joint_f1')):
    for _context in ('full', 'question_only'):
        for _decoder in ('json_object', 'guided_grammar'):
            EXPLORATORY_CONTRASTS[
                f'explicit_minus_original_{_label}_{_decoder}_{_context}'] = (
                    f'{_context}__explicit__{_decoder}',
                    f'{_context}__original__{_decoder}', _metric)
        for _variant in ('original', 'explicit'):
            EXPLORATORY_CONTRASTS[
                f'guided_minus_json_{_label}_{_variant}_{_context}'] = (
                    f'{_context}__{_variant}__guided_grammar',
                    f'{_context}__{_variant}__json_object', _metric)
    for _variant in ('original', 'explicit'):
        for _decoder in ('json_object', 'guided_grammar'):
            CONTEXT_CONTRASTS[f'full_minus_question_only_{_label}_{_variant}_{_decoder}'] = (
                f'full__{_variant}__{_decoder}',
                f'question_only__{_variant}__{_decoder}', _metric)
ALL_CONTRASTS = {**PRIMARY_CONTRASTS, **EXPLORATORY_CONTRASTS, **CONTEXT_CONTRASTS}


def question_metrics(result, view, gold):
    """One output per cell per question; do not drop or repair any output."""
    check(result['id'] == view['id'], 'Scoring result/view identity mismatch')
    check(set(result['outputs']) == set(JOBS), 'Incomplete or extra terminal outputs')
    outputs = {job: score_output(result['outputs'][job], view, gold) for job in JOBS}
    groups = {}
    for job, score in outputs.items():
        context, variant, decoder = JOBS[job]
        groups[job] = {
            **{metric: score[metric] for metric in OFFICIAL_METRIC_NAMES},
            'context': context, 'prompt_variant': variant, 'decoder': decoder,
            'outputs': 1, 'format_valid_count': int(score['format_valid']),
            'valid_output_count': int(score['valid_output']),
            'diagnostic_counts': dict(Counter(score['diagnostics'])),
            'invalid_reason_counts': ({score['invalid_reason']: 1}
                                     if score['invalid_reason'] else {}),
        }
    contrasts = {name: groups[plus][metric] - groups[minus][metric]
                 for name, (plus, minus, metric) in ALL_CONTRASTS.items()}
    return {'item': result['item'], 'id': view['id'], 'groups': groups,
            'contrasts': contrasts, 'outputs': outputs}


def group_metrics(rows):
    check(bool(rows), 'No questions to aggregate')
    summaries = {}
    for job, (context, variant, decoder) in JOBS.items():
        values = [row['groups'][job] for row in rows]
        count = sum(value['outputs'] for value in values)
        valid_format = sum(value['format_valid_count'] for value in values)
        valid_output = sum(value['valid_output_count'] for value in values)
        diagnostics, reasons = Counter(), Counter()
        for value in values:
            diagnostics.update(value['diagnostic_counts'])
            reasons.update(value['invalid_reason_counts'])
        summaries[job] = {
            **{metric: mean(value[metric] for value in values) for metric in OFFICIAL_METRIC_NAMES},
            'context': context, 'prompt_variant': variant, 'decoder': decoder,
            'questions': len(rows), 'all_planned_outputs': count,
            'actual_terminal_outputs': count,
            'format_valid_count': valid_format, 'format_valid_rate': valid_format / count,
            'format_violation_count': count - valid_format,
            'valid_output_count': valid_output, 'valid_output_rate': valid_output / count,
            'diagnostic_counts': dict(sorted(diagnostics.items())),
            'invalid_reason_counts': dict(sorted(reasons.items())),
        }
    return summaries


def paired_contrasts(rows, design, contrasts=None):
    """Resample whole matched questions, not individual calls."""
    check(bool(rows), 'No question pairs to bootstrap')
    contrasts = ALL_CONTRASTS if contrasts is None else contrasts
    return {name: {
        **interval([row['contrasts'][name] for row in rows],
                   design['bootstrap_seed'], design['bootstrap_resamples'], level=.95),
        'questions': len(rows), 'bootstrap_unit': 'question',
        'bootstrap_seed': design['bootstrap_seed'],
        'bootstrap_resamples': design['bootstrap_resamples'],
        'plus_group': plus, 'minus_group': minus, 'metric': metric,
        'interpretation': 'Descriptive unadjusted interval; no p-value or efficacy test',
    } for name, (plus, minus, metric) in contrasts.items()}


def interface_readiness(groups):
    """Prospective serialization-only gate; never a scientific efficacy claim."""
    group = groups['full__explicit__json_object']
    valid = group['format_valid_count']
    bracket_only = group['diagnostic_counts'].get('bracket_only_mismatch', 0)
    return {
        'kind': 'INTERFACE_READINESS_ONLY_NOT_METHOD_EFFICACY',
        'group': 'full__explicit__json_object',
        'planned_questions': ITEMS, 'minimum_format_valid': 7,
        'maximum_bracket_only_mismatch': 0,
        'observed_format_valid': valid, 'observed_bracket_only_mismatch': bracket_only,
        'passes': (group['all_planned_outputs'] == ITEMS and valid >= 7 and bracket_only == 0),
        'interpretation': 'Predefined format-readiness check only; does not establish answer quality, communication benefit, DICE effectiveness, or automatically authorize/launch another experiment.',
    }


def _expected_call_names():
    return {f'i{item}-{job}.json' for item in range(ITEMS) for job in JOBS}


def _expected_result_names():
    return {f'result-{item}.json' for item in range(ITEMS)}


def audit_coverage(root):
    """Count statuses only, without gold, outcome estimates, or file writes."""
    root = Path(root)
    statuses, malformed = Counter(), []
    paths = sorted(root.glob('shard-*/i*.json'))
    for path in paths:
        try:
            record = _read(path)
            check(isinstance(record, dict), 'Call record is not an object')
            status = record.get('status', 'unknown')
            check(isinstance(status, str), 'Status is not a string')
            statuses[status] += 1
        except (OSError, ValueError, TypeError):
            malformed.append(path.relative_to(root).as_posix())
    completion_path = root / 'shard-000' / 'completion.json'
    complete = False
    if completion_path.is_file():
        try:
            completion = _read(completion_path)
            complete = (isinstance(completion, dict) and
                        type(completion.get('completed_items')) is int and
                        completion['completed_items'] == ITEMS and
                        type(completion.get('calls')) is int and completion['calls'] == CALLS)
        except (OSError, ValueError, TypeError):
            malformed.append(completion_path.relative_to(root).as_posix())
    results = list(root.glob('shard-*/result-*.json'))
    exact_names = ({path.name for path in paths} == _expected_call_names() and
                   {path.name for path in results} == _expected_result_names() and
                   {path.name for path in root.glob('shard-*') if path.is_dir()} == {'shard-000'})
    return {
        'kind': 'COVERAGE_ONLY_NO_EFFECT_ESTIMATES',
        'planned_calls': CALLS, 'observed_call_files': len(paths),
        'call_status_counts': dict(sorted(statuses.items())),
        'planned_items': ITEMS, 'completed_item_files': len(results),
        'completion_present': completion_path.is_file(),
        'completion_counts_valid': complete, 'malformed_records': malformed,
        'coverage_counts_complete': (len(paths) == CALLS and len(results) == ITEMS
                                    and exact_names and complete and not malformed
                                    and sum(statuses[key] for key in ('ok', 'format_violation')) == CALLS),
        'warning': 'Coverage is not integrity verification. Full scoring reconstructs every request and output. Pending/failed calls are never imputed, retried, repaired, or subsetted.',
    }


def _check_frozen_plan(plan, manifest, config):
    _fields(plan, ('design', 'design_sha256', 'views', 'metadata', 'eligibility',
                  'candidate_sha256', 'tokenizer_class', 'chat_template_sha256',
                  'actual_calls', 'source_sha256', 'source_bundle',
                  'model_content_sha256', 'decoder_packages', 'grammar_preflight',
                  'server_engine'), 'plan')
    design, views = plan['design'], plan['views']
    validate_design(design)
    check(design.get('bootstrap_seed') == 10103027 and design.get('bootstrap_resamples') == 2000,
          'Frozen question-bootstrap design changed')
    check(design.get('primary_descriptive_endpoints') == list(PRIMARY_CONTRASTS),
          'Frozen primary endpoint names changed')
    check(digest(plan) == manifest['plan_sha256'], 'Plan hash mismatch')
    check(digest(design) == plan['design_sha256'], 'Design hash mismatch')
    check(digest(sources()) == plan['source_sha256'] == digest(plan['source_bundle']),
          'Current/recorded frozen source mismatch')
    check(isinstance(views, list) and len(views) == ITEMS and
          type(plan['actual_calls']) is int and plan['actual_calls'] == CALLS and
          type(manifest['planned_calls']) is int and manifest['planned_calls'] == CALLS,
          'Frozen item/call counts mismatch')
    check(all(isinstance(view, dict) and set(view) == {'id', 'question', 'units'}
              for view in views), 'Frozen views must be label-free projections')
    check(len({view['id'] for view in views}) == ITEMS, 'Duplicate planned question IDs')
    check(manifest.get('no_gold_in_runtime') is True, 'Missing runtime label-isolation declaration')
    check(all(config.get(key) == design[key] for key in ('temperature', 'top_p', 'max_tokens')),
          'Config/design mismatch')
    check(all(_sha256(plan[key]) for key in (
        'candidate_sha256', 'chat_template_sha256', 'model_content_sha256')),
        'Missing/malformed frozen candidate/tokenizer/model hash')
    check(isinstance(plan['tokenizer_class'], str) and bool(plan['tokenizer_class']),
          'Missing frozen tokenizer class')
    check(plan['model_content_sha256'] == config.get('model_content_sha256'), 'Model content mismatch')
    eligibility = plan['eligibility']
    check(isinstance(eligibility, list) and len(eligibility) == 64 and
          all(isinstance(entry, dict) and 'id' in entry and
              type(entry.get('selected')) is bool and type(entry.get('eligible')) is bool
              for entry in eligibility), 'Missing/malformed frozen eligibility')
    check([entry['id'] for entry in eligibility if entry['selected']] ==
          [view['id'] for view in views], 'Frozen eligibility/selected views mismatch')
    check([entry['id'] for entry in eligibility if entry['selected']] ==
          [entry['id'] for entry in eligibility if entry['eligible']][:ITEMS],
          'Frozen selection is not first eight eligible questions')
    packages = plan['decoder_packages']
    check(isinstance(packages, dict) and
          {'vllm', 'xgrammar', 'outlines', 'outlines_core', 'transformers'} <= set(packages) and
          all(isinstance(name, str) and isinstance(version, str) and version
              for name, version in packages.items()), 'Missing/malformed decoder package freeze')
    _check_preflight(plan, packages)
    validate_engine_evidence(plan['server_engine'])
    metadata = plan['metadata']
    _fields(metadata, ('file_sha256', 'scoring_gold_sha256', 'excluded_ids',
                       'all_ordered_ids'), 'dataset metadata')
    excluded, ordered_ids = metadata['excluded_ids'], metadata['all_ordered_ids']
    check(isinstance(excluded, list) and all(isinstance(value, str) for value in excluded) and
          len(excluded) == len(set(excluded)) == 200, 'Frozen exclusion frame mismatch')
    check(isinstance(ordered_ids, list) and all(isinstance(value, str) for value in ordered_ids) and
          len(ordered_ids) == len(set(ordered_ids)) == 7205 and
          not set(ordered_ids) & set(excluded), 'Frozen sampling frame mismatch')
    check([row['id'] for row in seeded_order([{'id': value} for value in ordered_ids],
                                            design['seed'])] == ordered_ids,
          'Frozen sampling frame order mismatch')
    check([entry['id'] for entry in eligibility] == ordered_ids[:64],
          'Frozen candidate eligibility order mismatch')
    check(not {view['id'] for view in views} & set(excluded),
          'Frozen views overlap excluded prior candidates')
    return design, views


def _check_preflight(plan, packages):
    # Pure-record validation must work offline without importing CUDA/vLLM.
    from .citation_grammar_v2 import validate_preflight
    visible_sets = [set()] + [{unit['id'] for unit in view['units']} for view in plan['views']]
    validate_preflight(plan['grammar_preflight'], visible_sets, packages)


def summarize(root, gold_path):
    """Return summary, per-question rows, and input hashes after exact replay."""
    root, gold_path = Path(root), Path(gold_path)
    directory = root / 'shard-000'
    check({path.name for path in root.glob('shard-*') if path.is_dir()} == {'shard-000'},
          'Exactly shard-000 is required')
    manifest = _read(directory / 'manifest.json')
    _fields(manifest, ('plan', 'plan_sha256', 'config', 'started_unix', 'planned_calls'), 'manifest')
    plan, config = manifest['plan'], manifest['config']
    check(isinstance(config, dict), 'Malformed frozen config')
    design, views = _check_frozen_plan(plan, manifest, config)
    check(config['server_settings_reported']['max_model_len'] == 4096, 'Context setting mismatch')
    check_server(config, _read(directory / 'server_metadata.json'), resolve_paths=False)
    completion = _read(directory / 'completion.json')
    _fields(completion, ('completed_items', 'calls', 'completed_unix'), 'completion')
    check(type(completion['completed_items']) is int and completion['completed_items'] == ITEMS and
          type(completion['calls']) is int and completion['calls'] == CALLS,
          'Completion record missing full run counts')
    started, finished = manifest['started_unix'], completion['completed_unix']
    check(_finite_nonnegative(started) and _finite_nonnegative(finished) and finished >= started,
          'Invalid recorded run timestamps')
    check({path.name for path in directory.glob('i*.json')} == _expected_call_names(),
          'Call records missing/extra; infrastructure failure cannot be imputed')
    check({path.name for path in directory.glob('result-*.json')} == _expected_result_names(),
          'Result records missing/extra')
    for path in directory.glob('i*.json'):
        record = _read(path)
        _fields(record, ('request', 'request_hash', 'status', 'raw_text', 'raw_response',
                         'outcome', 'usage', 'started_unix', 'wall_seconds',
                         'tokenizer_prompt_tokens'), 'call record')
        check(record['status'] in ('ok', 'format_violation'),
              f'Unresolved infrastructure call: {path.name}')
        check(_finite_nonnegative(record['wall_seconds']), 'Invalid recorded call duration')
        check(_finite_nonnegative(record['started_unix']) and
              started <= record['started_unix'] <= finished, 'Invalid recorded call start time')
        tokens = record['tokenizer_prompt_tokens']
        check(type(tokens) is int and tokens >= 0 and tokens + config['max_tokens'] <= 4096,
              'Missing or out-of-budget logged request token count')
    gold_file = _read(gold_path)
    _fields(gold_file, ('dataset_sha256', 'gold'), 'offline scoring gold')
    expected_dataset = plan['metadata']['file_sha256']
    check(gold_file['dataset_sha256'] == expected_dataset, 'Gold/dataset checksum mismatch')
    gold = gold_file['gold']
    check(digest(gold) == plan['metadata']['scoring_gold_sha256'], 'Scoring labels changed after input freeze')
    check(isinstance(gold, dict) and all(view['id'] in gold for view in views),
          'Scoring gold missing planned question IDs')
    audit, rows = Audit(directory, config), []
    for item, view in enumerate(views):
        saved_result = _read(directory / f'result-{item}.json')
        recreated = run_item(audit, design, view, item)
        check(recreated == saved_result, f'Recreated result mismatch: item {item}')
        rows.append(question_metrics(recreated, view, gold[view['id']]))
    check(len(audit.calls) == len(audit.keys) == CALLS, 'Audit did not visit all actual calls')
    calls = audit.calls
    usage_complete = all(isinstance(call['usage'], dict) and
                         all(type(call['usage'].get(key)) is int and call['usage'][key] >= 0
                             for key in ('prompt_tokens', 'completion_tokens')) for call in calls)
    valid_calls = sum(call['outcome']['format_valid'] for call in calls)
    diagnostics = Counter(code for call in calls for code in call['outcome']['diagnostics'])
    groups = group_metrics(rows)
    summary = {
        'status': 'CITATION_INTERFACE_V2_DESCRIPTIVE_DIAGNOSTIC',
        'protocol': design['protocol'], 'model': config['model'],
        'questions': ITEMS, 'actual_calls': CALLS, 'bootstrap_unit': 'question',
        'groups': groups, 'interface_readiness': interface_readiness(groups),
        'primary_descriptive_contrasts': paired_contrasts(rows, design, PRIMARY_CONTRASTS),
        'exploratory_prompt_decoder_contrasts': paired_contrasts(rows, design, EXPLORATORY_CONTRASTS),
        'context_contrasts': paired_contrasts(rows, design, CONTEXT_CONTRASTS),
        'all_calls_format_valid_count': valid_calls,
        'all_calls_format_valid_rate': valid_calls / CALLS,
        'all_calls_format_violation_count': CALLS - valid_calls,
        'all_calls_diagnostic_counts': dict(sorted(diagnostics.items())),
        'diagnostic_count_basis': 'Outputs carrying each diagnostic; categories can overlap',
        'metric_basis': 'All planned outputs; format-invalid outputs score zero on every answer/support/joint metric',
        'decoder_packages': plan['decoder_packages'],
        'server_engine': plan['server_engine'],
        'decoder_interpretation': 'Generic json_object versus bounded guided_grammar on the recorded vLLM V1 xgrammar engine. Grammar constraints can change content as well as format; this is not a DICE efficacy contrast.',
        'usage_complete': usage_complete,
        'prompt_tokens': sum(call['usage']['prompt_tokens'] for call in calls) if usage_complete else None,
        'completion_tokens': sum(call['usage']['completion_tokens'] for call in calls) if usage_complete else None,
        'sum_call_seconds': sum(call['wall_seconds'] for call in calls),
        'median_call_seconds': median(call['wall_seconds'] for call in calls),
        'recorded_run_elapsed_seconds': finished - started,
        'timing_basis': 'Recorded API-call durations and run timestamps, not inferred or billed duration',
        'integrity': 'Every recorded request, raw response, parsed outcome, schedule and result reconstructed against frozen/current sources and recorded server metadata; not independent execution attestation or a local rerun of the remote grammar compiler',
        'warning': 'Eight selected short-context development questions, not 64 independent samples. Invalid outputs remain in every denominator; infrastructure failure blocks scoring. No dropping, repair, imputation, p-values, efficacy claim, or automatic next batch. Question-only performance does not establish memorization. No pooling with failed citation-interface-v1 or earlier natural-repetition observations.',
        'design_limits': design.get('limits', []),
    }
    hashes = {
        'raw_input_files': {path.relative_to(root).as_posix(): _file_hash(path)
                            for path in sorted(directory.rglob('*')) if path.is_file()},
        'gold_file': {'sha256': _file_hash(gold_path), 'dataset_sha256': expected_dataset},
        'logical_inputs': {'plan_sha256': manifest['plan_sha256'],
                           'design_sha256': plan['design_sha256'],
                           'source_sha256': plan['source_sha256'],
                           'views_sha256': digest(views),
                           'candidate_sha256': plan['candidate_sha256'],
                           'chat_template_sha256': plan['chat_template_sha256'],
                           'decoder_packages_sha256': digest(plan['decoder_packages']),
                           'grammar_preflight_sha256': digest(plan['grammar_preflight']),
                           'server_engine_sha256': digest(plan['server_engine'])},
    }
    return summary, rows, hashes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--gold')
    parser.add_argument('--output')
    parser.add_argument('--audit-only', action='store_true')
    args = parser.parse_args(argv)
    root = Path(args.run)
    if args.audit_only:
        print(json.dumps(audit_coverage(root), indent=2, allow_nan=False))
        return
    if not args.gold:
        parser.error('--gold is required unless --audit-only is used')
    output = Path(args.output) if args.output else root / 'analysis'
    check(not output.exists(), 'Analysis output exists; refuse overwrite')
    summary, rows, hashes = summarize(root, Path(args.gold))
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'summary.json', summary)
    save(output / 'per_question_rows.json', rows)
    save(output / 'input_hashes.json', hashes)
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
