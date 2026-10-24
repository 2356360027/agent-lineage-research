"""Offline audited, question-level scoring for the natural repetition diagnostic.

Missing/infrastructure-failed calls block full scoring. Recorded model format
violations instead receive zero on every all-planned output metric. No inference,
repair, fee estimation, p-values, or method-efficacy conclusion is performed.
"""

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median

from .dependency_probe import check_server, sources
from .e1 import digest, save
from .format_probe_v2 import Audit
from .natural_qa_data import OFFICIAL_METRIC_NAMES, score_prediction
from .natural_repetition import JOBS, run_item, unique_json, validate_design
from .probe_score import check, interval


GROUP_JOBS = {
    'once': ('once_a0', 'once_a1', 'once_a2'),
    'replay': ('replay_a0', 'replay_a1', 'replay_a2'),
    'single_union': ('single_union',),
    'question_only': ('question_only',),
}
CONTRASTS = {
    'raw_replay_minus_once_answer_f1': 'f1',
    'raw_replay_minus_once_joint_f1': 'joint_f1',
}


def _read(path):
    path = Path(path)
    check(path.is_file(), f'Missing required input: {path.name}')
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_json)


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _finite_nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def score_output(outcome, view, gold):
    """Score an audited output; format-invalid assessments cannot earn credit."""
    check(isinstance(outcome, dict) and type(outcome.get('format_valid')) is bool,
          'Missing or malformed audited outcome; not an imputable format violation')
    prediction = outcome.get('assessment') if outcome['format_valid'] else None
    scores = score_prediction(prediction, view, gold)
    if not outcome['format_valid']:
        scores['invalid_reason'] = outcome.get('violation') or 'model format violation'
    return {**scores, 'format_valid': outcome['format_valid']}


def question_metrics(result, view, gold):
    """Retain output scores, but collapse agents within each question first."""
    check(result['id'] == view['id'], 'Scoring result/view identity mismatch')
    check(set(result['outputs']) == set(JOBS), 'Incomplete or extra terminal outputs')
    check(len(result['initial']) == 3, 'Incomplete initial outputs')
    outputs = {job: score_output(result['outputs'][job], view, gold) for job in JOBS}
    initial = [score_output(outcome, view, gold) for outcome in result['initial']]
    groups = {}
    for group, jobs in GROUP_JOBS.items():
        scores = [outputs[job] for job in jobs]
        groups[group] = {
            **{metric: mean(score[metric] for score in scores) for metric in OFFICIAL_METRIC_NAMES},
            'outputs': len(scores),
            'format_valid_count': sum(score['format_valid'] for score in scores),
            'valid_output_count': sum(score['valid_output'] for score in scores),
        }
    contrasts = {name: groups['replay'][metric] - groups['once'][metric]
                 for name, metric in CONTRASTS.items()}
    return {'item': result['item'], 'id': view['id'], 'groups': groups,
            'contrasts': contrasts, 'outputs': outputs, 'initial_outputs': initial}


def group_metrics(rows):
    """All-planned group means; invalid outputs remain in every denominator."""
    check(bool(rows), 'No questions to aggregate')
    summaries = {}
    for group in GROUP_JOBS:
        values = [row['groups'][group] for row in rows]
        count = sum(value['outputs'] for value in values)
        valid_format = sum(value['format_valid_count'] for value in values)
        valid_output = sum(value['valid_output_count'] for value in values)
        summaries[group] = {
            **{metric: mean(value[metric] for value in values) for metric in OFFICIAL_METRIC_NAMES},
            'questions': len(rows), 'actual_terminal_outputs': count,
            'format_valid_count': valid_format, 'format_valid_rate': valid_format / count,
            'valid_output_count': valid_output, 'valid_output_rate': valid_output / count,
        }
    return summaries


def paired_contrasts(rows, design):
    """Question bootstrap, not an agent/output bootstrap; descriptive only."""
    check(bool(rows), 'No question pairs to bootstrap')
    return {name: {
        **interval([row['contrasts'][name] for row in rows],
                   design['bootstrap_seed'], design['bootstrap_resamples'], level=.95),
        'questions': len(rows), 'bootstrap_unit': 'question',
        'bootstrap_seed': design['bootstrap_seed'],
        'bootstrap_resamples': design['bootstrap_resamples'],
        'interpretation': 'Descriptive unadjusted interval; no p-value or efficacy test',
    } for name in CONTRASTS}


def audit_coverage(root):
    """Coverage-only inspection, usable on incomplete runs without any gold."""
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
    completion_valid = False
    if completion_path.is_file():
        try:
            completion = _read(completion_path)
            completion_valid = (isinstance(completion, dict)
                                and completion.get('completed_items') == 20
                                and completion.get('calls') == 220)
        except (OSError, ValueError, TypeError):
            malformed.append(completion_path.relative_to(root).as_posix())
    results = list(root.glob('shard-*/result-*.json'))
    return {
        'kind': 'COVERAGE_ONLY_NO_EFFECT_ESTIMATES',
        'planned_calls': 220, 'observed_call_files': len(paths),
        'call_status_counts': dict(sorted(statuses.items())),
        'planned_items': 20, 'completed_item_files': len(results),
        'completion_present': completion_path.is_file(),
        'completion_counts_valid': completion_valid, 'malformed_records': malformed,
        'coverage_counts_complete': (len(paths) == 220 and len(results) == 20
                                    and completion_valid and not malformed
                                    and sum(statuses[key] for key in ('ok', 'format_violation')) == 220),
        'warning': 'Coverage is not integrity verification. Full scoring still reconstructs every request and output. Preserve pending/failed calls; do not impute, retry, or subset them.',
    }


