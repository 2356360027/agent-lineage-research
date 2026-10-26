"""CPU-only synthetic guard tests; no private data or model service needed."""
import copy
import unittest

from scripts.audit_reuse_result_tables import (
    METHODS, PRICES, bracket, percentage, supplemental_action_bounds, validate_e3,
)


def action_fixture():
    return {'low_price_actions': {
        'M3': dict(negative_n=96, positive_n=96, unsafe_reuse=96, useful_reuse=96, unresolved_actions=0),
        'M1': dict(negative_n=96, positive_n=96, unsafe_reuse=68, useful_reuse=63, unresolved_actions=44),
        'M6': dict(negative_n=96, positive_n=96, unsafe_reuse=0, useful_reuse=80, unresolved_actions=0)},
        'labels': {'M1': {'1': {'negative_labels': {'invalid': 17}, 'positive_labels': {'invalid': 27}}}}}


def execution_fixture():
    groups, rows = {}, []
    for method in METHODS:
        groups[method] = {}
        for price in PRICES:
            groups[method][price] = dict(n=2, positive_n=1, negative_n=1,
                final_correct=2, no_final_answer=0, fallbacks=0, cache_first_correct=0,
                positive_cache_first_correct=0, same_SQL_correct_in_both_test_worlds=0,
                reuse_selected=0, negative_reuse_selected=0, logical_calls=0,
                first_statuses={'RECOMPUTED': 2},
                mean_modeled_total_seconds_by_cache_uses={'1': 1.0, '10': 1.0, '100': 1.0})
            for sufficient in (False, True):
                rows.append(dict(cell_id=f'{method}-{price}-{sufficient}', method=method,
                    price=int(price), sufficient=sufficient, final_correct=True,
                    has_final_answer=True, fallback=False, cache_first_correct=False,
                    both_test_worlds_correct=False, action='recompute', first_status='RECOMPUTED',
                    decision_cost={'calls': 0}, transform_cost={'calls': 0},
                    modeled_total_seconds_by_cache_uses={'1': 1.0, '10': 1.0, '100': 1.0}))
    public = {'logical_cells': 36, 'groups': groups}
    sealed = copy.deepcopy(public)
    sealed['rows'] = rows
    return public, sealed


class Tests(unittest.TestCase):
    def test_rounding_is_explicit_half_up(self):
        self.assertEqual(percentage(.03125), '3.13')
        self.assertEqual(percentage(-.07291666666666667), '-7.29')
        self.assertEqual(bracket([.0520833333333333, .3854166666666667]), '[5.21, 38.54]')

    def test_posthoc_negative_bounds_keep_unresolved(self):
        self.assertEqual(supplemental_action_bounds(action_fixture(), 'M3_minus_M1_unsafe_reuse'),
                         [11 / 96, 28 / 96])

    def test_posthoc_positive_bounds_not_negative_denominator(self):
        self.assertEqual(supplemental_action_bounds(action_fixture(), 'M3_minus_M1_useful_reuse'),
                         [6 / 96, 33 / 96])

    def test_classical_fallback_is_not_unresolved(self):
        self.assertEqual(supplemental_action_bounds(action_fixture(), 'M3_minus_M6_useful_reuse'),
                         [16 / 96, 16 / 96])

    def test_unexpected_controller_unresolved_rejected(self):
        data = action_fixture()
        data['low_price_actions']['M3']['unresolved_actions'] = 1
        with self.assertRaises(ValueError):
            supplemental_action_bounds(data, 'M3_minus_M1_unsafe_reuse')

    def test_unknown_endpoint_rejected(self):
        with self.assertRaises(ValueError):
            supplemental_action_bounds(action_fixture(), 'M3_minus_M1_success')

    def test_e3_complete_fixture(self):
        validate_e3(*execution_fixture())

    def test_e3_duplicate_cell_rejected(self):
        public, sealed = execution_fixture()
        sealed['rows'][1]['cell_id'] = sealed['rows'][0]['cell_id']
        with self.assertRaises(ValueError):
            validate_e3(public, sealed)

    def test_e3_joint_aggregate_corruption_caught_from_rows(self):
        public, sealed = execution_fixture()
        public['groups']['M3']['1']['final_correct'] = 1
        sealed['groups']['M3']['1']['final_correct'] = 1
        with self.assertRaises(ValueError):
            validate_e3(public, sealed)

    def test_e3_shared_cost_corruption_caught_from_rows(self):
        public, sealed = execution_fixture()
        public['groups']['M6']['20']['mean_modeled_total_seconds_by_cache_uses']['100'] = 3.0
        sealed['groups']['M6']['20']['mean_modeled_total_seconds_by_cache_uses']['100'] = 3.0
        with self.assertRaises(ValueError):
            validate_e3(public, sealed)

    def test_e3_missing_price_rejected(self):
        public, sealed = execution_fixture()
        del public['groups']['M2']['20']
        del sealed['groups']['M2']['20']
        with self.assertRaises(ValueError):
            validate_e3(public, sealed)


if __name__ == '__main__':
    unittest.main()
