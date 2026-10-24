"""CPU software fixtures only; no model observations or network requests."""
from copy import deepcopy
import hashlib
import io
import itertools
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from runtime import citation_interface_v2 as c
from runtime import citation_interface_v1 as historical
from runtime import natural_repetition as n
from runtime.e1 import canonical, digest
from runtime.natural_qa_data import prediction_view, seeded_order
from test_citation_interface_v1 import fixture, config, FakeClient, FakeTokenizer

ROOT = Path(__file__).resolve().parents[1]
STARTUP = ("INFO Initializing a V1 LLM engine (v0.8.3) with config: "
           "max_seq_len=4096, decoding_config=DecodingConfig("
           "guided_decoding_backend='xgrammar')")


def design():
    return json.loads((ROOT/'configs/citation_interface_v2.json').read_text())


def candidates(d):
    ids = [r['id'] for r in seeded_order(
        [dict(id=f'SOFTWARE-FIXTURE-{i}') for i in range(7205)], d['seed'])]
    views = [prediction_view(dict(_id=item, question='Fixture question?',
        context=[(f'Doc {i}', [f'Software fixture sentence {i}.'])
                 for i in range(9 if index == 0 else 10)]))
        for index, item in enumerate(ids[:64])]
    return dict(views=views, metadata=dict(design_sha256=digest(d),
        file_sha256=c.DATASET_SHA, parent_candidates_file_sha256=c.PARENT_CANDIDATES_SHA,
        excluded_ids=[f'EXCLUDED-{i}' for i in range(200)], all_ordered_ids=ids,
        preparation_script_sha256=hashlib.sha256(
            (ROOT/'scripts/prepare_citation_candidates_v2.py').read_bytes()).hexdigest()))


class CitationV2ContractTests(unittest.TestCase):
    def test_baseline_prompt_and_request_are_byte_exact(self):
        view = fixture()
        for units in (view['units'], []):
            messages = c.prompt(view, units, 'original')
            self.assertEqual(canonical(messages), canonical(n.prompt(view, units)))
            self.assertEqual(canonical(c.request(config(), messages, 37, 'json_object', [])),
                             canonical(n.request(config(), messages, 37)))
            explicit = c.prompt(view, units, 'explicit')
            self.assertEqual(explicit[1], messages[1])
            self.assertEqual(explicit[0]['content'], messages[0]['content'] + c.EXPLICIT)
            self.assertIn('"d0s0", never "[d0s0]"', c.EXPLICIT)
            self.assertIn('{"answer":"","citations":[]}', c.EXPLICIT)

    def test_guided_request_matches_v1_backend_without_fallback_suffix(self):
        messages = c.prompt(fixture(), [], 'explicit')
        for ids in ([], ['d0s0', 'd1s0']):
            before = deepcopy((messages, ids))
            actual = c.request(config(), messages, 37, 'guided_grammar', ids)
            expected = n.request(config(), messages, 37)
            expected.pop('response_format')
            expected.update(guided_decoding_backend='xgrammar', guided_grammar=c.grammar(ids))
            self.assertEqual(actual, expected)
            self.assertNotIn('guided_json', actual)
            self.assertEqual((messages, ids), before)
        full = c.grammar(['d0s0'])
        self.assertEqual(full.count('(ws "," ws id)?'), 7)
        self.assertIn('array ::= "[" ws "]"', c.grammar([]))
        self.assertIs(c.evaluate, historical.evaluate)

    def test_full_factorial_order_and_pair_seed_are_deterministic(self):
        self.assertEqual(set(c.JOBS.values()), set(itertools.product(
            ('full', 'question_only'), ('original', 'explicit'), ('json_object', 'guided_grammar'))))
        d, view, client = design(), fixture(), FakeClient()
        result = c.run_item(client, d, view, 3)
        order = list(c.JOBS)
        random.Random(f"{d['protocol']}:{d['seed']}:{view['id']}:order").shuffle(order)
        seed = int(digest([d['protocol'], d['seed'], view['id'], 'paired'])[:7], 16)
        self.assertEqual(result['order'], order)
        self.assertEqual(len(client.calls), 8)
        self.assertEqual({row['seed'] for row in client.calls}, {seed})
        self.assertEqual(len({row['key'] for row in client.calls}), 8)
        for job, row in zip(order, client.calls, strict=True):
            context, variant, decoder = c.JOBS[job]
            units = view['units'] if context == 'full' else []
            self.assertEqual(row['key'], f'i3-{job}')
            self.assertEqual(row['messages'], c.prompt(view, units, variant))
            self.assertEqual(row['decoder'], decoder)
            self.assertEqual(row['visible'], {u['id'] for u in units})
        repeated = FakeClient()
        self.assertEqual(c.run_item(repeated, d, view, 3), result)
        self.assertEqual(repeated.calls, client.calls)

    def test_existing_record_and_failed_http_are_never_overwritten_or_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            dr = Path(tmp)
            client = c.Client(config(), 'http://127.0.0.1:8000/v1', dr, FakeTokenizer())
            messages = c.prompt(fixture(), [])
            body = b'{"error":"fixture rejection, not model inference"}'
            failure = urllib.error.HTTPError(client.endpoint, 400, 'Fixture', {}, io.BytesIO(body))
            with patch.object(client.opener, 'open', side_effect=failure) as network:
                with self.assertRaises(urllib.error.HTTPError):
                    client.call('fixture', messages, 37, 'guided_grammar', [])
            network.assert_called_once()
            path = dr/'fixture.json'
            saved_bytes = path.read_bytes()
            rec = json.loads(saved_bytes)
            self.assertEqual(rec['status'], 'failed')
            self.assertEqual(rec['http_status'], 400)
            self.assertEqual(rec['error_body'], body.decode())
            self.assertIsNone(rec['usage'])
            self.assertNotIn('outcome', rec)
            self.assertEqual(rec['request_hash'], digest(rec['request']))
            with patch.object(client.opener, 'open', side_effect=AssertionError('No network')) as network:
                with self.assertRaisesRegex(ValueError, 'no overwrite/retry'):
                    client.call('fixture', messages, 37, 'guided_grammar', [])
            network.assert_not_called()
            self.assertEqual(path.read_bytes(), saved_bytes)

    def test_prepare_selects_first_eight_eligible_and_requires_new_script(self):
        d = design()
        packet = candidates(d)
        tokenizer = FakeTokenizer()
        tokenizer.chat_template = 'SOFTWARE-FIXTURE-NOT-MODEL-TEMPLATE'
        result = c.prepare(packet, d, tokenizer)
        self.assertEqual(result['views'], packet['views'][1:9])
        self.assertEqual(result['actual_calls'], 64)
        self.assertFalse(result['eligibility'][0]['eligible'])
        self.assertEqual(result['eligibility'][0]['reason'], 'structure')
        self.assertEqual(sum(row['selected'] for row in result['eligibility']), 8)
        old_hash = hashlib.sha256((ROOT/'scripts/prepare_citation_candidates.py').read_bytes()).hexdigest()
        self.assertNotEqual(old_hash, packet['metadata']['preparation_script_sha256'])
        packet['metadata']['preparation_script_sha256'] = old_hash
        with self.assertRaisesRegex(ValueError, 'Preparation script changed'):
            c.prepare(packet, d, tokenizer)

    def test_prepare_rejects_gold_and_reordered_candidates(self):
        d = design()
        packet = candidates(d)
        packet['views'][1]['answer'] = 'SECRET GOLD'
        with self.assertRaisesRegex(ValueError, 'Labels in projected input'):
            c.prepare(packet, d, FakeTokenizer())
        packet = candidates(d)
        packet['views'][0], packet['views'][1] = packet['views'][1], packet['views'][0]
        with self.assertRaisesRegex(ValueError, 'Candidate order changed'):
            c.prepare(packet, d, FakeTokenizer())


