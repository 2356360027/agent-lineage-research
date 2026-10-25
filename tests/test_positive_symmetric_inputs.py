import copy
import json
import unittest
from scripts.prepare_positive_symmetric_inputs import remap, DATABASE_SHA
from runtime.cache_route_symmetric_design import build, audit


class Tests(unittest.TestCase):
    def setUp(self):
        self.packet = dict(context=dict(database_sha256=DATABASE_SHA),
            current_request='Current question',evidence='Current evidence',schema=['CREATE TABLE t(x)'],
            history=dict(historical_request='History plus reviewed amendment',evidence='History evidence',sql='SELECT x FROM t'))
        self.plan = dict(jobs=[dict(id=str(i),envelope=dict(messages=[{},dict(content=json.dumps(self.packet))])) for i in range(4)])

    def test_wording_preserved(self):
        common = remap(self.plan)
        self.assertEqual(common['current_contract'],'Current question\n\nEvidence:\nCurrent evidence')
        self.assertEqual(common['history_contract'],'History plus reviewed amendment\n\nEvidence:\nHistory evidence')
        self.assertEqual(common['history_sql'],'SELECT x FROM t')
        self.assertEqual(len(audit(build(common),common)['contrasts']),4)
        self.assertNotIn('reference',common)

    def test_changed_cell_rejected(self):
        self.packet['current_request']='Other'
        self.plan['jobs'][2]['envelope']['messages'][1]['content']=json.dumps(self.packet)
        with self.assertRaises(ValueError): remap(self.plan)

    def test_hidden_answer_field_rejected(self):
        self.packet['reference_answer']=2
        for job in self.plan['jobs']: job['envelope']['messages'][1]['content']=json.dumps(self.packet)
        with self.assertRaises(ValueError): remap(self.plan)

    def test_duplicate_cell_rejected(self):
        self.plan['jobs'][1]=copy.deepcopy(self.plan['jobs'][0])
        with self.assertRaises(ValueError): remap(self.plan)

    def test_snapshot_mismatch_rejected(self):
        self.packet['context']['database_sha256']='f'*64
        for job in self.plan['jobs']: job['envelope']['messages'][1]['content']=json.dumps(self.packet)
        with self.assertRaises(ValueError): remap(self.plan)


if __name__ == '__main__': unittest.main()
