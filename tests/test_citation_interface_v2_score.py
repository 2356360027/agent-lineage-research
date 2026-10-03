"""SOFTWARE FIXTURES ONLY; fabricated records are not research observations."""

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.citation_grammar_v2 import BACKEND, KIND, fixtures, grammar
from runtime.citation_interface_v2 import JOBS, evaluate, request, run_item
from runtime.citation_interface_v2_score import (
    ALL_CONTRASTS, CONTEXT_CONTRASTS, EXPLORATORY_CONTRASTS, PRIMARY_CONTRASTS,
    audit_coverage, group_metrics, interface_readiness, main, paired_contrasts, question_metrics,
    score_output, summarize,
)
from runtime.dependency_probe import sources
from runtime.e1 import canonical, digest, save
from runtime.natural_qa_data import OFFICIAL_METRIC_NAMES, adapt_hotpot_row, seeded_order


def design():
    path = Path(__file__).resolve().parents[1] / 'configs' / 'citation_interface_v2.json'
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


def fake_preflight(views, packages):
    """Fabricate a record to test its audit, never compile or claim conformance."""
    reports = {}
    for visible in [set()] + [{unit['id'] for unit in view['units']} for view in views]:
        specification = grammar(visible)
        payload = dict(model='Qwen/Qwen2.5-7B-Instruct',
            messages=[dict(role='user', content='CPU protocol fixture; no inference')],
            max_tokens=256, temperature=0., top_p=1., seed=1,
            guided_grammar=specification, guided_decoding_backend=BACKEND)
        cases = [dict(case, token_ids=[1, 2], accepted=case['expected'],
                      first_rejected=None if case['expected'] else 0,
                      terminated=case['expected']) for case in fixtures(visible)]
        reports[digest(specification)] = dict(visible=sorted(visible), grammar=specification,
            admitted=True, backend_after=BACKEND, admission_request=payload,
            admission_request_sha256=digest(payload), fixtures=cases, all_passed=True)
    return dict(kind=KIND, backend=BACKEND, packages=packages, grammars=reports,
        baseline_admitted=True, baseline_backend_after=BACKEND, tokenizer_vocab_size=100,
        tokenizer_eos_id=2,
        engine_sources={name: 'e' * 64 for name in (
            'vllm.entrypoints.openai.protocol', 'vllm.v1.engine.processor',
            'vllm.v1.structured_output.backend_xgrammar', 'vllm.v1.structured_output.utils')},
        software_fixture_only_not_a_compiler_result=True)


def fake_engine_evidence():
    """Synthetic startup record for checker tests, not a live server attestation."""
    line = ("INFO [SOFTWARE_FIXTURE] Initializing a V1 LLM engine (v0.8.3) "
            "with config: max_seq_len=4096, decoding_config=DecodingConfig("
            "guided_decoding_backend='xgrammar', reasoning_backend=None)")
    return {'kind': 'VLLM_V1_STARTUP_LOG_EVIDENCE', 'version': '0.8.3',
            'backend': 'xgrammar', 'initialization_line': line,
            'initialization_line_sha256': hashlib.sha256(line.encode('utf-8')).hexdigest()}


class SoftwareFixtureClient:
    """Fictional responses for exact replay tests, never model/API calls."""
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
        elif key == 'i2-question_only__original__guided_grammar':
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
    """All 64 records below are software fixtures, not experiment data."""
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
        'grammar_preflight': fake_preflight(views, packages),
        'server_engine': fake_engine_evidence(),
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