class CitationV2EngineEvidenceTests(unittest.TestCase):
    def test_real_shape_startup_line_and_sha_are_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'server.log'
            path.write_text('Other startup message\n' + STARTUP + '\nServer ready\n')
            record = c.server_engine_evidence(path)
        self.assertEqual(record['initialization_line'], STARTUP)
        self.assertEqual(record['initialization_line_sha256'], hashlib.sha256(STARTUP.encode()).hexdigest())
        self.assertEqual(record['backend'], 'xgrammar')
        c.validate_engine_evidence(record)

    def test_missing_multiple_v0_wrong_backend_or_context_rejected(self):
        invalid = ('No initialization line', STARTUP + '\n' + STARTUP,
                   STARTUP.replace('V1 LLM', 'V0 LLM'),
                   STARTUP.replace("backend='xgrammar'", "backend='outlines'"),
                   STARTUP.replace('max_seq_len=4096', 'max_seq_len=8192'),
                   STARTUP.replace('v0.8.3', 'v0.8.4'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'server.log'
            for text in invalid:
                with self.subTest(text=text), self.assertRaises(ValueError):
                    path.write_text(text)
                    c.server_engine_evidence(path)

    def test_forged_line_hash_and_multiline_record_rejected(self):
        record = dict(kind='VLLM_V1_STARTUP_LOG_EVIDENCE', version='0.8.3', backend='xgrammar',
            initialization_line=STARTUP, initialization_line_sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            c.validate_engine_evidence(record)
        for suffix in ('\nextra', '\rextra'):
            record['initialization_line'] = STARTUP + suffix
            record['initialization_line_sha256'] = hashlib.sha256(record['initialization_line'].encode()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'Unsupported engine startup line'):
                c.validate_engine_evidence(record)


if __name__ == '__main__':
    unittest.main()
