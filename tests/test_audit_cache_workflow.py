"""Scripted integrity/parser tests only; no model or network."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.audit_cache_workflow import sha, verify_tree, paired_actions
from runtime.cache_intent_staged_loop import parse_action


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp_parent = Path(__file__).resolve().parents[1]/'runtime/runs'
        self.temp_parent.mkdir(parents=True, exist_ok=True)

    def test_seal_and_tamper(self):
        with tempfile.TemporaryDirectory(dir=self.temp_parent) as d:
            root = Path(d)
            (root/'item.json').write_text('{}', encoding='utf-8')
            (root/'artifact_hashes.json').write_text(json.dumps({'item.json':sha(root/'item.json')}))
            self.assertEqual(verify_tree(root), 1)
            (root/'item.json').write_text('{"changed":true}', encoding='utf-8')
            with self.assertRaises(ValueError):
                verify_tree(root)

    def test_extra_file_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.temp_parent) as d:
            root = Path(d)
            (root/'artifact_hashes.json').write_text('{}')
            (root/'extra').write_text('unexpected')
            with self.assertRaises(ValueError):
                verify_tree(root)

    def test_trailing_garbage_not_repaired(self):
        valid = '{"kind":"run_sql","sql":"SELECT 1"}'
        self.assertEqual(parse_action(valid)['kind'], 'run_sql')
        with self.assertRaises(json.JSONDecodeError):
            parse_action(valid + "'}")

    def test_pair_retains_invalid_instead_of_imputing_sql(self):
        rows=[dict(id='h-original',history_id='h',condition='original',status='INVALID_ACTION',action=None),
              dict(id='h-documented',history_id='h',condition='documented',status='ACTION_RECORDED',action='read_cache')]
        self.assertEqual(paired_actions(rows),[dict(history_id='h',original='INVALID_ACTION',documented='read_cache')])
        with self.assertRaises(ValueError): paired_actions(rows+rows[:1])


if __name__ == '__main__':
    unittest.main()
