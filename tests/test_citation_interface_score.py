"""SOFTWARE FIXTURES ONLY: fabricated responses are not scientific observations."""

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.citation_interface_v1 import (
    BACKEND, JOBS, evaluate, request, run_item, schema,
)
from runtime.citation_interface_score import (
    ALL_CONTRASTS, CONTEXT_CONTRASTS, EXPLORATORY_CONTRASTS, PRIMARY_CONTRASTS,
    audit_coverage, group_metrics, main, paired_contrasts, question_metrics,
    score_output, summarize,
)
from runtime.dependency_probe import sources
from runtime.e1 import canonical, digest, save
from runtime.natural_qa_data import OFFICIAL_METRIC_NAMES, adapt_hotpot_row, seeded_order


def design():
    path = Path(__file__).resolve().parents[1] / 'configs' / 'citation_interface_v1.json'
    return json.loads(path.read_text(encoding='utf-8'))


def example(item_id='SOFTWARE-FIXTURE-0'):
    return adapt_hotpot_row({
        '_id': item_id, 'question': 'Which city?',
        'context': [[f'Document {number}', [f'Sentence {number} mentions Paris.']]
                    for number in range(10)],
        'answer': 'Paris', 'supporting_facts': [['Document 0', 0]],
    })


def outcome(answer='Paris', citations=None):
    return {'format_valid': True, 'assessment': {
        'answer': answer, 'citations': ['d0s0'] if citations is None else citations},
        'violation': None, 'diagnostics': []}


