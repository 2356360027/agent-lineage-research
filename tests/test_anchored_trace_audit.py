import copy
import unittest
from scripts.audit_anchored_trace import posterior, inspect_record
import json


class TraceAuditTests(unittest.TestCase):
    def fixture(self):
        obs=[dict(id='a',value=0,effective_reliability=.8),dict(id='b',value=1,effective_reliability=.6)]
        all_obs=obs+[dict(obs[0],id='c')]
        p=posterior(obs)
        payload=dict(messages=all_obs,origin_registry={'a':'x','b':'y','c':'x'},
            external_calculator=dict(input_observations=obs,p_state_1=p),
            your_previous_assessment=dict(assessment=dict(answer=1,p_state_1=.6)),peer_assessments=[])
        answer=dict(answer=1,p_state_1=round(p,6),citations=['a','b'])
        return dict(request=dict(messages=[{},dict(content=json.dumps(payload))]),
            raw_response=dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(answer)))]),
            outcome=dict(format_valid=True,assessment=answer))

    def test_independent_arithmetic(self):
        self.assertAlmostEqual(posterior([dict(value=0,effective_reliability=.8),dict(value=1,effective_reliability=.6)]),3/11)

    def test_consistency_distinct_from_probability(self):
        row=inspect_record(self.fixture(),'test')
        self.assertFalse(row['posterior_wrong'])
        self.assertTrue(row['map_disagreement'])
        self.assertTrue(row['answer_equals_initial'])

    def test_reject_raw_score_mismatch(self):
        fixture=self.fixture();fixture['outcome']['assessment']['answer']=0
        with self.assertRaisesRegex(ValueError,'Raw/scored'):
            inspect_record(fixture,'test')

    def test_reject_incorrect_tool(self):
        fixture=self.fixture();payload=json.loads(fixture['request']['messages'][1]['content'])
        payload['external_calculator']['p_state_1']=.99
        fixture['request']['messages'][1]['content']=json.dumps(payload)
        with self.assertRaisesRegex(ValueError,'arithmetic'):
            inspect_record(fixture,'test')

    def test_no_mutation(self):
        fixture=self.fixture();before=copy.deepcopy(fixture)
        inspect_record(fixture,'test');self.assertEqual(before,fixture)

    def test_wrong_probability_is_not_discarded(self):
        fixture=self.fixture()
        answer=dict(answer=1,p_state_1=.6,citations=['a'])
        fixture['raw_response']['choices'][0]['message']['content']=json.dumps(answer)
        fixture['outcome']['assessment']=answer
        row=inspect_record(fixture,'test')
        self.assertTrue(row['posterior_wrong'])
        self.assertEqual(row['wrong_probability_candidate_matches'],['previous_probability'])

    def test_reject_conflicting_origin_payload(self):
        fixture=self.fixture();payload=json.loads(fixture['request']['messages'][1]['content'])
        payload['messages'][2]['value']=1
        fixture['request']['messages'][1]['content']=json.dumps(payload)
        with self.assertRaisesRegex(ValueError,'Conflicting payload'):
            inspect_record(fixture,'test')
