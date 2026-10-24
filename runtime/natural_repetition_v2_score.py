"""Offline, frozen-request audit for the 32-question repetition v2 diagnostic.

Model format violations score zero on all planned answer/support/joint metrics.
Missing, failed and pending infrastructure records instead block scoring. This
module never generates or repairs model data and never chooses the next batch.
"""

import argparse
from collections import Counter
from itertools import combinations
import json
from pathlib import Path
from statistics import mean, median

from .citation_interface_score import _fields, _sha256, score_output
from .citation_interface_v2 import validate_engine_evidence
from .dependency_probe import check_server, sources
from .e1 import digest, save
from .natural_qa_data import OFFICIAL_METRIC_NAMES, normalize_answer, seeded_order
from .natural_repetition_score import _file_hash, _finite_nonnegative, _read
from .natural_repetition_v2 import (
    Audit, CITATION_CANDIDATES_SHA, DATASET_SHA, JOBS, NATURAL_CANDIDATES_SHA,
    run_item, validate_design, validate_control,
)
from .probe_score import check, interval


ITEMS, CALLS = 32, 544
RECIPIENT_GROUPS = ('once', 'replay', 'position', 'padding')
GROUP_JOBS = {group: tuple(f'{group}_a{agent}' for agent in range(3))
              for group in RECIPIENT_GROUPS}
GROUP_JOBS.update(single_union=('single_union',), question_only=('question_only',))
PRIMARY_CONTRASTS = {
    'raw_replay_minus_once_answer_f1': ('replay', 'once', 'f1'),
    'raw_replay_minus_once_joint_f1': ('replay', 'once', 'joint_f1'),
}
SECONDARY_CONTRASTS = {
    f'replay_minus_{control}_{label}': ('replay', control, metric)
    for control in ('position', 'padding')
    for label, metric in (('answer_f1', 'f1'), ('joint_f1', 'joint_f1'))
}
CAPABILITY_CONTRASTS = {
    f'single_union_minus_question_only_{label}': ('single_union', 'question_only', metric)
    for label, metric in (('answer_f1', 'f1'), ('joint_f1', 'joint_f1'))
}
ALL_CONTRASTS = {**PRIMARY_CONTRASTS, **SECONDARY_CONTRASTS, **CAPABILITY_CONTRASTS}


def target_coverage(view, gold, target_ids):
    """Post-run annotation only; selection must never consult these labels."""
    lookup = {unit['id']: (unit['title'], unit['sentence_index']) for unit in view['units']}
    check(isinstance(target_ids, list) and len(target_ids) == len(set(target_ids)) == 3
          and set(target_ids) <= set(lookup), 'Malformed selected target IDs')
    targets = {lookup[value] for value in target_ids}
    supports = {tuple(value) for value in gold['supporting_facts']}
    overlap = len(targets & supports)
    return {'target_ids': target_ids, 'selected_units': len(targets),
            'gold_support_units': len(supports), 'target_support_overlap': overlap,
            'fraction_targets_that_are_gold_support': overlap / len(targets),
            'fraction_gold_support_covered': overlap / len(supports) if supports else None,
            'has_any_gold_support': bool(overlap),
            'basis': 'Post-run supporting-fact annotation; not used for selection or prompt construction'}


def recipient_pair_metrics(outcomes, scores, gold):
    """Three unordered recipient pairs, never three independent observations.

    Wrong means normalized-answer non-exact-match, not necessarily zero token F1.
    Invalid pairs retain a separate count and stay in the all-planned denominator.
    Empty but schema-valid answers remain in the denominator, but are not
    counted as convergent substantive wrong answers; report them separately.
    """
    check(len(outcomes) == len(scores) == 3, 'Recipient pair metrics require three agents')
    expected = normalize_answer(gold['answer'])
    invalid = same_wrong = same_correct = empty_pairs = equal_empty_pairs = 0
    for left, right in combinations(range(3), 2):
        if not all(scores[index]['format_valid'] and scores[index]['valid_output']
                   for index in (left, right)):
            invalid += 1
            continue
        answers = [normalize_answer(outcomes[index]['assessment']['answer'])
                   for index in (left, right)]
        if not all(answers):
            empty_pairs += 1
            equal_empty_pairs += int(answers[0] == answers[1] == '')
            continue
        if answers[0] == answers[1] and answers[0] != expected:
            same_wrong += 1
        elif answers[0] == answers[1] == expected:
            same_correct += 1
    valid_pairs = 3 - invalid
    return {'planned_pairs': 3, 'valid_pairs': valid_pairs, 'invalid_pairs': invalid,
            'same_wrong_answer_pairs': same_wrong,
            'same_correct_answer_pairs': same_correct,
            'nonempty_equal_answer_pairs': same_wrong + same_correct,
            'empty_answer_pairs': empty_pairs, 'equal_empty_answer_pairs': equal_empty_pairs,
            'same_wrong_answer_pair_rate_all_planned': same_wrong / 3,
            'same_correct_answer_pair_rate_all_planned': same_correct / 3,
            'nonempty_equal_answer_pair_rate_all_planned': (same_wrong + same_correct) / 3,
            'same_wrong_answer_pair_rate_valid_only': same_wrong / valid_pairs if valid_pairs else None,
            'invalid_pair_rate': invalid / 3}


