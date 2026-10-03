"""SOFTWARE FIXTURES ONLY: no fabricated record here is a model observation."""

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import natural_repetition_v2 as runner
from runtime.citation_interface_v2 import evaluate, request
from runtime.dependency_probe import sources
from runtime.e1 import canonical, digest, save
from runtime.natural_qa_data import OFFICIAL_METRIC_NAMES, adapt_hotpot_row, seeded_order
from runtime.natural_repetition_v2_score import (
    ALL_CONTRASTS, CAPABILITY_CONTRASTS, GROUP_JOBS, PRIMARY_CONTRASTS,
    SECONDARY_CONTRASTS, audit_coverage, exploratory_metrics, group_metrics,
    main, paired_contrasts, question_metrics, recipient_pair_metrics,
    score_output, summarize, target_coverage,
)


def design():
    path = Path(__file__).resolve().parents[1] / 'configs' / 'natural_repetition_v2.json'
    return json.loads(path.read_text(encoding='utf-8'))


def example(item_id='SOFTWARE-FIXTURE-0'):
    return adapt_hotpot_row({
        '_id': item_id, 'question': 'Which city?',
        'context': [[f'Document {number}', [f'Sentence {number} mentions Paris city.']]
                    for number in range(10)],
        'answer': 'Paris', 'supporting_facts': [['Document 0', 0]],
    })


def outcome(answer='Paris', citations=None):
    return {'format_valid': True, 'assessment': {
        'answer': answer, 'citations': ['d0s0'] if citations is None else citations},
        'violation': None, 'diagnostics': []}


def example_result(view):
    return {'item': 0, 'id': view['id'], 'initial': [outcome() for _ in range(3)],
            'target_ids': ['d0s0', 'd1s0', 'd2s0'],
            'outputs': {job: outcome(citations=[] if job == 'question_only' else None)
                        for job in runner.JOBS}}


def fake_engine_evidence():
    """Synthetic startup record to exercise checks, never a live attestation."""
    line = ("INFO [SOFTWARE_FIXTURE] Initializing a V1 LLM engine (v0.8.3) "
            "with config: max_seq_len=4096, decoding_config=DecodingConfig("
            "guided_decoding_backend='xgrammar', reasoning_backend=None)")
    return {'kind': 'VLLM_V1_STARTUP_LOG_EVIDENCE', 'version': '0.8.3',
            'backend': 'xgrammar', 'initialization_line': line,
            'initialization_line_sha256': hashlib.sha256(line.encode('utf-8')).hexdigest()}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class CharacterTokenizer:
    """Deterministic software token-count fixture, not a model tokenizer."""
    chat_template = 'SOFTWARE_FIXTURE_ONLY_CHARACTERS'

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=True):
        text = ''.join(value['role'] + '\n' + value['content'] + '\n' for value in messages)
        return list(text.encode('utf-8')) if tokenize else text


class SoftwareFixtureClient:
    """Persist constructed raw JSON to test audit; no model/API is invoked."""
    def __init__(self, directory, config):
        self.directory, self.config = directory, config
        self.tokenizer = CharacterTokenizer()

    def call(self, key, messages, seed, decoder, visible):
        visible = sorted(set(visible))
        payload = request(self.config, messages, seed, decoder, visible)
        prediction = {'answer': 'Paris', 'citations': [visible[0]] if visible else []}
        finish = 'stop'
        if key == 'i0-once_a0':
            prediction['citations'] = ['[d0s0]']
        elif key == 'i1-replay_a1':
            prediction = {}
        elif key == 'i2-position_a2':
            finish = 'length'
        count = runner.old.token_count(self.tokenizer, messages)
        raw = {'model': self.config['model'], 'choices': [{
            'finish_reason': finish, 'message': {'content': canonical(prediction)}}],
            'usage': {'prompt_tokens': count, 'completion_tokens': 4}}
        parsed = evaluate(raw, decoder, self.config['model'], visible)
        save(self.directory / (key + '.json'), {
            'request': payload, 'request_hash': digest(payload), 'decoder': decoder,
            'visible': visible, 'raw_text': canonical(raw), 'raw_response': raw,
            'outcome': parsed, 'status': 'ok' if parsed['format_valid'] else 'format_violation',
            'usage': raw['usage'], 'wall_seconds': .25, 'started_unix': 110.,
            'tokenizer_prompt_tokens': count, 'data_status': runner.DATA_STATUS,
            'software_fixture_only_not_real_inference': True,
        })
        return parsed