class CitationInterfaceV2ScoreTests(unittest.TestCase):
    def test_invalid_format_and_wrapped_id_never_repaired(self):
        view, gold = example()
        invalid = dict(outcome(), format_valid=False, violation='fixture failure', diagnostics=['non_stop'])
        scores = score_output(invalid, view, gold)
        self.assertFalse(scores['valid_output'])
        self.assertEqual(scores['diagnostics'], ['non_stop'])
        self.assertTrue(all(scores[key] == 0. for key in OFFICIAL_METRIC_NAMES))
        self.assertTrue(all(score_output(outcome(citations=['[d0s0]']), view, gold)[key] == 0.
                            for key in OFFICIAL_METRIC_NAMES))

    def test_eight_groups_and_whole_question_bootstrap(self):
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
        with patch('runtime.citation_interface_v2_score.interval', return_value={'mean': 0.}) as boot:
            paired_contrasts([copy.deepcopy(row) for _ in range(8)], design())
        self.assertEqual(boot.call_count, len(ALL_CONTRASTS))
        for call in boot.call_args_list:
            self.assertEqual(len(call.args[0]), 8)
            self.assertEqual(call.args[1:3], (10103027, 2000))

    def test_full_fixture_replay_read_only_all_planned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            summary, rows, hashes = summarize(root, gold)
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual((summary['questions'], summary['actual_calls'], len(rows)), (8, 64, 8))
            self.assertEqual(summary['protocol'], 'citation-interface-v2')
            self.assertTrue(summary['interface_readiness']['passes'])
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
            self.assertIn('grammar_preflight_sha256', hashes['logical_inputs'])
            self.assertIn('server_engine_sha256', hashes['logical_inputs'])
            self.assertEqual(summary['server_engine'], fake_engine_evidence())
            self.assertTrue(audit_coverage(root)['coverage_counts_complete'])

    def test_readiness_is_predefined_format_only_not_quality_or_automatic_run(self):
        group = {'all_planned_outputs': 8, 'format_valid_count': 7,
                 'diagnostic_counts': {}, 'f1': 0., 'joint_f1': 0.}
        key = 'full__explicit__json_object'
        self.assertTrue(interface_readiness({key: group})['passes'])
        self.assertFalse(interface_readiness({key: dict(group, format_valid_count=6)})['passes'])
        self.assertFalse(interface_readiness({key: dict(group,
            diagnostic_counts={'bracket_only_mismatch': 1})})['passes'])
        self.assertFalse(interface_readiness({key: dict(group, all_planned_outputs=7)})['passes'])

    def test_coverage_no_gold_effects_or_writes(self):
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

    def test_failed_pending_missing_and_extra_calls_block(self):
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
            save(path, original)
            save(root / 'shard-000' / 'i8-extra.json', {'status': 'ok'})
            with self.assertRaisesRegex(ValueError, 'Call records missing/extra'):
                summarize(root, gold)

    def test_missing_completion_blocks_without_imputation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            (root / 'shard-000' / 'completion.json').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing required input: completion'):
                summarize(root, gold)
            self.assertFalse(audit_coverage(root)['coverage_counts_complete'])

    def test_request_backend_grammar_response_outcome_visibility_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'i0-full__explicit__guided_grammar.json'
            original = read(path)
            for field, value in (('seed', -1), ('guided_decoding_backend', 'outlines'),
                                 ('guided_grammar', 'root ::= "{}"')):
                changed = copy.deepcopy(original)
                changed['request'][field] = value
                changed['request_hash'] = digest(changed['request'])
                save(path, changed)
                with self.assertRaisesRegex(ValueError, 'Request mismatch'):
                    summarize(root, gold)
            for changed, message in (
                (dict(original, raw_text='{}'), 'Raw mismatch'),
                (dict(original, outcome=outcome(answer='tampered')), 'Outcome mismatch'),
                (dict(original, visible=[]), 'Decoder/visibility mismatch'),
            ):
                save(path, changed)
                with self.assertRaisesRegex(ValueError, message):
                    summarize(root, gold)
            save(path, original)
            result_path = root / 'shard-000' / 'result-0.json'
            result = read(result_path)
            result['order'].reverse()
            save(result_path, result)
            with self.assertRaisesRegex(ValueError, 'Recreated result mismatch'):
                summarize(root, gold)

    def test_frozen_fields_sources_and_plan_hash_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'manifest.json'
            original = read(path)
            for field in ('grammar_preflight', 'candidate_sha256', 'eligibility',
                          'decoder_packages', 'server_engine'):
                record = copy.deepcopy(original)
                del record['plan'][field]
                record['plan_sha256'] = digest(record['plan'])
                save(path, record)
                with self.assertRaisesRegex(ValueError, 'frozen record is incomplete'):
                    summarize(root, gold)
            save(path, dict(original, plan_sha256='bad'))
            with self.assertRaisesRegex(ValueError, 'Plan hash mismatch'):
                summarize(root, gold)
            save(path, original)
            with patch('runtime.citation_interface_v2_score.sources', return_value={'changed': 'fixture'}):
                with self.assertRaisesRegex(ValueError, 'frozen source mismatch'):
                    summarize(root, gold)

    def test_server_engine_evidence_is_required_and_tampering_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'manifest.json'
            original = read(path)
            for field, value in (('backend', 'outlines'), ('version', '0.9.0'),
                                 ('kind', 'UNVERIFIED'),
                                 ('initialization_line_sha256', '0' * 64)):
                record = copy.deepcopy(original)
                record['plan']['server_engine'][field] = value
                record['plan_sha256'] = digest(record['plan'])
                save(path, record)
                with self.assertRaises(ValueError):
                    summarize(root, gold)
            for fragment, replacement in (('a V1', 'a V0'),
                                           ("backend='xgrammar'", "backend='outlines'")):
                record = copy.deepcopy(original)
                evidence = record['plan']['server_engine']
                evidence['initialization_line'] = evidence['initialization_line'].replace(
                    fragment, replacement)
                evidence['initialization_line_sha256'] = hashlib.sha256(
                    evidence['initialization_line'].encode('utf-8')).hexdigest()
                record['plan_sha256'] = digest(record['plan'])
                save(path, record)
                with self.assertRaises(ValueError):
                    summarize(root, gold)

    def test_cpu_preflight_v1_admission_and_fixture_record_tampering_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'manifest.json'
            original = read(path)
            def check_mutation(mutate, message):
                record = copy.deepcopy(original)
                mutate(record['plan']['grammar_preflight'])
                record['plan_sha256'] = digest(record['plan'])
                save(path, record)
                with self.assertRaisesRegex(ValueError, message):
                    summarize(root, gold)
            check_mutation(lambda p: p.update(grammars={}), 'Missing/extra compiled grammar')
            check_mutation(lambda p: p.update(baseline_admitted=False), 'baseline was not V1 admitted')
            check_mutation(lambda p: p.update(engine_sources={}), 'Missing actual V1 source')
            check_mutation(lambda p: next(iter(p['grammars'].values())).update(backend_after='outlines'),
                           'Compiled/admitted grammar changed')
            check_mutation(lambda p: next(iter(p['grammars'].values()))['fixtures'][0].update(accepted=False),
                           'Grammar conformance failed')
            check_mutation(lambda p: next(iter(p['grammars'].values()))['fixtures'][0].update(token_ids=[]),
                           'Missing CPU fixture tokens')

    def test_sampling_frame_and_first_eligible_selection_tampering_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'manifest.json'
            original = read(path)
            record = copy.deepcopy(original)
            record['plan']['eligibility'][0]['eligible'] = False
            record['plan_sha256'] = digest(record['plan'])
            save(path, record)
            with self.assertRaisesRegex(ValueError, 'first eight eligible'):
                summarize(root, gold)
            record = copy.deepcopy(original)
            order = record['plan']['metadata']['all_ordered_ids']
            order[0], order[1] = order[1], order[0]
            record['plan_sha256'] = digest(record['plan'])
            save(path, record)
            with self.assertRaisesRegex(ValueError, 'sampling frame order mismatch'):
                summarize(root, gold)

    def test_gold_or_dataset_change_rejected(self):
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

    def test_invalid_times_context_or_model_record_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = fixture_run(root)
            path = root / 'shard-000' / 'i0-full__original__json_object.json'
            original = read(path)
            for field, value, message in (
                ('wall_seconds', -1, 'call duration'), ('started_unix', 99., 'call start time'),
                ('tokenizer_prompt_tokens', 4096, 'request token count'),
                ('tokenizer_prompt_tokens', True, 'request token count'),
            ):
                save(path, dict(original, **{field: value}))
                with self.assertRaisesRegex(ValueError, message):
                    summarize(root, gold)
            save(path, original)
            metadata = root / 'shard-000' / 'server_metadata.json'
            changed = read(metadata)
            changed['data'][0]['root'] = '/not-the-recorded-model'
            save(metadata, changed)
            with self.assertRaisesRegex(ValueError, 'endpoint/path mismatch'):
                summarize(root, gold)

    def test_missing_usage_never_fabricated(self):
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

    def test_cli_no_write_until_success_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('runtime.citation_interface_v2_score.summarize', side_effect=ValueError('fixture failure')):
                with self.assertRaisesRegex(ValueError, 'fixture failure'):
                    main(['--run', str(root), '--gold', 'unused-fixture-path'])
            self.assertFalse((root / 'analysis').exists())
            fake = ({'status': 'SOFTWARE_FIXTURE_ONLY'}, [], {'fixture': True})
            with patch('runtime.citation_interface_v2_score.summarize', return_value=fake), \
                    patch('sys.stdout', new_callable=io.StringIO):
                main(['--run', str(root), '--gold', 'unused-fixture-path'])
            self.assertEqual({path.name for path in (root / 'analysis').iterdir()},
                             {'summary.json', 'per_question_rows.json', 'input_hashes.json'})
            with patch('runtime.citation_interface_v2_score.summarize') as score:
                with self.assertRaisesRegex(ValueError, 'refuse overwrite'):
                    main(['--run', str(root), '--gold', 'unused-fixture-path'])
                score.assert_not_called()


if __name__ == '__main__':
    unittest.main()
