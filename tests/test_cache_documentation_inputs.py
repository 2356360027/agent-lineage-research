from copy import deepcopy
import json
import unittest
from runtime.cache_documentation_inputs import build, DOCUMENTATION, HISTORIES


def fixture():
    return {'jobs':[dict(history_id=h,read_price=1,envelope={'messages':[
        {'role':'system','content':'Read-only tools. Stage 1: output contract.'},
        {'role':'user','content':json.dumps(dict(current_request='fixture', history={'sql':'SELECT 1'},
            context=dict(cache=dict(cache_id='v000',materialized_available=True,read_cost=1),sql_call_cost=10)))}]})
        for h in HISTORIES]}


class InputTests(unittest.TestCase):
    def test_only_literal_documentation_changes(self):
        source = fixture()
        preserved = deepcopy(source)
        result = build(source)
        self.assertEqual(source, preserved)
        self.assertEqual(result, build(source))
        self.assertEqual(len(result['jobs']), 12)
        for hid in HISTORIES:
            pair={j['condition']:deepcopy(j['payload']) for j in result['jobs'] if j['history_id']==hid}
            s=pair['documented']['messages'][0]['content']
            self.assertTrue(s.endswith('\n\n'+DOCUMENTATION))
            pair['documented']['messages'][0]['content']=s[:-len('\n\n'+DOCUMENTATION)]
            self.assertEqual(pair['original'],pair['documented'])
            self.assertNotIn('Stage 1:',pair['original']['messages'][0]['content'])
        self.assertFalse(result['authorization'])

    def test_missing_or_duplicate_history_rejected(self):
        source=fixture(); source['jobs'].pop()
        with self.assertRaises(ValueError): build(source)
        source=fixture(); source['jobs'].append(deepcopy(source['jobs'][0]))
        with self.assertRaises(ValueError): build(source)

    def test_unavailable_input_rejected(self):
        source=fixture(); m=source['jobs'][0]['envelope']['messages'][1]
        packet=json.loads(m['content']); packet['context']['cache']['materialized_available']=False
        m['content']=json.dumps(packet)
        with self.assertRaises(ValueError): build(source)