def _expected_call_names(items):
    return {f'i{item}-{job}.json' for item in range(items)
            for job in (*[f'initial-a{agent}' for agent in range(3)], *JOBS)}


def summarize(root, gold_path):
    """Return (summary, per-question rows, input hashes) after complete audit."""
    root, gold_path = Path(root), Path(gold_path)
    directory = root / 'shard-000'
    check({path.name for path in root.glob('shard-*') if path.is_dir()} == {'shard-000'},
          'Exactly shard-000 is required')
    manifest = _read(directory / 'manifest.json')
    plan, config = manifest['plan'], manifest['config']
    design, views = plan['design'], plan['views']
    validate_design(design)
    check(digest(plan) == manifest['plan_sha256'], 'Plan hash mismatch')
    check(digest(design) == plan['design_sha256'], 'Design hash mismatch')
    check(digest(sources()) == plan['source_sha256'] == digest(plan['source_bundle']),
          'Current/recorded frozen source mismatch')
    check(len(views) == 20 and plan['actual_calls'] == manifest['planned_calls'] == 220,
          'Frozen item/call counts mismatch')
    check(manifest.get('no_gold_in_runtime') is True, 'Missing runtime label-isolation declaration')
    check(len({view['id'] for view in views}) == 20, 'Duplicate planned question IDs')
    check(all(config[key] == design[key] for key in ('temperature', 'top_p', 'max_tokens')),
          'Config/design mismatch')
    check(plan['model_content_sha256'] == config['model_content_sha256'], 'Model content mismatch')
    check(config['server_settings_reported']['max_model_len'] == 4096, 'Context setting mismatch')
    check_server(config, _read(directory / 'server_metadata.json'), resolve_paths=False)
    completion = _read(directory / 'completion.json')
    check(completion.get('completed_items') == 20 and completion.get('calls') == 220,
          'Completion record missing full run counts')
    started, finished = manifest.get('started_unix'), completion.get('completed_unix')
    check(_finite_nonnegative(started) and _finite_nonnegative(finished) and finished >= started,
          'Invalid recorded run timestamps')
    check({path.name for path in directory.glob('i*.json')} == _expected_call_names(20),
          'Call records missing/extra; infrastructure failure cannot be imputed')
    check({path.name for path in directory.glob('result-*.json')} ==
          {f'result-{item}.json' for item in range(20)}, 'Result records missing/extra')
    for path in directory.glob('i*.json'):
        record = _read(path)
        check(record.get('status') in ('ok', 'format_violation'),
              f'Unresolved infrastructure call: {path.name}')
        check(_finite_nonnegative(record.get('wall_seconds')), 'Invalid recorded call duration')
        tokens = record.get('tokenizer_prompt_tokens')
        check(type(tokens) is int and 0 <= tokens and tokens + config['max_tokens'] <= 4096,
              'Missing or out-of-budget logged request token count')
    gold_file = _read(gold_path)
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
    check(len(audit.calls) == len(audit.keys) == 220, 'Audit did not visit all actual calls')
    calls = audit.calls
    usage_complete = all(isinstance(call.get('usage'), dict) and
                         all(type(call['usage'].get(key)) is int and call['usage'][key] >= 0
                             for key in ('prompt_tokens', 'completion_tokens')) for call in calls)
    valid_calls = sum(call['outcome']['format_valid'] for call in calls)
    summary = {
        'status': 'NATURAL_CONTEXT_REPETITION_DESCRIPTIVE_DIAGNOSTIC',
        'model': config['model'], 'questions': 20, 'actual_calls': 220,
        'paired_agents_per_question': 3, 'bootstrap_unit': 'question',
        'groups': group_metrics(rows), 'paired_contrasts': paired_contrasts(rows, design),
        'all_calls_format_valid_count': valid_calls,
        'all_calls_format_valid_rate': valid_calls / len(calls),
        'structural_aliases': {
            'canonical_dedup_replay': {'equivalent_to': 'once', 'additional_calls': 0,
                                     'additional_observations': 0,
                                     'meaning': 'Reconstructed byte-identical prompt and seed; alias, not an independently observed treatment'},
        },
        'usage_complete': usage_complete,
        'prompt_tokens': sum(call['usage']['prompt_tokens'] for call in calls) if usage_complete else None,
        'completion_tokens': sum(call['usage']['completion_tokens'] for call in calls) if usage_complete else None,
        'sum_call_seconds': sum(call['wall_seconds'] for call in calls),
        'median_call_seconds': median(call['wall_seconds'] for call in calls),
        'recorded_run_elapsed_seconds': finished - started,
        'timing_basis': 'Recorded API-call durations and run timestamps, not inferred or billed duration',
        'integrity': 'Every recorded request, raw response, parsed outcome, schedule, alias and result reconstructed against frozen/current sources and recorded server metadata; not independent execution attestation',
        'warning': 'Twenty selected short-context development questions; not 60 independent agent pairs. Invalid outputs score zero, infrastructure failures are never imputed. Replay jointly changes length, position and salience; provenance is not statistical independence. Question-only success need not be memorization. No p-values, efficacy claim, or automatic next batch.',
        'design_limits': design.get('limits', []),
    }
    hashes = {
        'raw_input_files': {path.relative_to(root).as_posix(): _file_hash(path)
                            for path in sorted(directory.rglob('*')) if path.is_file()},
        'gold_file': {'sha256': _file_hash(gold_path),
                      'dataset_sha256': expected_dataset},
        'logical_inputs': {'plan_sha256': manifest['plan_sha256'],
                           'design_sha256': plan['design_sha256'],
                           'source_sha256': plan['source_sha256'],
                           'views_sha256': digest(views)},
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
