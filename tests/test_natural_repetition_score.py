"""SOFTWARE FIXTURES ONLY. Fabricated responses are not model experiments."""

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.dependency_probe import sources
from runtime.e1 import canonical, digest, save
from runtime.format_probe import request
from runtime.natural_qa_data import OFFICIAL_METRIC_NAMES, adapt_hotpot_row
from runtime.natural_repetition import JOBS, run_item
from runtime.natural_repetition_score import (
    audit_coverage, group_metrics, main, paired_contrasts, question_metrics,
    score_output, summarize,
)


def design():
    path = Path(__file__).resolve().parents[1] / 'configs' / 'natural_repetition_v1.json'
    return json.loads(path.read_text(encoding='utf-8'))


def example(index=0):
    return adapt_hotpot_row({
        '_id': f'SOFTWARE-FIXTURE-{index}', 'question': 'Which city?',
        'context': [[f'Document {number}', [f'Sentence {number} mentions Paris.']]
                    for number in range(10)],
        'answer': 'Paris', 'supporting_facts': [['Document 0', 0]],
    })


def outcome(answer='Paris', citations=None):
    return {'format_valid': True, 'assessment': {
        'answer': answer, 'citations': ['d0s0'] if citations is None else citations},
        'violation': None}


class SoftwareFixtureClient:
    """Writes fictional records solely to test the offline integrity checker."""
    def __init__(self, directory, config):
        self.directory, self.config = directory, config

    def call(self, key, messages, seed, arm):
        payload = request(self.config, messages, seed)
        # Correct-looking string but deliberately malformed format at one job.
        content = ('not json' if key == 'i0-replay_a0' else canonical({
            'answer': 'Paris', 'citations': [] if 'question_only' in key else
            (['d0s0'] if '[d0s0]' in messages[1]['content'] else [])}))
        raw = {'model': self.config['model'], 'choices': [{
            'finish_reason': 'stop', 'message': {'content': content}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 4}}
        parsed = self.evaluator(raw, arm, self.config['model'])
        save(self.directory / (key + '.json'), {
            'request': payload, 'request_hash': digest(payload), 'arm': arm,
            'raw_text': canonical(raw), 'raw_response': raw, 'outcome': parsed,
            'status': 'ok' if parsed['format_valid'] else 'format_violation',
            'usage': raw['usage'], 'wall_seconds': .25, 'tokenizer_prompt_tokens': 10,
            'software_fixture_only_not_real_inference': True,
        })
        return parsed


def fixture_run(root):
    """All 220 records are tiny fabricated unit-test fixtures, never inference."""
    directory = root / 'shard-000'
    directory.mkdir()
    d = design()
    pairs = [example(index) for index in range(20)]
    views = [view for view, _ in pairs]
    bundle = sources()
    cfg = {'model': 'SOFTWARE_FIXTURE_ONLY_NOT_A_MODEL_RUN', 'temperature': 0.,
           'top_p': 1., 'max_tokens': 256, 'model_content_sha256': 'fixture-model',
           'model_path': '/fixture', 'served_model_metadata': {'root': '/fixture'},
           'server_settings_reported': {'max_model_len': 4096}}
    plan = {'design': d, 'design_sha256': digest(d), 'views': views,
            'source_bundle': bundle, 'source_sha256': digest(bundle),
            'actual_calls': 220, 'model_content_sha256': 'fixture-model',
            'metadata': {'file_sha256': 'f' * 64,
                         'scoring_gold_sha256': digest({view['id']: gold for view, gold in pairs})}}
    save(directory / 'manifest.json', {'plan': plan, 'plan_sha256': digest(plan),
         'config': cfg, 'planned_calls': 220, 'no_gold_in_runtime': True, 'started_unix': 100.})
    save(directory / 'server_metadata.json', {'data': [{
        'id': cfg['model'], 'root': '/fixture', 'max_model_len': 4096}]})
    client = SoftwareFixtureClient(directory, cfg)
    for index, view in enumerate(views):
        run_item(client, d, view, index, directory)
    save(directory / 'completion.json', {
        'completed_items': 20, 'calls': 220, 'completed_unix': 160.})
    gold_path = root / 'software_fixture_gold.json'
    save(gold_path, {'dataset_sha256': 'f' * 64,
                    'gold': {view['id']: gold for view, gold in pairs}})
    return gold_path


class NaturalRepetitionScoreTests(unittest.TestCase):
    def test_format_invalid_is_zero_even_with_correct_assessment(self):
        view, gold = example()
        invalid = outcome()
        invalid.update(format_valid=False, violation='fixture format failure')
        scores = score_output(invalid, view, gold)
        self.assertFalse(scores['valid_output'])
        self.assertFalse(scores['format_valid'])
        self.assertEqual(scores['invalid_reason'], 'fixture format failure')
        self.assertTrue(all(scores[key] == 0. for key in OFFICIAL_METRIC_NAMES))
        with self.assertRaises(ValueError):
            score_output(None, view, gold)

    def test_invalid_citation_is_all_zero(self):
        view, gold = example()
        scores = score_output(outcome(citations=['invented']), view, gold)
        self.assertTrue(scores['format_valid'])
        self.assertFalse(scores['valid_output'])
        self.assertTrue(all(scores[key] == 0. for key in OFFICIAL_METRIC_NAMES))

    def test_agents_are_collapsed_before_question_bootstrap(self):
        view, gold = example()
        result = {'item': 0, 'id': view['id'], 'initial': [outcome()] * 3,
                  'outputs': {job: outcome() for job in JOBS}}
        result['outputs']['replay_a0'] = outcome(answer='wrong')
        result['outputs']['question_only'] = outcome(citations=[])
        row = question_metrics(result, view, gold)
        self.assertAlmostEqual(row['groups']['replay']['f1'], 2 / 3)
        self.assertAlmostEqual(row['contrasts']['raw_replay_minus_once_answer_f1'], -1 / 3)
        groups = group_metrics([row])
        self.assertEqual(set(groups), {'once', 'replay', 'single_union', 'question_only'})
        self.assertEqual(groups['once']['questions'], 1)
        self.assertEqual(groups['once']['actual_terminal_outputs'], 3)
        self.assertEqual(groups['question_only']['f1'], 1.)
        self.assertEqual(groups['question_only']['joint_f1'], 0.)
        with patch('runtime.natural_repetition_score.interval', return_value={'mean': -.25}) as boot:
            paired_contrasts([row, copy.deepcopy(row)], design())
        self.assertEqual(boot.call_count, 2)
        for call in boot.call_args_list:
            self.assertEqual(len(call.args[0]), 2)
            self.assertEqual(call.args[1:3], (10103026, 2000))

    def test_changed_gold_with_same_dataset_declaration_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gold_path = fixture_run(root)
            obj = json.loads(gold_path.read_text())
            first = next(iter(obj['gold']))
            obj['gold'][first]['answer'] = 'changed after freeze'
            save(gold_path, obj)
            with self.assertRaisesRegex(ValueError, 'Scoring labels changed'):
                summarize(root, gold_path)

    def test_coverage_without_gold_has_no_effect_estimates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'shard-000'
            directory.mkdir()
            save(directory / 'i0-initial-a0.json', {'status': 'failed'})
            report = audit_coverage(root)
            self.assertEqual(report['call_status_counts'], {'failed': 1})
            self.assertFalse(report['coverage_counts_complete'])
            self.assertNotIn('groups', report)
            self.assertNotIn('paired_contrasts', report)
            with patch('sys.stdout', new_callable=io.StringIO) as stdout:
                main(['--run', str(root), '--audit-only'])
            self.assertEqual(json.loads(stdout.getvalue())['observed_call_files'], 1)
            self.assertFalse((root / 'analysis').exists())

    def test_full_software_fixture_audit_scores_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold_path = fixture_run(root)
            summary, rows, hashes = summarize(root, gold_path)
            self.assertEqual(summary['actual_calls'], 220)
            self.assertEqual(len(rows), 20)
            self.assertEqual(summary['groups']['once']['actual_terminal_outputs'], 60)
            self.assertEqual(summary['groups']['replay']['valid_output_count'], 59)
            self.assertAlmostEqual(summary['groups']['replay']['f1'], 59 / 60)
            self.assertAlmostEqual(summary['paired_contrasts'][
                'raw_replay_minus_once_answer_f1']['mean'], -1 / 60)
            self.assertEqual(summary['all_calls_format_valid_count'], 219)
            self.assertEqual(summary['prompt_tokens'], 2200)
            self.assertEqual(summary['completion_tokens'], 880)
            self.assertEqual(summary['sum_call_seconds'], 55.)
            self.assertEqual(summary['recorded_run_elapsed_seconds'], 60.)
            self.assertEqual(summary['structural_aliases']['canonical_dedup_replay']['additional_calls'], 0)
            self.assertEqual(len(hashes['raw_input_files']), 243)
            self.assertEqual(set(hashes['logical_inputs']),
                             {'plan_sha256', 'design_sha256', 'source_sha256', 'views_sha256'})
            self.assertEqual(hashes['gold_file']['dataset_sha256'], 'f' * 64)
            self.assertTrue(audit_coverage(root)['coverage_counts_complete'])

    def test_infrastructure_failure_blocks_full_scoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold_path = fixture_run(root)
            path = root / 'shard-000' / 'i0-initial-a0.json'
            record = json.loads(path.read_text())
            record['status'] = 'failed'
            save(path, record)
            with self.assertRaisesRegex(ValueError, 'Unresolved infrastructure'):
                summarize(root, gold_path)

    def test_missing_completion_blocks_full_scoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold_path = fixture_run(root)
            (root / 'shard-000' / 'completion.json').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing required input: completion'):
                summarize(root, gold_path)

    def test_missing_call_changed_source_and_request_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold_path = fixture_run(root)
            path = root / 'shard-000' / 'i0-initial-a0.json'
            original = json.loads(path.read_text())
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'Call records missing/extra'):
                summarize(root, gold_path)
            save(path, original)
            with patch('runtime.natural_repetition_score.sources', return_value={'changed': 'fixture'}):
                with self.assertRaisesRegex(ValueError, 'frozen source mismatch'):
                    summarize(root, gold_path)
            changed = copy.deepcopy(original)
            changed['request']['seed'] += 1
            changed['request_hash'] = digest(changed['request'])
            save(path, changed)
            with self.assertRaisesRegex(ValueError, 'Request mismatch'):
                summarize(root, gold_path)

    def test_tampered_result_and_dataset_checksum_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold_path = fixture_run(root)
            gold = json.loads(gold_path.read_text())
            gold['dataset_sha256'] = 'bad'
            save(gold_path, gold)
            with self.assertRaisesRegex(ValueError, 'Gold/dataset checksum'):
                summarize(root, gold_path)
            gold['dataset_sha256'] = 'f' * 64
            save(gold_path, gold)
            result_path = root / 'shard-000' / 'result-0.json'
            result = json.loads(result_path.read_text())
            result['outputs']['once_a0']['assessment']['answer'] = 'tampered'
            save(result_path, result)
            with self.assertRaisesRegex(ValueError, 'Recreated result mismatch'):
                summarize(root, gold_path)

    def test_missing_usage_is_not_estimated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold_path = fixture_run(root)
            path = root / 'shard-000' / 'i0-initial-a0.json'
            record = json.loads(path.read_text())
            record['usage'] = None
            record['raw_response']['usage'] = None
            record['raw_text'] = canonical(record['raw_response'])
            save(path, record)
            summary, _, _ = summarize(root, gold_path)
            self.assertFalse(summary['usage_complete'])
            self.assertIsNone(summary['prompt_tokens'])
            self.assertIsNone(summary['completion_tokens'])

    def test_cli_creates_exclusive_analysis_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = ({'status': 'SOFTWARE_FIXTURE_ONLY'}, [], {'fixture': True})
            with patch('runtime.natural_repetition_score.summarize', return_value=fake), \
                    patch('sys.stdout', new_callable=io.StringIO):
                main(['--run', str(root), '--gold', 'unused-fixture-path'])
            self.assertEqual({path.name for path in (root / 'analysis').iterdir()},
                             {'summary.json', 'per_question_rows.json', 'input_hashes.json'})
            with patch('runtime.natural_repetition_score.summarize') as score:
                with self.assertRaisesRegex(ValueError, 'refuse overwrite'):
                    main(['--run', str(root), '--gold', 'unused-fixture-path'])
                score.assert_not_called()


if __name__ == '__main__':
    unittest.main()
