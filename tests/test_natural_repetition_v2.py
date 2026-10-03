"""Independent CPU contract fixtures, never model or scientific observations."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from runtime import natural_repetition as old
from runtime import citation_interface_v2 as interface
from runtime import natural_repetition_v2 as n
from runtime.e1 import canonical, digest
from runtime.natural_qa_data import prediction_view, seeded_order


ROOT = Path(__file__).resolve().parents[1]


def design():
    return json.loads((ROOT / 'configs/natural_repetition_v2.json').read_text())


def fixture(item_id='SOFTWARE-FIXTURE-NOT-A-DATASET-ITEM', docs=10):
    return prediction_view(dict(_id=item_id, question='Where was Ada born?',
        context=[(f'Doc {i}', [f'Ada location {i}.', f'Other event {i}.'])
                 for i in range(docs)]))


def envelope(content, finish='stop'):
    return dict(model='fixture', choices=[dict(finish_reason=finish,
        message=dict(content=content))])


def config():
    return dict(model='fixture', temperature=0., top_p=1., max_tokens=256)


def candidate_packet(d):
    ordered = [row['id'] for row in seeded_order(
        [dict(id=f'SOFTWARE-UNSEEN-{i}') for i in range(7141)], d['seed'])]
    return dict(views=[fixture(item_id, docs=9 if index == 0 else 10)
                       for index, item_id in enumerate(ordered[:200])],
        metadata=dict(design_sha256=digest(d), file_sha256=n.DATASET_SHA,
            mirror_revision='1908d6afbbead072334abe2965f91bd2709910ab',
            natural_candidates_file_sha256=n.NATURAL_CANDIDATES_SHA,
            citation_candidates_file_sha256=n.CITATION_CANDIDATES_SHA,
            excluded_ids=[f'SOFTWARE-EXCLUDED-{i}' for i in range(264)],
            all_ordered_ids=ordered,
            preparation_script_sha256=hashlib.sha256(
                (ROOT / 'scripts/prepare_natural_candidates_v2.py').read_bytes()).hexdigest()))


class CharacterTokenizer:
    """Deliberately transparent fixture tokenizer, not the native tokenizer."""
    chat_template = 'SOFTWARE-FIXTURE-NOT-A-MODEL-TEMPLATE'

    def apply_chat_template(self, messages, tokenize, add_generation_prompt):
        assert tokenize is True and add_generation_prompt is True
        return list(range(len('\n'.join(m['role'] + ':' + m['content'] for m in messages))))


class FakeClient:
    def __init__(self, tokenizer=None, invalid_initial=False):
        self.config = config()
        self.tokenizer = tokenizer or CharacterTokenizer()
        self.calls = []
        self.invalid_initial = invalid_initial

    def call(self, key, messages, seed, decoder, visible):
        self.calls.append(dict(key=key, messages=deepcopy(messages), seed=seed,
                              decoder=decoder, visible=set(visible)))
        if 'initial' in key and self.invalid_initial:
            content = '{}'
        else:
            content = json.dumps(dict(answer='prior-' + key if 'initial' in key else '',
                citations=[sorted(visible)[0]] if 'initial' in key and visible else []))
        return interface.evaluate(envelope(content), decoder, 'fixture', visible)


class NaturalRepetitionV2Tests(unittest.TestCase):
    def test_fixed_design_has_full_planned_count_and_unchanged_parser_limits(self):
        d = design()
        n.validate_design(d)
        self.assertEqual(d['items'], 32)
        self.assertEqual(d['agents'], 3)
        self.assertEqual(d['actual_calls_per_item'], 17)
        self.assertEqual(d['items'] * d['actual_calls_per_item'], 544)
        self.assertEqual((d['max_tokens'], d['max_answer_utf8_bytes'], d['max_citations']),
                         (256, 160, 8))
        for key, changed in (('items', 16), ('seed', d['seed'] + 1),
                             ('max_citations', 9), ('temperature', .7)):
            altered = dict(d, **{key: changed})
            with self.subTest(key=key), self.assertRaises(ValueError):
                n.validate_design(altered)

    def test_prompt_is_label_free_and_system_is_fixed_explicit_interface(self):
        view = fixture()
        poisoned = dict(view, answer='SECRET_GOLD', supporting_facts=['SECRET_SUPPORT'])
        prior = dict(format_valid=True, assessment=dict(answer='prior', citations=['d0s0']))
        for units, previous, appendix in ((view['units'], None, None),
                ([], None, None), (view['units'], prior, 'SOFTWARE APPENDIX')):
            messages = n.prompt(view, units, 0, previous, appendix)
            self.assertEqual(messages, n.prompt(poisoned, units, 0, previous, appendix))
            self.assertEqual(messages[0]['content'], old.SYSTEM + interface.EXPLICIT)
            self.assertNotIn('SECRET_', canonical(messages))
        self.assertEqual(n.target_units(view, design()), n.target_units(poisoned, design()))

    def test_targets_and_position_change_neither_payload_nor_unique_evidence(self):
        view, d = fixture(), design()
        before = deepcopy(view)
        targets = n.target_units(view, d)
        self.assertEqual(len(targets), 3)
        self.assertEqual(len({u['id'] for u in targets}), 3)
        expected = old.replay_units(view, d)[len(view['units']):len(view['units']) + 3]
        self.assertEqual(targets, expected)
        moved = n.position_units(view, d)
        self.assertEqual(moved[-3:], targets)
        self.assertEqual(len(moved), len(view['units']))
        self.assertEqual({u['id']: u for u in moved}, {u['id']: u for u in view['units']})
        self.assertEqual(moved[:-3], [u for u in view['units']
                                    if u['id'] not in {t['id'] for t in targets}])
        appendix = n.replay_appendix(view, d)
        for target in targets:
            line = f"[{target['id']}] {target['title']} | {target['text']}"
            self.assertEqual(appendix.count(line), 2)
        self.assertEqual(view, before)

    def test_invalid_initial_is_retained_as_unavailable_not_repaired(self):
        messages = n.prompt(fixture(), [], 0,
                            dict(format_valid=False, assessment=None, violation='fixture'))
        self.assertIn('unavailable (invalid prior output)', messages[1]['content'])
        self.assertNotIn('SECRET_GOLD', canonical(messages))

    def test_control_is_label_free_fixed_and_matches_whole_chat_before_s0(self):
        view, d, tokenizer = fixture(), design(), CharacterTokenizer()
        control = n.prepare_control(view, d, tokenizer)
        self.assertIsNotNone(control)
        self.assertEqual(control['target_ids'], [u['id'] for u in n.target_units(view, d)])
        self.assertEqual(control['replay_appendix'], n.replay_appendix(view, d))
        self.assertEqual(control['control_sha256'], digest({k: v for k, v in control.items()
                                                          if k != 'control_sha256'}))
        self.assertEqual(control, n.prepare_control(dict(view, answer='SECRET_GOLD',
            supporting_facts=['SECRET_SUPPORT']), d, tokenizer))
        self.assertNotIn('SECRET_', canonical(control))
        for unit in view['units']:
            self.assertNotIn(f"[{unit['id']}]", control['padding_appendix'])
            self.assertNotIn(unit['text'], control['padding_appendix'])
        for agent in (None, 0, 1, 2):
            replay = n.prompt(view, view['units'], agent, None, control['replay_appendix'])
            padding = n.prompt(view, view['units'], agent, None, control['padding_appendix'])
            replay_n = len(tokenizer.apply_chat_template(replay, True, True))
            padding_n = len(tokenizer.apply_chat_template(padding, True, True))
            self.assertEqual(replay_n, padding_n)
            self.assertEqual(replay[0], padding[0])
            self.assertEqual(replay[1]['content'][:-len(control['replay_appendix'])],
                             padding[1]['content'][:-len(control['padding_appendix'])])

    def test_control_validation_rejects_content_target_and_forged_prompt_changes(self):
        view, d = fixture(), design()
        original = n.prepare_control(view, d, CharacterTokenizer())
        edits = (
            lambda control: control.update(padding_appendix=control['padding_appendix'] + ' FACT'),
            lambda control: control['target_ids'].__setitem__(0, 'not-a-source'),
            lambda control: control.update(replay_appendix=control['replay_appendix'] + ' changed'),
            lambda control: control['preflight_checks'][0].update(padding_prompt_sha256='0' * 64),
            lambda control: control['preflight_checks'][0].update(padding_tokens=0),
        )
        for edit in edits:
            control = deepcopy(original)
            edit(control)
            control['control_sha256'] = digest({k: v for k, v in control.items()
                                                if k != 'control_sha256'})
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                n.validate_control(view, control, d)

    def test_prepare_selects_first_32_eligible_without_truncation_or_changed_frame(self):
        d = design()
        packet = candidate_packet(d)
        packet['views'][1]['question'] = 'x' * 5000
        before = deepcopy(packet)
        plan = n.prepare(packet, d, CharacterTokenizer())
        self.assertEqual(plan['views'], packet['views'][2:34])
        self.assertEqual(plan['actual_calls'], 544)
        self.assertEqual(len(plan['eligibility']), 200)
        self.assertEqual(plan['eligibility'][0]['reason'], 'structure')
        self.assertEqual(plan['eligibility'][1]['reason'], 'context_limit')
        self.assertEqual(sum(row['selected'] for row in plan['eligibility']), 32)
        self.assertEqual(set(plan['controls']), {view['id'] for view in plan['views']})
        self.assertFalse(set(plan['controls']) & set(packet['metadata']['excluded_ids']))
        self.assertEqual(packet, before)

    def test_prepare_rejects_exclusion_overlap_counts_order_and_labels(self):
        d = design()
        edits = (
            lambda packet: packet['metadata']['excluded_ids'].pop(),
            lambda packet: packet['metadata']['excluded_ids'].__setitem__(0,
                packet['metadata']['all_ordered_ids'][0]),
            lambda packet: packet['metadata']['all_ordered_ids'].reverse(),
            lambda packet: packet['views'].reverse(),
            lambda packet: packet['metadata'].update(citation_candidates_file_sha256='0' * 64),
            lambda packet: packet['metadata'].update(preparation_script_sha256='0' * 64),
            lambda packet: packet['views'][0].update(answer='SECRET_GOLD'),
            lambda packet: packet['views'][0]['units'][0].update(text='Mutated payload'),
        )
        for edit in edits:
            packet = candidate_packet(d)
            edit(packet)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                n.prepare(packet, d, CharacterTokenizer())

    def test_prepare_retains_padding_failure_and_selects_next_unseen_eligible(self):
        d, tokenizer = design(), CharacterTokenizer()
        packet = candidate_packet(d)
        unavailable = packet['views'][1]['id']
        real = n.prepare_control

        def no_control_for_one(view, spec, tok):
            return None if view['id'] == unavailable else real(view, spec, tok)

        with patch.object(n, 'prepare_control', side_effect=no_control_for_one):
            plan = n.prepare(packet, d, tokenizer)
        self.assertEqual(plan['views'], packet['views'][2:34])
        self.assertEqual(plan['eligibility'][1]['reason'], 'padding_token_match_unavailable')
        self.assertFalse(plan['eligibility'][1]['selected'])
        self.assertNotIn(unavailable, plan['controls'])

    def test_seventeen_calls_three_priors_four_paired_arms_and_two_references(self):
        view, d, tokenizer = fixture(), design(), CharacterTokenizer()
        control = n.prepare_control(view, d, tokenizer)
        before = deepcopy((view, d, control))
        client = FakeClient(tokenizer)
        result = n.run_item(client, d, view, 5, control)
        self.assertEqual(len(client.calls), 17)
        self.assertEqual(len({row['key'] for row in client.calls}), 17)
        self.assertEqual([row['key'] for row in client.calls[:3]],
                         [f'i5-initial-a{agent}' for agent in range(3)])
        self.assertEqual(len(result['initial']), 3)
        self.assertEqual(len(result['outputs']), 14)
        rows = {row['key']: row for row in client.calls}
        for agent in range(3):
            paired = [rows[f'i5-{arm}_a{agent}'] for arm in
                      ('once', 'replay', 'position', 'padding')]
            self.assertEqual(len({row['seed'] for row in paired}), 1)
            for row in paired:
                self.assertIn(f'Answer: prior-i5-initial-a{agent}\n', row['messages'][1]['content'])
                self.assertEqual(row['visible'], {u['id'] for u in view['units']})
            self.assertEqual(paired[0]['messages'], n.prompt(view, view['units'], agent,
                                                           result['initial'][agent]))
            self.assertEqual(paired[2]['messages'], n.prompt(view, n.position_units(view, d),
                                                           agent, result['initial'][agent]))
            self.assertEqual(paired[1]['messages'], n.prompt(view, view['units'], agent,
                result['initial'][agent], control['replay_appendix']))
            self.assertEqual(paired[3]['messages'], n.prompt(view, view['units'], agent,
                result['initial'][agent], control['padding_appendix']))
            self.assertEqual(len(tokenizer.apply_chat_template(paired[1]['messages'], True, True)),
                             len(tokenizer.apply_chat_template(paired[3]['messages'], True, True)))
        self.assertEqual(rows['i5-single_union']['messages'], n.prompt(view, view['units']))
        self.assertEqual(rows['i5-question_only']['messages'], n.prompt(view, []))
        self.assertEqual(rows['i5-question_only']['visible'], set())
        for row in client.calls:
            self.assertEqual(row['messages'][0]['content'], old.SYSTEM + interface.EXPLICIT)
            self.assertEqual(row['decoder'], 'json_object')
        self.assertEqual((view, d, control), before)
        other = FakeClient(tokenizer)
        self.assertEqual(n.run_item(other, d, view, 5, control), result)
        self.assertEqual(other.calls, client.calls)

    def test_actual_s0_mismatch_preserves_failure_and_never_retunes_or_calls_final(self):
        view, d = fixture(), design()
        control = n.prepare_control(view, d, CharacterTokenizer())

        class BoundaryTokenizer(CharacterTokenizer):
            def apply_chat_template(self, messages, tokenize, add_generation_prompt):
                tokens = super().apply_chat_template(messages, tokenize, add_generation_prompt)
                text = messages[1]['content']
                if 'Your previous assessment' in text and control['padding_appendix'] in text:
                    tokens.append(-1)
                return tokens

        client = FakeClient(BoundaryTokenizer())
        frozen = deepcopy(control)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch.object(n, 'prepare_control', side_effect=AssertionError('Do not retune')):
                with self.assertRaises(ValueError):
                    n.run_item(client, d, view, 0, control, directory)
            self.assertEqual(len(client.calls), 3)
            self.assertTrue(all('initial' in row['key'] for row in client.calls))
            failure = directory / 'control-check-failure-0.json'
            self.assertTrue(failure.is_file())
            self.assertIn(view['id'], failure.read_text())
            self.assertFalse((directory / 'result-0.json').exists())
            self.assertFalse((directory / 'completion.json').exists())
        self.assertEqual(control, frozen)

    def test_invalid_private_outputs_do_not_remove_question_or_trigger_retries(self):
        view, d, tokenizer = fixture(), design(), CharacterTokenizer()
        control = n.prepare_control(view, d, tokenizer)
        client = FakeClient(tokenizer, invalid_initial=True)
        result = n.run_item(client, d, view, 0, control)
        self.assertEqual(len(client.calls), 17)
        self.assertTrue(all(not output['format_valid'] for output in result['initial']))
        finals = [row for row in client.calls if any(
            row['key'].startswith(f'i0-{arm}_') for arm in ('once', 'replay', 'position', 'padding'))]
        self.assertEqual(len(finals), 12)
        self.assertTrue(all('unavailable (invalid prior output)' in row['messages'][1]['content']
                            for row in finals))

    def test_actual_context_overflow_fails_before_network_without_truncation(self):
        class TooLongTokenizer(CharacterTokenizer):
            def apply_chat_template(self, messages, tokenize, add_generation_prompt):
                return [0] * 4097

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            client = n.Client(config(), 'http://127.0.0.1:8000/v1', directory, TooLongTokenizer())
            with patch.object(client.opener, 'open') as network:
                with self.assertRaisesRegex(ValueError, 'context budget'):
                    client.call('too-long', n.prompt(fixture(), []), 19, 'json_object', [])
            network.assert_not_called()
            self.assertFalse((directory / 'too-long.json').exists())

    def test_audit_uses_matching_recorded_tokens_and_rejects_changed_prefix(self):
        messages = n.prompt(fixture(), [])
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / 'fixture.json'
            path.write_text(json.dumps(dict(request=dict(messages=messages),
                                            tokenizer_prompt_tokens=137)))
            audit = n.Audit(directory, config())
            self.assertEqual(audit.recorded_token_count('fixture', messages), 137)
            changed = deepcopy(messages)
            changed[0]['content'] += ' changed'
            with self.assertRaisesRegex(ValueError, 'Token-check prompt mismatch'):
                audit.recorded_token_count('fixture', changed)
            for invalid in (True, 0, -1, 1.5):
                path.write_text(json.dumps(dict(request=dict(messages=messages),
                                                tokenizer_prompt_tokens=invalid)))
                with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, 'token count invalid'):
                    audit.recorded_token_count('fixture', messages)

    def test_existing_result_is_never_overwritten_or_followed_by_new_calls(self):
        view, d = fixture(), design()
        control = n.prepare_control(view, d, CharacterTokenizer())
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / 'result-0.json'
            path.write_text('PRESERVE ORIGINAL FIXTURE')
            with self.assertRaisesRegex(ValueError, 'Result exists; no overwrite'):
                n.run_item(client, d, view, 0, control, directory)
            self.assertEqual(path.read_text(), 'PRESERVE ORIGINAL FIXTURE')
            self.assertEqual(client.calls, [])

    def test_control_failure_file_is_not_overwritten_by_repeat_failure(self):
        view, d = fixture(), design()
        control = n.prepare_control(view, d, CharacterTokenizer())
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / 'control-check-failure-0.json'
            path.write_text('PRESERVE ORIGINAL CONTROL FAILURE')
            client = FakeClient()
            with patch.object(n, '_actual_count', side_effect=ValueError('Fixture token-check failure')):
                with self.assertRaisesRegex(ValueError, 'Control failure record exists'):
                    n.run_item(client, d, view, 0, control, directory)
            self.assertEqual(path.read_text(), 'PRESERVE ORIGINAL CONTROL FAILURE')
            self.assertFalse((directory / 'result-0.json').exists())
            self.assertEqual(client.calls, [])

    def test_live_client_never_adds_grammar_treatment(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            client = n.Client(config(), 'http://127.0.0.1:8000/v1', directory, CharacterTokenizer())
            with patch.object(client.opener, 'open') as network:
                with self.assertRaisesRegex(ValueError, 'Only frozen generic JSON'):
                    client.call('wrong-decoder', n.prompt(fixture(), []), 19, 'guided_grammar', [])
            network.assert_not_called()
            self.assertFalse((directory / 'wrong-decoder.json').exists())

    def test_inherited_client_rejects_remote_endpoint_and_preserves_http_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with self.assertRaises(ValueError):
                n.Client(config(), 'https://example.org/v1', directory, CharacterTokenizer())
            client = n.Client(config(), 'http://127.0.0.1:8000/v1', directory, CharacterTokenizer())
            messages = n.prompt(fixture(), [])
            body = b'{"error":"software fixture; no model inference"}'
            failure = urllib.error.HTTPError(client.endpoint, 400, 'Fixture', {}, io.BytesIO(body))
            with patch.object(client.opener, 'open', side_effect=failure) as network:
                with self.assertRaises(urllib.error.HTTPError):
                    client.call('fixture', messages, 19, 'json_object', [])
            network.assert_called_once()
            path = directory / 'fixture.json'
            before = path.read_bytes()
            record = json.loads(before)
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['http_status'], 400)
            self.assertEqual(record['error_body'], body.decode())
            self.assertEqual(record['request_hash'], digest(record['request']))
            self.assertIsNone(record['usage'])
            self.assertNotIn('outcome', record)
            with patch.object(client.opener, 'open') as network:
                with self.assertRaisesRegex(ValueError, 'no overwrite/retry'):
                    client.call('fixture', messages, 19, 'json_object', [])
            network.assert_not_called()
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
