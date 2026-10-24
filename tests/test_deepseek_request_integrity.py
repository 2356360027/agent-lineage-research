import json
import unittest
from copy import deepcopy

from runtime.bird_readonly_audit import digest
from runtime.deepseek_trajectory_adapter import make_payload
from scripts.audit_deepseek_request_integrity import check_price_pair, check_generation


class Tests(unittest.TestCase):
    def payload(self, price):
        return make_payload([{'role':'system','content':'shared'},
            {'role':'user','content':json.dumps({'request':'same','context':{'cache':{'read_cost':price}}})}])

    def test_price_only_difference(self):
        check_price_pair(self.payload(1),self.payload(8))

    def test_other_setting_or_content_difference_rejected(self):
        for key in ('temperature','messages'):
            changed = self.payload(8)
            if key == 'temperature': changed[key] = 0.1
            else: changed[key][0]['content'] += ' extra'
            with self.assertRaises(ValueError):
                check_price_pair(self.payload(1),changed)

    def test_wrong_price_rejected(self):
        with self.assertRaises(ValueError):
            check_price_pair(self.payload(1),self.payload(1))

    def test_request_to_response_links_and_tampering(self):
        request = {'messages':[{'role':'system','content':'s'},{'role':'user','content':'u'}]}
        payload = make_payload(request['messages'])
        context = {'payload_sha256':digest(payload),'truncated':False,'reference_input_tokens':10}
        raw = {'model':payload['model'],'usage':{'prompt_tokens':10,'completion_tokens':2},
               'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]}
        transport = {'transport_status':'RESPONSE_RECEIVED','response_text':json.dumps(raw)}
        response = {'request_sha256':digest(request),'response':{'request_sha256':digest(payload),
            'content':'{}','finish_reason':'stop','usage':raw['usage']}}
        args = [request, {'payload':payload}, context, {'raw':raw}, transport, response]
        check_generation(*args)
        for index in (1,2,3,4,5):
            changed = deepcopy(args)
            if index == 1: changed[index]['payload']['temperature'] = 0.2
            elif index == 2: changed[index]['truncated'] = True
            elif index == 3: changed[index]['raw']['model'] = 'other'
            elif index == 4: changed[index]['response_text'] = '{}'
            else: changed[index]['response']['content'] = 'changed'
            with self.assertRaises(ValueError):
                check_generation(*changed)


if __name__ == '__main__':
    unittest.main()