def question_metrics(result, view, gold, control):
    """Collapse recipient metrics within each question before aggregation."""
    check(result['id'] == view['id'], 'Scoring result/view identity mismatch')
    check(set(result['outputs']) == set(JOBS), 'Incomplete or extra terminal outputs')
    check(len(result['initial']) == 3, 'Incomplete initial outputs')
    targets = control['target_ids']
    check(result['target_ids'] == targets, 'Result/control target mismatch')
    outputs = {job: score_output(result['outputs'][job], view, gold) for job in JOBS}
    initial = [score_output(outcome, view, gold) for outcome in result['initial']]
    groups, pairs = {}, {}
    for group, jobs in GROUP_JOBS.items():
        scores = [outputs[job] for job in jobs]
        outcomes = [result['outputs'][job] for job in jobs]
        selected = []
        for output, score in zip(outcomes, scores):
            citations = set(output['assessment']['citations']) if score['valid_output'] else set()
            selected.append(len(citations & set(targets)))
        diagnostics = Counter(code for score in scores for code in score['diagnostics'])
        reasons = Counter(score['invalid_reason'] for score in scores if score['invalid_reason'])
        groups[group] = {
            **{metric: mean(score[metric] for score in scores) for metric in OFFICIAL_METRIC_NAMES},
            'outputs': len(scores),
            'format_valid_count': sum(score['format_valid'] for score in scores),
            'valid_output_count': sum(score['valid_output'] for score in scores),
            'diagnostic_counts': dict(sorted(diagnostics.items())),
            'invalid_reason_counts': dict(sorted(reasons.items())),
            'outputs_citing_any_target': sum(value > 0 for value in selected),
            'target_selected_citation_rate_all_planned': mean(value > 0 for value in selected),
            'mean_fraction_target_ids_cited_all_planned': mean(value / len(targets) for value in selected),
        }
        if group in RECIPIENT_GROUPS:
            pairs[group] = recipient_pair_metrics(outcomes, scores, gold)
    contrasts = {name: groups[plus][metric] - groups[minus][metric]
                 for name, (plus, minus, metric) in ALL_CONTRASTS.items()}
    return {'item': result['item'], 'id': view['id'], 'groups': groups,
            'contrasts': contrasts, 'outputs': outputs, 'initial_outputs': initial,
            'exploratory_recipient_pairs': pairs,
            'exploratory_target_coverage': target_coverage(view, gold, targets)}


def group_metrics(rows):
    check(bool(rows), 'No questions to aggregate')
    summaries = {}
    for group in GROUP_JOBS:
        values = [row['groups'][group] for row in rows]
        count = sum(value['outputs'] for value in values)
        valid_format = sum(value['format_valid_count'] for value in values)
        valid_output = sum(value['valid_output_count'] for value in values)
        diagnostics, reasons = Counter(), Counter()
        for value in values:
            diagnostics.update(value['diagnostic_counts'])
            reasons.update(value['invalid_reason_counts'])
        summaries[group] = {
            **{metric: mean(value[metric] for value in values) for metric in OFFICIAL_METRIC_NAMES},
            'questions': len(rows), 'all_planned_outputs': count,
            'actual_terminal_outputs': count,
            'format_valid_count': valid_format, 'format_valid_rate': valid_format / count,
            'format_violation_count': count - valid_format,
            'valid_output_count': valid_output, 'valid_output_rate': valid_output / count,
            'diagnostic_counts': dict(sorted(diagnostics.items())),
            'invalid_reason_counts': dict(sorted(reasons.items())),
            'outputs_citing_any_target': sum(value['outputs_citing_any_target'] for value in values),
            'target_selected_citation_rate_all_planned': mean(
                value['target_selected_citation_rate_all_planned'] for value in values),
            'mean_fraction_target_ids_cited_all_planned': mean(
                value['mean_fraction_target_ids_cited_all_planned'] for value in values),
        }
    return summaries


