import unittest
from runtime import bird_readonly_audit as audit
from runtime.cache_intent_observation_bound import ObservationBound
from scripts.audit_deepseek_cache_controls import first_read, describe


def fixture():
    rows=[[['integer','3']]]
    return dict(status='EXECUTED',complete=True,columns=['n'],rows=rows,
                **audit.fingerprints(rows))


class Tests(unittest.TestCase):
    def test_response_shape_and_no_mutation(self):
        cache=fixture()
        response=first_read(cache,8)
        self.assertEqual(response['remaining_budget'],202)
        self.assertEqual(response['attempt'],1)
        response['rows'].clear()
        self.assertEqual(len(cache['rows']),1)

    def test_oversize_cannot_be_positive_visibility(self):
        bound=ObservationBound(lambda m: 600 if '"rows"' in m[1]['content'] else 30)
        cache=fixture()
        result=describe(cache,1,bound,cache['bag_sha256'])
        self.assertTrue(result['raw_bag_equals_witness'])
        self.assertFalse(result['visible_complete'])
        self.assertEqual(result['visible_status'],'OBSERVATION_TOO_LARGE')

    def test_mismatch_and_unassessed_are_distinct(self):
        bound=ObservationBound(lambda m: 80)
        self.assertFalse(describe(fixture(),1,bound,'0'*64)['raw_bag_equals_witness'])
        self.assertIsNone(describe(fixture(),1,bound)['raw_bag_equals_witness'])

    def test_corrupt_cache_rejected(self):
        cache=fixture(); cache['rows']=[[['integer','4']]]
        with self.assertRaises(ValueError): first_read(cache,1)
        with self.assertRaises(ValueError): first_read(fixture(),True)


if __name__ == '__main__': unittest.main()
