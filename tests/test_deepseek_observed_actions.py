import unittest
from runtime.bird_readonly_audit import fingerprints
from scripts.audit_deepseek_observed_actions import describe


class Tests(unittest.TestCase):
    def trace(self, rows, complete=True):
        observation = {'status':'EXECUTED','complete':complete,'columns':['x'],
                       'rows':rows,'charged_units':10}
        return {'termination':'ANSWERED','events':[
            {'parsed_action':{'kind':'run_sql'},'tool':{
                'tool_event':'t001','request':{'tool':'run_sql'},
                'raw_response':{'observation':observation},
                'visible_observation':observation,'included_in_later_saved_request':True}},
            {'parsed_action':{'kind':'answer','text':'deliberately wrong final text'},'tool':None}]}

    def test_equal_bag_is_not_final_correctness(self):
        rows = [[['integer','1']],[['integer','2']]]
        result = describe(self.trace(list(reversed(rows))), fingerprints(rows)['bag_sha256'])
        self.assertTrue(result['tool_observations'][0]['bag_equals_scripted_witness'])
        for field in ('final_text_correctness','program_semantic_equivalence','contract_semantic_label','C_label'):
            self.assertIsNone(result[field])

    def test_multiplicity_and_type_are_preserved(self):
        reference = fingerprints([[['integer','1']]])['bag_sha256']
        for rows in ([[['integer','1']],[['integer','1']]], [[['text','1']]]):
            result = describe(self.trace(rows), reference)
            self.assertFalse(result['tool_observations'][0]['bag_equals_scripted_witness'])

    def test_incomplete_stays_unknown(self):
        result = describe(self.trace([],False), fingerprints([])['bag_sha256'])
        self.assertIsNone(result['tool_observations'][0]['bag_equals_scripted_witness'])

    def test_invalid_typed_result_rejected(self):
        with self.assertRaises(ValueError):
            describe(self.trace([[['integer','bogus']]]), 'unused')


if __name__ == '__main__':
    unittest.main()