def exploratory_metrics(rows):
    pairs = {}
    for group in RECIPIENT_GROUPS:
        values = [row['exploratory_recipient_pairs'][group] for row in rows]
        totals = {key: sum(value[key] for value in values) for key in
                  ('planned_pairs', 'valid_pairs', 'invalid_pairs', 'same_wrong_answer_pairs',
                   'same_correct_answer_pairs', 'nonempty_equal_answer_pairs',
                   'empty_answer_pairs', 'equal_empty_answer_pairs')}
        pairs[group] = {
            **totals, 'questions': len(rows),
            'same_wrong_answer_pair_rate_all_planned': totals['same_wrong_answer_pairs'] / totals['planned_pairs'],
            'same_correct_answer_pair_rate_all_planned': totals['same_correct_answer_pairs'] / totals['planned_pairs'],
            'nonempty_equal_answer_pair_rate_all_planned': totals['nonempty_equal_answer_pairs'] / totals['planned_pairs'],
            'same_wrong_answer_pair_rate_valid_only': (totals['same_wrong_answer_pairs'] / totals['valid_pairs']
                                                       if totals['valid_pairs'] else None),
            'invalid_pair_rate': totals['invalid_pairs'] / totals['planned_pairs'],
        }
    coverage = [row['exploratory_target_coverage'] for row in rows]
    defined = [value['fraction_gold_support_covered'] for value in coverage
               if value['fraction_gold_support_covered'] is not None]
    return {
        'recipient_pairs': pairs,
        'recipient_pair_interpretation': 'Exploratory normalized nonempty equal-wrong-answer counts, wrong defined by answer EM. The three within-question pairs overlap and are not independent samples or a test of statistical independence. Invalid and empty-answer pairs are separate, remain in the all-planned denominator, and are not counted as substantive wrong agreement.',
        'selected_target_coverage': {
            'questions': len(rows),
            'questions_with_any_gold_support_target': sum(value['has_any_gold_support'] for value in coverage),
            'mean_fraction_targets_that_are_gold_support': mean(value['fraction_targets_that_are_gold_support'] for value in coverage),
            'mean_fraction_gold_support_covered': mean(defined) if defined else None,
            'questions_with_defined_gold_support_denominator': len(defined),
            'basis': 'Post-run annotation only; targets chosen without labels. No subgroup efficacy tests or question selection.',
        },
    }


def paired_contrasts(rows, design, contrasts=None):
    check(bool(rows), 'No question pairs to bootstrap')
    contrasts = ALL_CONTRASTS if contrasts is None else contrasts
    return {name: {
        **interval([row['contrasts'][name] for row in rows], design['bootstrap_seed'],
                   design['bootstrap_resamples'], level=.95),
        'questions': len(rows), 'bootstrap_unit': 'question',
        'bootstrap_seed': design['bootstrap_seed'], 'bootstrap_resamples': design['bootstrap_resamples'],
        'plus_group': plus, 'minus_group': minus, 'metric': metric,
        'interpretation': 'Descriptive unadjusted interval; no p-value, multiplicity-adjusted inference or efficacy claim',
    } for name, (plus, minus, metric) in contrasts.items()}


def _expected_call_names():
    return {f'i{item}-{job}.json' for item in range(ITEMS)
            for job in (*[f'initial-a{agent}' for agent in range(3)], *JOBS)}


def _expected_result_names():
    return {f'result-{item}.json' for item in range(ITEMS)}