def fixture_run(root):
    """Construct 544 explicitly fictional raw records for full offline replay."""
    directory = root / 'shard-000'
    directory.mkdir()
    d = design()
    ordered = [row['id'] for row in seeded_order(
        [{'id': f'SOFTWARE-FIXTURE-{index}'} for index in range(7141)], d['seed'])]
    pairs = [example(item_id) for item_id in ordered[:32]]
    views = [view for view, _ in pairs]
    gold = {view['id']: labels for view, labels in pairs}
    # IDs do not enter the text, so the shared input has byte-identical controls.
    control = runner.prepare_control(views[0], d, CharacterTokenizer())
    assert control is not None, 'Software fixture tokenizer failed to construct control'
    controls = {view['id']: copy.deepcopy(control) for view in views}
    bundle = sources()
    packages = {'vllm': '0.8.3', 'xgrammar': '0.1.17', 'outlines': 'fixture',
                'outlines_core': 'fixture', 'transformers': 'fixture'}
    cfg = {'model': 'SOFTWARE_FIXTURE_ONLY_NOT_A_MODEL_RUN', 'temperature': 0.,
           'top_p': 1., 'max_tokens': 256, 'model_content_sha256': 'a' * 64,
           'model_path': '/fixture', 'served_model_metadata': {'root': '/fixture'},
           'server_settings_reported': {'max_model_len': 4096},
           'packages': {'vllm': '0.8.3', 'transformers': 'fixture'}}
    plan = {
        'design': d, 'design_sha256': digest(d), 'views': views, 'controls': controls,
        'source_bundle': bundle, 'source_sha256': digest(bundle),
        'actual_calls': 544, 'model_content_sha256': 'a' * 64,
        'candidate_sha256': 'b' * 64, 'chat_template_sha256': 'c' * 64,
        'tokenizer_class': 'SOFTWARE_FIXTURE_TOKENIZER', 'decoder_packages': packages,
        'eligibility': [{'id': item_id, 'selected': index < 32, 'eligible': True,
                         'worst_reserved_tokens': 3000, 'reason': None}
                        for index, item_id in enumerate(ordered[:200])],
        'metadata': {'file_sha256': runner.DATASET_SHA, 'scoring_gold_sha256': digest(gold),
                     'natural_candidates_file_sha256': runner.NATURAL_CANDIDATES_SHA,
                     'citation_candidates_file_sha256': runner.CITATION_CANDIDATES_SHA,
                     'excluded_ids': [f'EXCLUDED-SOFTWARE-FIXTURE-{index}' for index in range(264)],
                     'all_ordered_ids': ordered},
        'server_engine': fake_engine_evidence(),
    }
    save(directory / 'manifest.json', {'plan': plan, 'plan_sha256': digest(plan),
         'config': cfg, 'planned_calls': 544, 'no_gold_in_runtime': True, 'started_unix': 100.})
    save(directory / 'server_metadata.json', {'data': [{
        'id': cfg['model'], 'root': '/fixture', 'max_model_len': 4096}]})
    client = SoftwareFixtureClient(directory, cfg)
    for index, view in enumerate(views):
        runner.run_item(client, d, view, index, controls[view['id']], directory)
    save(directory / 'completion.json', {'completed_items': 32, 'calls': 544, 'completed_unix': 260.})
    gold_path = root / 'software_fixture_gold.json'
    save(gold_path, {'dataset_sha256': runner.DATASET_SHA, 'gold': gold})
    return gold_path


