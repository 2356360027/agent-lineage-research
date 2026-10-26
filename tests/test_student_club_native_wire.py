"""No provider calls; wire conversion from actual offline history objects."""
from copy import deepcopy
import json
import unittest

from runtime.student_club_native_wire import payload, validate_wire
from tests.test_student_club_definition_history import make, response


class Tests(unittest.TestCase):
    def test_reverse_validation_for_all_phases(self):
        for protocol in ('ordinary','request_first','catalog_first'):
            h,_=make(protocol)
            request=h.begin()
            self.assertEqual(validate_wire(payload(request)),request)
            if protocol!='ordinary':
                h.accept(response('record_definition','own definition'))
                request=h.begin()
                self.assertEqual(validate_wire(payload(request)),request)
            for i in range(3):
                h.accept(response('run_sql','SELECT 1',f'sql{i}'))
                h.observe(f'sql{i}','{"tool_observation":{}}')
                request=h.begin()
                self.assertEqual(validate_wire(payload(request)),request)

    def test_reverse_validation_rejects_wire_tampering(self):
        h,_=make('ordinary');wire=payload(h.begin())
        for mutate in (
            lambda x:x.update(model='other'),lambda x:x.update(temperature=False),
            lambda x:x.update(max_tokens=513),lambda x:x.update(stream=True),
            lambda x:x.update(tool_choice='auto'),lambda x:x.update(extra=1),
            lambda x:x['messages'][0].update(content='other'),
            lambda x:x['messages'][1].update(content='{"a":1,"a":2}'),
            lambda x:x['messages'].append({'role':'user','content':'extra'}),
            lambda x:x['tools'][0]['function'].update(strict=False)):
            bad=deepcopy(wire);mutate(bad)
            with self.assertRaises(ValueError):validate_wire(bad)

    def test_definition_blinding_and_exact_own_text_in_action(self):
        h,_=make('request_first'); first=h.begin(); before=deepcopy(first)
        p=payload(first)
        self.assertEqual(first,before)
        self.assertEqual(len(p['messages']),2)
        self.assertNotIn('HISTORY_REQUEST_SENTINEL',str(p))
        self.assertEqual([t['function']['name'] for t in p['tools']],['record_definition'])
        own='  WRONG SYNTHETIC DEFINITION\n中文'
        h.accept(response('record_definition',own))
        p=payload(h.begin())
        packet=json.loads(p['messages'][1]['content'])
        self.assertEqual(packet['prior_agent_definition']['text'],own)
        self.assertIn('history',packet)
        self.assertEqual(p['thinking'],{'type':'disabled'})

    def test_revision_and_tool_messages_preserved_and_terminal_schema(self):
        h,_=make();h.begin();h.accept(response('record_definition','first'))
        h.begin();h.accept(response('read_cache','v000','cache'))
        h.observe('cache','{"tool_observation":{"status":"CACHE_UNAVAILABLE"}}')
        request=h.begin();wire=payload(request)
        self.assertEqual(wire['messages'][2:],request['messages'])
        h.accept(response('revise_definition','second','rev'))
        request=h.begin();wire=payload(request)
        self.assertEqual(wire['messages'][2:],request['messages'])
        h.accept(response('run_sql','SELECT 1','sql'));h.observe('sql','{"tool_observation":{}}')
        wire=payload(h.begin())
        self.assertEqual({t['function']['name'] for t in wire['tools']},{'answer','abstain'})

    def test_ordinary_no_definition_and_canonical_serialization(self):
        h,_=make('ordinary');request=h.begin();wire=payload(request)
        self.assertNotIn('prior_agent_definition',wire['messages'][1]['content'])
        self.assertNotIn('revise_definition',str(wire['tools']))
        self.assertEqual(payload(json.loads(json.dumps(request,sort_keys=True))),wire)

    def test_generation_schema_and_history_tampering_rejected(self):
        h,_=make('ordinary');r=h.begin()
        for mutate in (
            lambda x:x.update(generation=2), lambda x:x.update(action_generation=True),
            lambda x:x.update(inference_authorized=True),lambda x:x.update(tools=[]),
            lambda x:x.update(extra='unexpected'),lambda x:x.update(phase='other')):
            bad=deepcopy(r);mutate(bad)
            with self.assertRaises(ValueError):payload(bad)
        h.accept(response('run_sql','SELECT 1'));h.observe('c1','{"tool_observation":{}}');r=h.begin()
        for mutate in (
            lambda x:x['messages'][1].update(tool_call_id='wrong'),
            lambda x:x['messages'][0].update(content='extra'),
            lambda x:x['messages'].pop(),
            lambda x:x['messages'][0]['tool_calls'][0]['function'].update(name='answer',arguments='{"text":"done"}')):
            bad=deepcopy(r);mutate(bad)
            with self.assertRaises(ValueError):payload(bad)


if __name__=='__main__':unittest.main()