def audit_coverage(root):
    """Read statuses/counts only, no gold, effect estimates or file writes."""
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
                        type(completion.get('completed_items')) is int and completion['completed_items'] == ITEMS
                        and type(completion.get('calls')) is int and completion['calls'] == CALLS)
        except (OSError, ValueError, TypeError):
            malformed.append(completion_path.relative_to(root).as_posix())
    results = list(root.glob('shard-*/result-*.json'))
    exact_names = ({path.name for path in paths} == _expected_call_names() and
                   {path.name for path in results} == _expected_result_names() and
                   {path.name for path in root.glob('shard-*') if path.is_dir()} == {'shard-000'})
    return {'kind': 'COVERAGE_ONLY_NO_EFFECT_ESTIMATES',
            'planned_calls': CALLS, 'observed_call_files': len(paths),
            'call_status_counts': dict(sorted(statuses.items())),
            'planned_items': ITEMS, 'completed_item_files': len(results),
            'completion_present': completion_path.is_file(),
            'completion_counts_valid': complete, 'malformed_records': malformed,
            'coverage_counts_complete': (len(paths) == CALLS and len(results) == ITEMS
                and exact_names and complete and not malformed
                and sum(statuses[key] for key in ('ok', 'format_violation')) == CALLS),
            'warning': 'Coverage is not integrity verification. Full scoring reconstructs every request and result. Do not impute, silently retry, repair, or subset pending/failed calls.'}


