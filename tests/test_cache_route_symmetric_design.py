import itertools
import json
import unittest
from runtime.cache_route_symmetric_design import build,audit,classify


class Tests(unittest.TestCase):
    def setUp(self):
        self.common=dict(shared_contract='Shared',history_contract='History',current_contract='Current',
            schema=[['table','t','t','CREATE TABLE t(x)']],history_sql='SELECT x FROM t',database_sha256='a'*64)

    def test_four_contrasts(self):
        result=audit(build(self.common),self.common)
        self.assertEqual(len(result['contrasts']),4)
        self.assertTrue(result['price_order_reversed'])

    def test_shared_edit_rejected(self):
        cells=build(self.common)
        for cell in cells:
            p=json.loads(cell['messages'][1]['content']); p['current_contract']='Changed'
            cell['messages'][1]['content']=json.dumps(p)
        with self.assertRaises(ValueError): audit(cells,self.common)

    def test_extra_gold_field_rejected(self):
        self.common['gold_count']=63
        with self.assertRaises(ValueError): build(self.common)

    def test_all_tool_sequences_affordable(self):
        for price in (1,20):
            for length in range(4):
                for sequence in itertools.product((10,price),repeat=length):
                    self.assertLessEqual(sum(sequence),60)
            for first_two in itertools.product((10,price),repeat=2):
                self.assertGreaterEqual(60-sum(first_two),10)

    def test_third_slot_symmetric(self):
        for generation in (1,2,3):
            for action in ({'kind':'read_cache','cache_id':'v000'},{'kind':'run_sql','sql':'SELECT 1'}):
                self.assertTrue(classify(json.dumps(action),generation)['dispatch_tool'])

    def test_fourth_slot_no_tools(self):
        for action in ({'kind':'read_cache','cache_id':'v000'},{'kind':'run_sql','sql':'SELECT 1'}):
            self.assertEqual(classify(json.dumps(action),4)['status'],'TOOL_HORIZON_VIOLATION')

    def test_early_and_final_answer(self):
        for generation in (1,2,3,4):
            for kind in ('answer','abstain'):
                result=classify(json.dumps(dict(kind=kind,text='x')),generation)
                self.assertTrue(result['stop']); self.assertFalse(result['dispatch_tool'])

    def test_duplicate_keys_rejected(self):
        self.assertEqual(classify('{"kind":"answer","kind":"read_cache","text":"x"}',1)['status'],'INVALID_ACTION')

    def test_invalid_generation(self):
        for value in (0,5,True,2.0):
            with self.assertRaises(ValueError): classify('{}',value)

    def test_bad_fingerprint(self):
        self.common['database_sha256']='x'*64
        with self.assertRaises(ValueError): build(self.common)


if __name__=='__main__': unittest.main()