class SoftwareFixtureClient:
    """Write fictional records only to test offline reconstruction, never an API."""
    def __init__(self, directory, config):
        self.directory, self.config = directory, config

    def call(self, key, messages, seed, decoder, visible):
        visible = sorted(set(visible))
        payload = request(self.config, messages, seed, decoder, visible)
        prediction = {'answer': 'Paris', 'citations': ['d0s0'] if visible else []}
        finish = 'stop'
        if key == 'i0-full__original__json_object':
            prediction['citations'] = ['[d0s0]']
        elif key == 'i1-full__explicit__json_object':
            prediction = {}
        elif key == 'i2-question_only__original__guided_json':
            finish = 'length'
        raw = {'model': self.config['model'], 'choices': [{
            'finish_reason': finish, 'message': {'content': canonical(prediction)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 4}}
        parsed = evaluate(raw, decoder, self.config['model'], visible)
        save(self.directory / (key + '.json'), {
            'request': payload, 'request_hash': digest(payload), 'decoder': decoder,
            'visible': visible, 'raw_text': canonical(raw), 'raw_response': raw,
            'outcome': parsed, 'status': 'ok' if parsed['format_valid'] else 'format_violation',
            'usage': raw['usage'], 'wall_seconds': .25, 'started_unix': 110.,
            'tokenizer_prompt_tokens': 10,
            'software_fixture_only_not_real_inference': True,
        })
        return parsed


def fixture_run(root):
    """All 64 records are fabricated fixtures, not model calls or experiment data."""
    directory = root / 'shard-000'
    directory.mkdir()
    d = design()
    ordered = [row['id'] for row in seeded_order(
        [{'id': f'SOFTWARE-FIXTURE-{index}'} for index in range(7205)], d['seed'])]
    pairs = [example(item_id) for item_id in ordered[:8]]
    views = [view for view, _ in pairs]
    gold = {view['id']: labels for view, labels in pairs}
    bundle = sources()
    packages = {'vllm': '0.8.3', 'xgrammar': '0.1.17', 'outlines': 'fixture',
                'outlines_core': 'fixture', 'transformers': 'fixture'}
    cfg = {'model': 'SOFTWARE_FIXTURE_ONLY_NOT_A_MODEL_RUN', 'temperature': 0.,
           'top_p': 1., 'max_tokens': 256, 'model_content_sha256': 'a' * 64,
           'model_path': '/fixture', 'served_model_metadata': {'root': '/fixture'},
           'server_settings_reported': {'max_model_len': 4096}}
    plan = {
        'design': d, 'design_sha256': digest(d), 'views': views,
        'source_bundle': bundle, 'source_sha256': digest(bundle),
        'actual_calls': 64, 'model_content_sha256': 'a' * 64,
        'candidate_sha256': 'b' * 64, 'chat_template_sha256': 'c' * 64,
        'tokenizer_class': 'SOFTWARE_FIXTURE_TOKENIZER', 'decoder_packages': packages,
        'eligibility': [{'id': item_id, 'selected': index < 8, 'eligible': True,
                         'worst_reserved_tokens': 266, 'reason': None}
                        for index, item_id in enumerate(ordered[:64])],
        'metadata': {'file_sha256': 'f' * 64, 'scoring_gold_sha256': digest(gold),
                     'excluded_ids': [f'EXCLUDED-SOFTWARE-FIXTURE-{index}' for index in range(200)],
                     'all_ordered_ids': ordered},
        'grammar_preflight': {
            'kind': 'CPU_GRAMMAR_COMPILATION_NO_INFERENCE', 'backend': BACKEND,
            'schemas': {digest(schema(visible)): 'SOFTWARE_FIXTURE_PROCESSOR'
                        for visible in ([], [unit['id'] for unit in views[0]['units']])},
            'packages': packages,
        },
    }
    save(directory / 'manifest.json', {'plan': plan, 'plan_sha256': digest(plan),
         'config': cfg, 'planned_calls': 64, 'no_gold_in_runtime': True, 'started_unix': 100.})
    save(directory / 'server_metadata.json', {'data': [{
        'id': cfg['model'], 'root': '/fixture', 'max_model_len': 4096}]})
    client = SoftwareFixtureClient(directory, cfg)
    for index, view in enumerate(views):
        run_item(client, d, view, index, directory)
    save(directory / 'completion.json', {'completed_items': 8, 'calls': 64, 'completed_unix': 160.})
    gold_path = root / 'software_fixture_gold.json'
    save(gold_path, {'dataset_sha256': 'f' * 64, 'gold': gold})
    return gold_path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class CitationInterfaceScoreTests(unittest.TestCase):
    def test_invalid_format_retains_diagnostics_and_scores_zero(self):
        view, gold = example()
        invalid = outcome()
        invalid.update(format_valid=False, violation='fixture failure', diagnostics=['non_stop'])
        scores = score_output(invalid, view, gold)
        self.assertFalse(scores['valid_output'])
        self.assertEqual(scores['diagnostics'], ['non_stop'])
        self.assertEqual(scores['invalid_reason'], 'fixture failure')
        self.assertTrue(all(scores[key] == 0. for key in OFFICIAL_METRIC_NAMES))
        with self.assertRaises(ValueError):
            score_output(None, view, gold)
        with self.assertRaisesRegex(ValueError, 'diagnostics'):
            score_output(dict(outcome(), diagnostics=['duplicate', 'duplicate']), view, gold)

    def test_invalid_citation_not_repaired_or_partially_scored(self):
        view, gold = example()
        scores = score_output(outcome(citations=['[d0s0]']), view, gold)
        self.assertFalse(scores['valid_output'])
        self.assertTrue(all(scores[key] == 0. for key in OFFICIAL_METRIC_NAMES))

    def test_all_eight_groups_and_question_bootstrap(self):
        view, gold = example()
        result = {'item': 0, 'id': view['id'], 'outputs': {job: outcome() for job in JOBS}}
        for job in JOBS:
            if job.startswith('question_only'):
                result['outputs'][job] = outcome(citations=[])
        result['outputs']['full__original__json_object'] = outcome(answer='wrong')
        row = question_metrics(result, view, gold)
        groups = group_metrics([row])
        self.assertEqual(set(groups), set(JOBS))
        self.assertTrue(all(group['all_planned_outputs'] == 1 for group in groups.values()))
        self.assertEqual(groups['question_only__original__json_object']['f1'], 1.)
        self.assertEqual(groups['question_only__original__json_object']['joint_f1'], 0.)
        self.assertEqual(row['contrasts']['explicit_minus_original_answer_f1_json_object_full'], 1.)
        self.assertEqual(row['contrasts']['full_minus_question_only_joint_f1_explicit_json_object'], 1.)
        self.assertEqual((len(PRIMARY_CONTRASTS), len(EXPLORATORY_CONTRASTS), len(CONTEXT_CONTRASTS)),
                         (2, 16, 8))
        with patch('runtime.citation_interface_score.interval', return_value={'mean': 0.}) as boot:
            paired_contrasts([copy.deepcopy(row) for _ in range(8)], design())
        self.assertEqual(boot.call_count, len(ALL_CONTRASTS))
        for call in boot.call_args_list:
            self.assertEqual(len(call.args[0]), 8)
            self.assertEqual(call.args[1:3], (10103027, 2000))

    def test_full_software_fixture_audit_is_read_only_and_all_planned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            summary, rows, hashes = summarize(root, gold)
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual((summary['questions'], summary['actual_calls'], len(rows)), (8, 64, 8))
            self.assertEqual(summary['all_calls_format_valid_count'], 61)
            self.assertEqual(summary['all_calls_format_violation_count'], 3)
            self.assertEqual(summary['groups']['full__original__json_object']['f1'], 7 / 8)
            self.assertEqual(summary['groups']['full__original__json_object']['joint_f1'], 7 / 8)
            self.assertEqual(summary['groups']['full__original__json_object']['diagnostic_counts'][
                'bracket_only_mismatch'], 1)
            self.assertEqual(summary['all_calls_diagnostic_counts']['non_stop'], 1)
            self.assertEqual(summary['primary_descriptive_contrasts'][
                'explicit_minus_original_format_valid_json_object_full']['mean'], 0.)
            self.assertEqual(summary['primary_descriptive_contrasts'][
                'guided_minus_json_format_valid_explicit_full']['mean'], 1 / 8)
            self.assertEqual(summary['prompt_tokens'], 640)
            self.assertEqual(summary['completion_tokens'], 256)
            self.assertEqual(summary['sum_call_seconds'], 16.)
            self.assertEqual(summary['recorded_run_elapsed_seconds'], 60.)
            self.assertEqual(len(hashes['raw_input_files']), 75)
            self.assertEqual(hashes['gold_file']['dataset_sha256'], 'f' * 64)
            self.assertTrue(audit_coverage(root)['coverage_counts_complete'])

    def test_coverage_is_gold_free_without_effect_estimates_or_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'shard-000'
            directory.mkdir()
            save(directory / 'i0-full__original__json_object.json', {'status': 'failed'})
            report = audit_coverage(root)
            self.assertEqual(report['call_status_counts'], {'failed': 1})
            self.assertFalse(report['coverage_counts_complete'])
            self.assertNotIn('groups', report)
            self.assertNotIn('primary_descriptive_contrasts', report)
            with patch('sys.stdout', new_callable=io.StringIO) as stdout:
                main(['--run', str(root), '--audit-only'])
            self.assertEqual(json.loads(stdout.getvalue())['observed_call_files'], 1)
            self.assertFalse((root / 'analysis').exists())

    def test_missing_failed_and_pending_calls_block_full_scoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'i0-full__original__json_object.json'
            original = read(path)
            for status in ('failed', 'pending'):
                save(path, dict(original, status=status))
                with self.assertRaisesRegex(ValueError, 'Unresolved infrastructure'):
                    summarize(root, gold)
                self.assertFalse(audit_coverage(root)['coverage_counts_complete'])
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'Call records missing/extra'):
                summarize(root, gold)

    def test_missing_completion_and_extra_files_block_scoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'completion.json'
            original = read(path)
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'Missing required input: completion'):
                summarize(root, gold)
            save(path, original)
            save(root / 'shard-000' / 'i8-extra.json', {'status': 'ok'})
            with self.assertRaisesRegex(ValueError, 'Call records missing/extra'):
                summarize(root, gold)
            self.assertFalse(audit_coverage(root)['coverage_counts_complete'])

    def test_request_response_outcome_visibility_and_result_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'i0-full__original__json_object.json'
            original = read(path)
            changed = copy.deepcopy(original)
            changed['request']['seed'] += 1
            changed['request_hash'] = digest(changed['request'])
            for record, message in (
                (changed, 'Request mismatch'),
                (dict(original, raw_text='{}'), 'Raw mismatch'),
                (dict(original, outcome=outcome()), 'Outcome mismatch'),
                (dict(original, visible=[]), 'Decoder/visibility mismatch'),
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

    def test_truncated_frozen_plan_and_changed_sources_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'manifest.json'
            original = read(path)
            for field in ('grammar_preflight', 'candidate_sha256', 'eligibility', 'decoder_packages'):
                manifest = copy.deepcopy(original)
                del manifest['plan'][field]
                manifest['plan_sha256'] = digest(manifest['plan'])
                save(path, manifest)
                with self.assertRaisesRegex(ValueError, 'frozen record is incomplete'):
                    summarize(root, gold)
            save(path, original)
            with patch('runtime.citation_interface_score.sources', return_value={'changed': 'fixture'}):
                with self.assertRaisesRegex(ValueError, 'frozen source mismatch'):
                    summarize(root, gold)

    def test_frozen_hash_grammar_frame_and_selection_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'manifest.json'
            original = read(path)
            def change(field, value):
                record = copy.deepcopy(original)
                record['plan'][field] = value
                record['plan_sha256'] = digest(record['plan'])
                return record
            grammar = copy.deepcopy(original['plan']['grammar_preflight'])
            grammar['schemas'] = {}
            selection = copy.deepcopy(original['plan']['eligibility'])
            selection[0]['eligible'] = False
            metadata = copy.deepcopy(original['plan']['metadata'])
            metadata['all_ordered_ids'][0], metadata['all_ordered_ids'][1] = (
                metadata['all_ordered_ids'][1], metadata['all_ordered_ids'][0])
            for manifest, message in (
                (dict(original, plan_sha256='bad'), 'Plan hash mismatch'),
                (change('grammar_preflight', grammar), 'grammar schemas missing/extra'),
                (change('eligibility', selection), 'first eight eligible'),
                (change('metadata', metadata), 'sampling frame order mismatch'),
            ):
                save(path, manifest)
                with self.assertRaisesRegex(ValueError, message):
                    summarize(root, gold)

    def test_changed_gold_or_dataset_declaration_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold_path = fixture_run(root)
            original = read(gold_path)
            save(gold_path, dict(original, dataset_sha256='changed'))
            with self.assertRaisesRegex(ValueError, 'Gold/dataset checksum'):
                summarize(root, gold_path)
            original['gold'][next(iter(original['gold']))]['answer'] = 'changed'
            save(gold_path, original)
            with self.assertRaisesRegex(ValueError, 'Scoring labels changed'):
                summarize(root, gold_path)

    def test_bad_timestamps_or_token_budget_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'i0-full__original__json_object.json'
            original = read(path)
            for field, value, message in (
                ('wall_seconds', -1, 'call duration'),
                ('started_unix', 99., 'call start time'),
                ('tokenizer_prompt_tokens', 4096, 'request token count'),
                ('tokenizer_prompt_tokens', True, 'request token count'),
            ):
                save(path, dict(original, **{field: value}))
                with self.assertRaisesRegex(ValueError, message):
                    summarize(root, gold)

    def test_missing_usage_not_estimated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'i0-full__original__json_object.json'
            record = read(path)
            record['usage'] = None
            record['raw_response']['usage'] = None
            record['raw_text'] = canonical(record['raw_response'])
            save(path, record)
            summary, _, _ = summarize(root, gold)
            self.assertFalse(summary['usage_complete'])
            self.assertIsNone(summary['prompt_tokens'])
            self.assertIsNone(summary['completion_tokens'])

    def test_cli_exclusive_analysis_and_no_output_before_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('runtime.citation_interface_score.summarize', side_effect=ValueError('fixture failure')):
                with self.assertRaisesRegex(ValueError, 'fixture failure'):
                    main(['--run', str(root), '--gold', 'unused-fixture-path'])
            self.assertFalse((root / 'analysis').exists())
            fake = ({'status': 'SOFTWARE_FIXTURE_ONLY'}, [], {'fixture': True})
            with patch('runtime.citation_interface_score.summarize', return_value=fake), \
                    patch('sys.stdout', new_callable=io.StringIO):
                main(['--run', str(root), '--gold', 'unused-fixture-path'])
            self.assertEqual({path.name for path in (root / 'analysis').iterdir()},
                             {'summary.json', 'per_question_rows.json', 'input_hashes.json'})
            with patch('runtime.citation_interface_score.summarize') as score:
                with self.assertRaisesRegex(ValueError, 'refuse overwrite'):
                    main(['--run', str(root), '--gold', 'unused-fixture-path'])
                score.assert_not_called()


if __name__ == '__main__':
    unittest.main()
