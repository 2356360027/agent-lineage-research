import copy
import json
from pathlib import Path
import unittest

from scripts.plot_reuse_execution import outcome_counts, validate_record

ROOT = Path(__file__).resolve().parents[1]


class Tests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / 'configs/run_records/reuse_e3_qwen_20261027.json').read_bytes())

    def test_both_sealed_records_pass(self):
        validate_record(self.data)
        d = json.loads((ROOT / 'configs/run_records/reuse_e3_mistral_20261027.json').read_bytes())
        validate_record(d)

    def test_outcomes_partition_all_conditions(self):
        for prices in self.data['groups'].values():
            for row in prices.values():
                self.assertEqual(sum(outcome_counts(row)), 24)

    def test_missing_price_rejected(self):
        del self.data['groups']['M1']['20']
        with self.assertRaises(ValueError): validate_record(self.data)

    def test_missing_method_rejected(self):
        del self.data['groups']['M6']
        with self.assertRaises(ValueError): validate_record(self.data)

    def test_invalid_cost_rejected(self):
        for value in (0, -1, float('nan'), float('inf')):
            d = copy.deepcopy(self.data)
            d['groups']['M1']['1']['mean_modeled_total_seconds_by_cache_uses']['1'] = value
            with self.assertRaises(ValueError): validate_record(d)

    def test_missing_amortization_rejected(self):
        del self.data['groups']['M1']['1']['mean_modeled_total_seconds_by_cache_uses']['100']
        with self.assertRaises(ValueError): validate_record(self.data)

    def test_wrong_denominator_rejected(self):
        self.data['groups']['M1']['1']['n'] = 23
        with self.assertRaises(ValueError): validate_record(self.data)

    def test_lucky_negative_reuse_requires_new_figure_design(self):
        self.data['groups']['M1']['1']['negative_snapshot_lucky_cache_matches'] = 1
        with self.assertRaises(ValueError): validate_record(self.data)

    def test_impossible_outcome_rejected(self):
        self.data['groups']['M1']['1']['no_final_answer'] = 1
        with self.assertRaises(ValueError): validate_record(self.data)


if __name__ == '__main__': unittest.main()
