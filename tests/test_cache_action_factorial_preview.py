import json
import unittest
from copy import deepcopy

from runtime import cache_action_factorial_preview as draft
from runtime import cache_action_preview as old
from runtime import cache_intent_prompts as canonical


class Tests(unittest.TestCase):
    def inputs(self):
        return dict(current_request='Count matching rows.', evidence='Match x = 1.',
                    schema=['CREATE TABLE t (id INTEGER PRIMARY KEY, x INTEGER)'],
                    history=dict(historical_request='Count all x groups.',
                                 evidence='Include all observed groups.',
                                 sql='SELECT x, COUNT(id) FROM t GROUP BY x'),
                    database_sha256='a' * 64)

    def test_exact_factors(self):
        report = draft.audit_cells(draft.make_cells(**self.inputs()))
        self.assertEqual(len(report['contrasts']), 4)
        self.assertFalse(report['inference_authorized'])
        self.assertFalse(report['price_order_reversed'])

    def test_history_evidence_required(self):
        args = self.inputs()
        del args['history']['evidence']
        with self.assertRaises(ValueError): draft.make_cells(**args)

    def test_explicit_missing_evidence(self):
        args = self.inputs()
        args['history']['evidence'] = None
        cells = draft.make_cells(**args)
        draft.audit_cells(cells)
        self.assertIsNone(json.loads(cells[0]['messages'][1]['content'])['history']['evidence'])

    def test_shared_prompt_edit_rejected(self):
        cells = deepcopy(draft.make_cells(**self.inputs()))
        for cell in cells: cell['messages'][0]['content'] += ' Prefer reuse.'
        with self.assertRaises(ValueError): draft.audit_cells(cells)

    def test_one_cell_scope_edit_rejected(self):
        cells = deepcopy(draft.make_cells(**self.inputs()))
        packet = json.loads(cells[3]['messages'][1]['content'])
        packet['history']['evidence'] = 'Only top five groups.'
        cells[3]['messages'][1]['content'] = canonical._json(packet)
        with self.assertRaises(ValueError): draft.audit_cells(cells)

    def test_no_old_runner_compatibility_claim(self):
        for cell in draft.make_cells(**self.inputs()):
            with self.assertRaises(ValueError): old.validate(cell)

    def test_inputs_not_mutated_or_shared(self):
        args = self.inputs()
        original = deepcopy(args)
        cells = draft.make_cells(**args)
        self.assertEqual(args, original)
        cells[0]['messages'][0]['content'] = 'modified'
        self.assertEqual(cells[1]['messages'][0]['content'], draft.SYSTEM)

    def test_reordered_or_duplicate_cells_rejected(self):
        cells = draft.make_cells(**self.inputs())
        for bad in (cells[::-1], (cells[0],) * 4):
            with self.assertRaises(ValueError): draft.audit_cells(bad)


if __name__ == '__main__': unittest.main()