def _check_frozen_plan(plan, manifest, config):
    _fields(plan, ('design', 'design_sha256', 'views', 'controls', 'metadata', 'eligibility',
                  'candidate_sha256', 'tokenizer_class', 'chat_template_sha256', 'actual_calls',
                  'source_sha256', 'source_bundle', 'model_content_sha256', 'decoder_packages',
                  'server_engine'), 'plan')
    design, views = plan['design'], plan['views']
    validate_design(design)
    check(design.get('bootstrap_seed') == 10103028 and design.get('bootstrap_resamples') == 2000,
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
    check(all(isinstance(view, dict) and set(view) == {'id', 'question', 'units'} for view in views),
          'Frozen views must be label-free projections')
    check(len({view['id'] for view in views}) == ITEMS, 'Duplicate planned question IDs')
    check(manifest.get('no_gold_in_runtime') is True, 'Missing runtime label-isolation declaration')
    check(all(config.get(key) == design[key] for key in ('temperature', 'top_p', 'max_tokens')),
          'Config/design mismatch')
    check(all(_sha256(plan[key]) for key in ('candidate_sha256', 'chat_template_sha256', 'model_content_sha256')),
          'Missing/malformed frozen candidate/tokenizer/model hash')
    check(isinstance(plan['tokenizer_class'], str) and bool(plan['tokenizer_class']), 'Missing tokenizer class')
    check(plan['model_content_sha256'] == config.get('model_content_sha256'), 'Model content mismatch')
    controls = plan['controls']
    check(isinstance(controls, dict) and set(controls) == {view['id'] for view in views},
          'Missing/extra frozen per-question controls')
    for view in views:
        validate_control(view, controls[view['id']], design)
    eligibility = plan['eligibility']
    check(isinstance(eligibility, list) and len(eligibility) == 200 and
          all(isinstance(entry, dict) and 'id' in entry and type(entry.get('selected')) is bool
              and type(entry.get('eligible')) is bool for entry in eligibility),
          'Missing/malformed frozen eligibility')
    check([entry['id'] for entry in eligibility if entry['selected']] == [view['id'] for view in views],
          'Frozen eligibility/selected views mismatch')
    check([entry['id'] for entry in eligibility if entry['selected']] ==
          [entry['id'] for entry in eligibility if entry['eligible']][:ITEMS],
          'Frozen selection is not first 32 eligible questions')
    packages = plan['decoder_packages']
    check(isinstance(packages, dict) and
          {'vllm', 'xgrammar', 'outlines', 'outlines_core', 'transformers'} <= set(packages) and
          all(isinstance(name, str) and isinstance(value, str) and value for name, value in packages.items()),
          'Missing/malformed decoder package freeze')
    check(packages.get('vllm') == config.get('packages', {}).get('vllm') == '0.8.3',
          'Frozen vLLM package mismatch')
    check(packages.get('transformers') == config.get('packages', {}).get('transformers'),
          'Frozen tokenizer package mismatch')
    validate_engine_evidence(plan['server_engine'])
    metadata = plan['metadata']
    _fields(metadata, ('file_sha256', 'scoring_gold_sha256', 'excluded_ids', 'all_ordered_ids',
                      'natural_candidates_file_sha256', 'citation_candidates_file_sha256'), 'dataset metadata')
    check(metadata['file_sha256'] == DATASET_SHA and
          metadata['natural_candidates_file_sha256'] == NATURAL_CANDIDATES_SHA and
          metadata['citation_candidates_file_sha256'] == CITATION_CANDIDATES_SHA,
          'Frozen dataset/exclusion packet provenance mismatch')
    excluded, ordered = metadata['excluded_ids'], metadata['all_ordered_ids']
    check(isinstance(excluded, list) and all(isinstance(value, str) for value in excluded) and
          len(excluded) == len(set(excluded)) == 264, 'Frozen exclusion frame mismatch')
    check(isinstance(ordered, list) and all(isinstance(value, str) for value in ordered) and
          len(ordered) == len(set(ordered)) == 7141 and not set(ordered) & set(excluded),
          'Frozen sampling frame mismatch')
    check([row['id'] for row in seeded_order([{'id': value} for value in ordered], design['seed'])] == ordered,
          'Frozen sampling frame order mismatch')
    check([entry['id'] for entry in eligibility] == ordered[:200], 'Frozen candidate eligibility order mismatch')
    check(not {view['id'] for view in views} & set(excluded), 'Selected views overlap prior candidates')
    return design, views, controls


def summarize(root, gold_path):
    """Return (summary, per-question rows, input hashes), read-only after audit."""
    root, gold_path = Path(root), Path(gold_path)
    directory = root / 'shard-000'
    check({path.name for path in root.glob('shard-*') if path.is_dir()} == {'shard-000'},
          'Exactly shard-000 is required')
    check(not list(directory.glob('control-check-failure-*.json')),
          'Control failure artifact present; do not score or silently resume')
    manifest = _read(directory / 'manifest.json')
    _fields(manifest, ('plan', 'plan_sha256', 'config', 'started_unix', 'planned_calls'), 'manifest')
    plan, config = manifest['plan'], manifest['config']
    check(isinstance(config, dict), 'Malformed frozen config')
    design, views, controls = _check_frozen_plan(plan, manifest, config)
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
        _fields(record, ('request', 'request_hash', 'status', 'raw_text', 'raw_response', 'outcome',
                         'usage', 'started_unix', 'wall_seconds', 'tokenizer_prompt_tokens'), 'call record')
        check(record['status'] in ('ok', 'format_violation'), f'Unresolved infrastructure call: {path.name}')
        check(_finite_nonnegative(record['wall_seconds']), 'Invalid recorded call duration')
        check(_finite_nonnegative(record['started_unix']) and started <= record['started_unix'] <= finished,
              'Invalid recorded call start time')
        tokens = record['tokenizer_prompt_tokens']
        check(type(tokens) is int and tokens >= 0 and tokens + config['max_tokens'] <= 4096,
              'Missing or out-of-budget logged request token count')
    gold_file = _read(gold_path)
    _fields(gold_file, ('dataset_sha256', 'gold'), 'offline scoring gold')
    expected_dataset = plan['metadata']['file_sha256']
    check(gold_file['dataset_sha256'] == expected_dataset, 'Gold/dataset checksum mismatch')
    gold = gold_file['gold']
    check(digest(gold) == plan['metadata']['scoring_gold_sha256'], 'Scoring labels changed after input freeze')
    check(isinstance(gold, dict) and all(view['id'] in gold for view in views), 'Scoring gold missing question IDs')
    audit, rows = Audit(directory, config), []
    for item, view in enumerate(views):
        saved_result = _read(directory / f'result-{item}.json')
        recreated = run_item(audit, design, view, item, controls[view['id']])
        check(recreated == saved_result, f'Recreated result mismatch: item {item}')
        rows.append(question_metrics(recreated, view, gold[view['id']], controls[view['id']]))
    check(len(audit.calls) == len(audit.keys) == CALLS, 'Audit did not visit all actual calls')
    calls = audit.calls
    # Recorded local prompt lengths are checked by Audit.run_item. Independently
    # cross-check the server-reported lengths when available, without guessing
    # missing usage or silently accepting a failed matching control.
    api_matched, api_unknown = 0, []
    for item in range(ITEMS):
        for agent in range(3):
            records = [_read(directory / f'i{item}-{arm}_a{agent}.json')
                       for arm in ('replay', 'padding')]
            counts = [(record.get('usage') or {}).get('prompt_tokens')
                      if isinstance(record.get('usage'), dict) else None for record in records]
            if all(type(value) is int and value >= 0 for value in counts):
                check(counts[0] == counts[1], 'Server-reported replay/padding prompt lengths differ')
                api_matched += 1
            else:
                api_unknown.append({'item': item, 'agent': agent})
    usage_complete = all(isinstance(call['usage'], dict) and
                         all(type(call['usage'].get(key)) is int and call['usage'][key] >= 0
                             for key in ('prompt_tokens', 'completion_tokens')) for call in calls)
    valid_calls = sum(call['outcome']['format_valid'] for call in calls)
    diagnostics = Counter(code for call in calls for code in call['outcome']['diagnostics'])
    groups = group_metrics(rows)
    summary = {
        'status': 'NATURAL_CONTEXT_REPETITION_V2_DESCRIPTIVE_DIAGNOSTIC',
        'protocol': design['protocol'], 'model': config['model'], 'questions': ITEMS, 'actual_calls': CALLS,
        'paired_agents_per_question': 3, 'bootstrap_unit': 'question', 'groups': groups,
        'primary_descriptive_contrasts': paired_contrasts(rows, design, PRIMARY_CONTRASTS),
        'secondary_control_contrasts': paired_contrasts(rows, design, SECONDARY_CONTRASTS),
        'capability_descriptive_contrasts': paired_contrasts(rows, design, CAPABILITY_CONTRASTS),
        'exploratory': exploratory_metrics(rows),
        'review_before_any_continuation': {
            'single_union_answer_f1': groups['single_union']['f1'],
            'single_union_joint_f1': groups['single_union']['joint_f1'],
            'single_union_minus_question_only_answer_f1': groups['single_union']['f1'] - groups['question_only']['f1'],
            'single_union_format_valid_rate': groups['single_union']['format_valid_rate'],
            'all_groups_format_valid_rate': {group: value['format_valid_rate'] for group, value in groups.items()},
            'control_request_reconstruction_complete': True,
            'server_reported_token_matched_pairs': api_matched,
            'server_reported_token_match_unknown_pairs': api_unknown,
            'server_reported_token_match_complete': api_matched == ITEMS * 3,
            'automatic_continuation': False,
            'interpretation': 'Descriptive capability, interface and control checks for human interpretation; no fitted significance threshold or DICE efficacy gate.',
        },
        'all_calls_format_valid_count': valid_calls, 'all_calls_format_valid_rate': valid_calls / CALLS,
        'all_calls_format_violation_count': CALLS - valid_calls,
        'all_calls_diagnostic_counts': dict(sorted(diagnostics.items())),
        'diagnostic_count_basis': 'Outputs carrying each diagnostic; categories can overlap',
        'metric_basis': 'Every planned output remains in answer/support/joint denominators; invalid outputs receive zero. No repair or conditional-valid primary scoring.',
        'decoder_packages': plan['decoder_packages'], 'server_engine': plan['server_engine'],
        'usage_complete': usage_complete,
        'prompt_tokens': sum(call['usage']['prompt_tokens'] for call in calls) if usage_complete else None,
        'completion_tokens': sum(call['usage']['completion_tokens'] for call in calls) if usage_complete else None,
        'sum_call_seconds': sum(call['wall_seconds'] for call in calls),
        'median_call_seconds': median(call['wall_seconds'] for call in calls),
        'recorded_run_elapsed_seconds': finished - started,
        'timing_basis': 'Recorded API-call durations and run timestamps, not inferred or billed duration',
        'integrity': 'Every request, raw response, parsed outcome, schedule, control and result reconstructed against frozen/current sources and recorded server metadata. Logged tokenizer counts cross-checked for controls; not independent model execution or a rerun of the remote tokenizer.',
        'warning': 'Thirty-two short-context development questions, not 96 independent recipient pairs or 544 independent samples. Position and length controls are not semantically inert; contrasts do not uniquely identify an independence mechanism. No peer verdict intervention, confidence/calibration measurement, DICE validation, p-values, pooling with earlier batches, or automatic next batch. Question-only performance does not establish memorization.',
        'design_limits': design.get('limits', []),
    }
    hashes = {
        'raw_input_files': {path.relative_to(root).as_posix(): _file_hash(path)
                            for path in sorted(directory.rglob('*')) if path.is_file()},
        'gold_file': {'sha256': _file_hash(gold_path), 'dataset_sha256': expected_dataset},
        'logical_inputs': {'plan_sha256': manifest['plan_sha256'], 'design_sha256': plan['design_sha256'],
                           'source_sha256': plan['source_sha256'], 'views_sha256': digest(views),
                           'controls_sha256': digest(controls), 'candidate_sha256': plan['candidate_sha256'],
                           'chat_template_sha256': plan['chat_template_sha256'],
                           'decoder_packages_sha256': digest(plan['decoder_packages']),
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