class NaturalRepetitionV2ScoreTests(unittest.TestCase):
    def test_invalid_outputs_zero_without_repair(self):
        view, gold = example()
        invalid = dict(outcome(), format_valid=False, violation='fixture failure', diagnostics=['non_stop'])
        scores = score_output(invalid, view, gold)
        self.assertFalse(scores['valid_output'])
        self.assertEqual(scores['diagnostics'], ['non_stop'])
        self.assertTrue(all(scores[key] == 0. for key in OFFICIAL_METRIC_NAMES))
        wrapped = score_output(outcome(citations=['[d0s0]']), view, gold)
        self.assertTrue(all(wrapped[key] == 0. for key in OFFICIAL_METRIC_NAMES))

    def test_primary_secondary_and_question_level_bootstrap(self):
        view, gold = example()
        result = example_result(view)
        result['outputs']['once_a0'] = outcome(answer='London')
        control = {'target_ids': result['target_ids']}
        row = question_metrics(result, view, gold, control)
        groups = group_metrics([row])
        self.assertEqual(set(groups), set(GROUP_JOBS))
        self.assertEqual(groups['once']['all_planned_outputs'], 3)
        self.assertEqual(groups['single_union']['all_planned_outputs'], 1)
        self.assertAlmostEqual(row['contrasts']['raw_replay_minus_once_answer_f1'], 1 / 3)
        self.assertEqual(row['contrasts']['single_union_minus_question_only_joint_f1'], 1.)
        self.assertEqual((len(PRIMARY_CONTRASTS), len(SECONDARY_CONTRASTS), len(CAPABILITY_CONTRASTS)), (2, 4, 2))
        with patch('runtime.natural_repetition_v2_score.interval', return_value={'mean': 0.}) as boot:
            paired_contrasts([copy.deepcopy(row) for _ in range(32)], design())
        self.assertEqual(boot.call_count, len(ALL_CONTRASTS))
        for call in boot.call_args_list:
            self.assertEqual(len(call.args[0]), 32)
            self.assertEqual(call.args[1:3], (10103028, 2000))

    def test_same_wrong_answer_official_normalization_and_invalid_pairs(self):
        view, gold = example()
        outcomes = [outcome(answer='The LONDON!'), outcome(answer='London'), outcome(answer='Paris')]
        scores = [score_output(value, view, gold) for value in outcomes]
        pairs = recipient_pair_metrics(outcomes, scores, gold)
        self.assertEqual(pairs['planned_pairs'], 3)
        self.assertEqual(pairs['same_wrong_answer_pairs'], 1)
        self.assertEqual(pairs['same_wrong_answer_pair_rate_all_planned'], 1 / 3)
        self.assertEqual(pairs['same_correct_answer_pairs'], 0)
        self.assertEqual(pairs['nonempty_equal_answer_pairs'], 1)
        outcomes[2] = dict(outcome(), format_valid=False, violation='invalid', diagnostics=[])
        scores = [score_output(value, view, gold) for value in outcomes]
        pairs = recipient_pair_metrics(outcomes, scores, gold)
        self.assertEqual(pairs['same_wrong_answer_pairs'], 1)
        self.assertEqual(pairs['invalid_pairs'], 2)
        self.assertEqual(pairs['same_wrong_answer_pair_rate_all_planned'], 1 / 3)
        self.assertEqual(pairs['same_wrong_answer_pair_rate_valid_only'], 1.)

    def test_empty_answers_not_substantive_wrong_convergence(self):
        view, gold = example()
        outcomes = [outcome(answer=''), outcome(answer=' the '), outcome(answer='London')]
        scores = [score_output(value, view, gold) for value in outcomes]
        pairs = recipient_pair_metrics(outcomes, scores, gold)
        self.assertEqual(pairs['same_wrong_answer_pairs'], 0)
        self.assertEqual(pairs['same_wrong_answer_pair_rate_all_planned'], 0.)
        self.assertEqual(pairs['invalid_pairs'], 0)
        self.assertEqual(pairs['empty_answer_pairs'], 3)
        self.assertEqual(pairs['equal_empty_answer_pairs'], 1)
        invalids = [dict(outcome(), format_valid=False, violation='invalid', diagnostics=[]) for _ in range(3)]
        pairs = recipient_pair_metrics(invalids, [score_output(value, view, gold) for value in invalids], gold)
        self.assertEqual(pairs['invalid_pairs'], 3)
        self.assertIsNone(pairs['same_wrong_answer_pair_rate_valid_only'])

    def test_correct_agreement_separate_from_same_wrong(self):
        view, gold = example()
        outcomes = [outcome(answer='The Paris'), outcome(answer='Paris!'), outcome(answer='PARIS')]
        pairs = recipient_pair_metrics(outcomes, [score_output(value, view, gold) for value in outcomes], gold)
        self.assertEqual(pairs['same_correct_answer_pairs'], 3)
        self.assertEqual(pairs['same_wrong_answer_pairs'], 0)
        self.assertEqual(pairs['nonempty_equal_answer_pairs'], 3)
        self.assertEqual(pairs['same_correct_answer_pair_rate_all_planned'], 1.)

    def test_target_annotations_postrun_and_all_planned_citation_denominators(self):
        view, gold = example()
        result = example_result(view)
        result['outputs']['once_a1'] = outcome(citations=['d0s0', 'd0s0'])
        result['outputs']['once_a2'] = dict(outcome(), format_valid=False, violation='invalid', diagnostics=[])
        row = question_metrics(result, view, gold, {'target_ids': result['target_ids']})
        self.assertEqual(row['groups']['once']['target_selected_citation_rate_all_planned'], 2 / 3)
        self.assertEqual(row['groups']['once']['mean_fraction_target_ids_cited_all_planned'], 2 / 9)
        self.assertEqual(row['exploratory_target_coverage']['fraction_gold_support_covered'], 1.)
        self.assertEqual(row['exploratory_target_coverage']['fraction_targets_that_are_gold_support'], 1 / 3)
        report = exploratory_metrics([row])
        self.assertEqual(report['recipient_pairs']['once']['planned_pairs'], 3)
        self.assertEqual(report['recipient_pairs']['once']['invalid_pairs'], 2)
        empty_gold = dict(gold, supporting_facts=[])
        self.assertIsNone(target_coverage(view, empty_gold, result['target_ids'])['fraction_gold_support_covered'])

    def test_coverage_is_no_effects_no_gold_and_no_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'shard-000'
            directory.mkdir()
            save(directory / 'i0-once_a0.json', {'status': 'failed'})
            report = audit_coverage(root)
            self.assertEqual(report['planned_calls'], 544)
            self.assertEqual(report['call_status_counts'], {'failed': 1})
            self.assertFalse(report['coverage_counts_complete'])
            self.assertNotIn('groups', report)
            self.assertNotIn('primary_descriptive_contrasts', report)
            with patch('sys.stdout', new_callable=io.StringIO) as stdout:
                main(['--run', str(root), '--audit-only'])
            self.assertEqual(json.loads(stdout.getvalue())['observed_call_files'], 1)
            self.assertFalse((root / 'analysis').exists())

    def test_full_544_fixture_raw_replay_read_only_all_planned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            summary, rows, hashes = summarize(root, gold)
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual((summary['questions'], summary['actual_calls'], len(rows)), (32, 544, 32))
            self.assertEqual(summary['all_calls_format_valid_count'], 541)
            self.assertEqual(summary['all_calls_format_violation_count'], 3)
            self.assertEqual(summary['groups']['once']['all_planned_outputs'], 96)
            self.assertEqual(summary['groups']['once']['format_valid_count'], 95)
            self.assertAlmostEqual(summary['groups']['once']['f1'], 95 / 96)
            self.assertEqual(summary['groups']['question_only']['joint_f1'], 0.)
            self.assertEqual(summary['all_calls_diagnostic_counts']['bracket_only_mismatch'], 1)
            self.assertEqual(summary['all_calls_diagnostic_counts']['non_stop'], 1)
            self.assertAlmostEqual(summary['primary_descriptive_contrasts'][
                'raw_replay_minus_once_answer_f1']['mean'], 0.)
            self.assertTrue(summary['usage_complete'])
            self.assertEqual(summary['completion_tokens'], 2176)
            self.assertEqual(summary['sum_call_seconds'], 136.)
            self.assertEqual(summary['recorded_run_elapsed_seconds'], 160.)
            self.assertEqual(len(hashes['raw_input_files']), 579)
            self.assertIn('controls_sha256', hashes['logical_inputs'])
            self.assertFalse(summary['review_before_any_continuation']['automatic_continuation'])
            self.assertEqual(summary['review_before_any_continuation']['server_reported_token_matched_pairs'], 96)
            self.assertTrue(audit_coverage(root)['coverage_counts_complete'])

    def test_failed_pending_missing_extra_and_missing_completion_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'i0-once_a0.json'
            original = read(path)
            for status in ('failed', 'pending'):
                save(path, dict(original, status=status))
                with self.assertRaisesRegex(ValueError, 'Unresolved infrastructure'):
                    summarize(root, gold)
                self.assertFalse(audit_coverage(root)['coverage_counts_complete'])
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'Call records missing/extra'):
                summarize(root, gold)
            save(path, original)
            extra = root / 'shard-000' / 'i99-extra.json'
            save(extra, {'status': 'ok'})
            with self.assertRaisesRegex(ValueError, 'Call records missing/extra'):
                summarize(root, gold)
            extra.unlink()
            (root / 'shard-000' / 'completion.json').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing required input: completion'):
                summarize(root, gold)

    def test_request_raw_outcome_visibility_schedule_tampering_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'i0-once_a0.json'
            original = read(path)
            changed = copy.deepcopy(original)
            changed['request']['seed'] += 1
            changed['request_hash'] = digest(changed['request'])
            for record, message in (
                (changed, 'Request mismatch'),
                (dict(original, raw_text='{}'), 'Raw mismatch'),
                (dict(original, outcome=outcome(answer='tampered')), 'Outcome mismatch'),
                (dict(original, visible=[]), 'Decoder/visibility mismatch'),
                (dict(original, data_status='UNVERIFIED'), 'Unexpected data status'),
            ):
                save(path, record)
                with self.assertRaisesRegex(ValueError, message):
                    summarize(root, gold)
            save(path, original)
            result_path = root / 'shard-000' / 'result-0.json'
            result = read(result_path)
            result['order'].reverse()
            save(result_path, result)
            with self.assertRaisesRegex(ValueError, 'Recreated result mismatch'):
                summarize(root, gold)

    def test_plan_control_and_full_actual_token_match_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'manifest.json'
            original = read(path)
            for mutation, message in (
                (lambda p: p.update(controls={}), 'per-question controls'),
                (lambda p: next(iter(p['controls'].values())).update(target_ids=['d9s0']), 'Control checksum'),
                (lambda p: p['eligibility'][0].update(eligible=False), 'first 32 eligible'),
                (lambda p: p['server_engine'].update(backend='outlines'), 'engine'),
            ):
                changed = copy.deepcopy(original)
                mutation(changed['plan'])
                changed['plan_sha256'] = digest(changed['plan'])
                save(path, changed)
                with self.assertRaises(ValueError):
                    summarize(root, gold)
            save(path, original)
            with patch('runtime.natural_repetition_v2_score.sources', return_value={'changed': 'fixture'}):
                with self.assertRaisesRegex(ValueError, 'frozen source mismatch'):
                    summarize(root, gold)
            call_path = root / 'shard-000' / 'i0-padding_a0.json'
            record = read(call_path)
            record['tokenizer_prompt_tokens'] += 1
            save(call_path, record)
            with self.assertRaisesRegex(ValueError, 'no longer token-matched'):
                summarize(root, gold)

    def test_frozen_fields_hashes_and_frame_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'manifest.json'
            original = read(path)
            for field in ('controls', 'candidate_sha256', 'eligibility', 'decoder_packages', 'server_engine'):
                changed = copy.deepcopy(original)
                del changed['plan'][field]
                changed['plan_sha256'] = digest(changed['plan'])
                save(path, changed)
                with self.assertRaisesRegex(ValueError, 'frozen record is incomplete'):
                    summarize(root, gold)
            save(path, dict(original, plan_sha256='wrong'))
            with self.assertRaisesRegex(ValueError, 'Plan hash mismatch'):
                summarize(root, gold)
            changed = copy.deepcopy(original)
            order = changed['plan']['metadata']['all_ordered_ids']
            order[0], order[1] = order[1], order[0]
            changed['plan_sha256'] = digest(changed['plan'])
            save(path, changed)
            with self.assertRaisesRegex(ValueError, 'sampling frame order mismatch'):
                summarize(root, gold)

    def test_gold_timestamps_context_model_and_missing_usage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold_path = fixture_run(root)
            gold = read(gold_path)
            save(gold_path, dict(gold, dataset_sha256='changed'))
            with self.assertRaisesRegex(ValueError, 'Gold/dataset checksum'):
                summarize(root, gold_path)
            changed = copy.deepcopy(gold)
            changed['gold'][next(iter(changed['gold']))]['answer'] = 'changed'
            save(gold_path, changed)
            with self.assertRaisesRegex(ValueError, 'Scoring labels changed'):
                summarize(root, gold_path)
            save(gold_path, gold)
            path = root / 'shard-000' / 'i0-once_a0.json'
            original = read(path)
            for field, value, message in (
                ('wall_seconds', -1, 'call duration'), ('started_unix', 99., 'call start time'),
                ('tokenizer_prompt_tokens', 4096, 'request token count'),
                ('tokenizer_prompt_tokens', True, 'request token count'),
            ):
                save(path, dict(original, **{field: value}))
                with self.assertRaisesRegex(ValueError, message):
                    summarize(root, gold_path)
            save(path, original)
            metadata_path = root / 'shard-000' / 'server_metadata.json'
            metadata = read(metadata_path)
            changed = copy.deepcopy(metadata)
            changed['data'][0]['root'] = '/not-the-recorded-model'
            save(metadata_path, changed)
            with self.assertRaisesRegex(ValueError, 'endpoint/path mismatch'):
                summarize(root, gold_path)
            save(metadata_path, metadata)
            changed = copy.deepcopy(original)
            changed['usage'] = changed['raw_response']['usage'] = None
            changed['raw_text'] = canonical(changed['raw_response'])
            save(path, changed)
            summary, _, _ = summarize(root, gold_path)
            self.assertFalse(summary['usage_complete'])
            self.assertIsNone(summary['prompt_tokens'])
            self.assertIsNone(summary['completion_tokens'])

    def test_failed_control_artifact_and_server_reported_length_mismatch_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            failure = root / 'shard-000' / 'control-check-failure-0.json'
            save(failure, {'software_fixture_only': True})
            with self.assertRaisesRegex(ValueError, 'Control failure artifact'):
                summarize(root, gold)
            failure.unlink()
            path = root / 'shard-000' / 'i0-padding_a0.json'
            original = read(path)
            changed = copy.deepcopy(original)
            changed['usage']['prompt_tokens'] += 1
            changed['raw_response']['usage'] = changed['usage']
            changed['raw_text'] = canonical(changed['raw_response'])
            save(path, changed)
            with self.assertRaisesRegex(ValueError, 'Server-reported replay/padding prompt lengths differ'):
                summarize(root, gold)
            changed = copy.deepcopy(original)
            changed['usage'] = changed['raw_response']['usage'] = None
            changed['raw_text'] = canonical(changed['raw_response'])
            save(path, changed)
            summary, _, _ = summarize(root, gold)
            review = summary['review_before_any_continuation']
            self.assertFalse(review['server_reported_token_match_complete'])
            self.assertEqual(review['server_reported_token_matched_pairs'], 95)
            self.assertEqual(review['server_reported_token_match_unknown_pairs'], [{'item': 0, 'agent': 0}])

    def test_dataset_exclusion_and_package_provenance_changes_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'manifest.json'
            original = read(path)
            for field in ('file_sha256', 'natural_candidates_file_sha256', 'citation_candidates_file_sha256'):
                changed = copy.deepcopy(original)
                changed['plan']['metadata'][field] = '0' * 64
                changed['plan_sha256'] = digest(changed['plan'])
                save(path, changed)
                with self.assertRaisesRegex(ValueError, 'provenance mismatch'):
                    summarize(root, gold)
            for package in ('vllm', 'transformers'):
                changed = copy.deepcopy(original)
                changed['config']['packages'][package] = 'unfrozen'
                save(path, changed)
                with self.assertRaisesRegex(ValueError, 'package mismatch'):
                    summarize(root, gold)

    def test_cli_audits_before_write_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('runtime.natural_repetition_v2_score.summarize', side_effect=ValueError('fixture failure')):
                with self.assertRaisesRegex(ValueError, 'fixture failure'):
                    main(['--run', str(root), '--gold', 'unused-fixture-path'])
            self.assertFalse((root / 'analysis').exists())
            fake = ({'status': 'SOFTWARE_FIXTURE_ONLY'}, [], {'fixture': True})
            with patch('runtime.natural_repetition_v2_score.summarize', return_value=fake), \
                    patch('sys.stdout', new_callable=io.StringIO):
                main(['--run', str(root), '--gold', 'unused-fixture-path'])
            self.assertEqual({path.name for path in (root / 'analysis').iterdir()},
                             {'summary.json', 'per_question_rows.json', 'input_hashes.json'})
            with patch('runtime.natural_repetition_v2_score.summarize') as score:
                with self.assertRaisesRegex(ValueError, 'refuse overwrite'):
                    main(['--run', str(root), '--gold', 'unused-fixture-path'])
                score.assert_not_called()


if __name__ == '__main__':
    unittest.main()
