"""Offline prompt invariants, not model or research results."""
from copy import deepcopy
import json
import unittest
from scripts.audit_deepseek_migration import proposal, proposed_contract_payload


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.plan={'jobs':[{'id':f'test-p{p}', 'history_id':'test', 'read_price':p, 'seed':123,
            'envelope':{'messages':[{'role':'system','content':'Frozen instructions'},
                {'role':'user','content':json.dumps({'task':'synthetic', 'context':{'cache':{'read_cost':p}}})}]}}
            for p in (1,8)]}

    def test_only_price_differs_and_inputs_untouched(self):
        original=deepcopy(self.plan)
        rows,pairs=proposal(self.plan)
        self.assertEqual(self.plan,original)
        self.assertEqual(len(rows),2)
        self.assertEqual(pairs[0]['only_within_pair_difference'],'context.cache.read_cost')
        p=rows[0]['proposed_payload']
        self.assertEqual(p['messages'][1],original['jobs'][0]['envelope']['messages'][1])
        self.assertEqual(p['response_format'],{'type':'json_object'})
        self.assertNotIn('seed',p)
        self.assertEqual(p['thinking'],{'type':'disabled'})

    def test_extra_difference_is_rejected(self):
        self.plan['jobs'][1]['envelope']['messages'][0]['content']='Different instructions'
        with self.assertRaises(ValueError): proposal(self.plan)

    def test_missing_pair_is_rejected(self):
        self.plan['jobs'].pop()
        with self.assertRaises(ValueError): proposal(self.plan)

    def test_eight_descriptions_not_reference_answers(self):
        text=proposed_contract_payload(self.plan['jobs'][0])['messages'][0]['content']
        descriptions=json.loads(text.split('\n')[-1])
        self.assertEqual(set(descriptions),{'entity','time','filter','aggregation','dedup','denominator','unit','output'})


if __name__=='__main__': unittest.main()
