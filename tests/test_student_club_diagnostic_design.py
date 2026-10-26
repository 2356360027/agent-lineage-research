"""Mock prompt-boundary tests; no inference or data queries."""
from copy import deepcopy
import unittest
from runtime.student_club_diagnostic_design import build,bind_definition,PROTOCOLS,SETTINGS


def common():
    return dict(current_request='CURRENT_REQUEST_SENTINEL',current_evidence='CURRENT_EVIDENCE_SENTINEL',
        history_request='HISTORY_REQUEST_SENTINEL',history_evidence='HISTORY_EVIDENCE_SENTINEL',
        history_sql='SELECT COUNT(*) FROM synthetic',schema_projection='SYNTHETIC_SCHEMA',
        database_sha256='a'*64)


class Tests(unittest.TestCase):
    def test_full_grid_deterministic_and_no_authority(self):
        c=common();before=deepcopy(c); p=build(c)
        self.assertEqual(c,before)
        self.assertEqual(p,build(c))
        self.assertEqual(len(p['jobs']),12)
        self.assertEqual(sum(j['max_model_calls'] for j in p['jobs']),56)
        self.assertEqual(sum(j['max_tool_attempts'] for j in p['jobs']),36)
        self.assertFalse(p['inference_authorized'])
        self.assertFalse(p['runner_implemented'])
        for protocol in PROTOCOLS:
            self.assertEqual({(j['available'],j['price']) for j in p['jobs'] if j['protocol']==protocol},set(SETTINGS))

    def test_request_first_is_blind_to_catalog_and_treatment(self):
        jobs=[j for j in build(common())['jobs'] if j['protocol']=='request_first']
        self.assertTrue(all(j['definition_stage']==jobs[0]['definition_stage'] for j in jobs))
        self.assertNotIn('HISTORY_REQUEST_SENTINEL',str(jobs[0]['definition_stage']))
        self.assertNotIn('resources',jobs[0]['definition_stage']['data'])

    def test_only_treatment_fields_change_within_protocol(self):
        p=build(common())
        for protocol in PROTOCOLS:
            normalized=[]
            for j in p['jobs']:
                if j['protocol']!=protocol:continue
                data=deepcopy(j['action_stage_template'])
                data['data']['resources'].pop('materialized_available')
                data['data']['resources'].pop('read_cost')
                normalized.append(data)
                if protocol=='catalog_first':
                    self.assertEqual(j['definition_stage']['data'],j['action_stage_template']['data'])
            self.assertTrue(all(d==normalized[0] for d in normalized))

    def test_action_context_same_across_protocols_before_own_definition(self):
        jobs=build(common())['jobs']
        for a,p in SETTINGS:
            cells=[j for j in jobs if j['available']==a and j['price']==p]
            self.assertEqual(len(cells),3)
            self.assertTrue(all(j['action_stage_template']==cells[0]['action_stage_template'] for j in cells))
            self.assertGreaterEqual(60,3*max(p,10))

    def test_reference_fields_rejected_and_own_definition_not_gold(self):
        for key in ('current_sql','reference_answer','gold_contract','cache_rows'):
            with self.assertRaises(ValueError): build(common()|{key:'FORBIDDEN'})
        jobs=build(common())['jobs']
        ordinary=next(j for j in jobs if j['protocol']=='ordinary')
        self.assertIsNone(ordinary['definition_stage'])
        with self.assertRaises(ValueError):
            bind_definition(ordinary,own_definition='anything',source_event_id='g0')
        job=next(j for j in jobs if j['protocol']=='catalog_first')
        raw='Intentionally wrong SYNTHETIC task definition'
        bound=bind_definition(job,own_definition=raw,source_event_id='synthetic-g0')
        self.assertEqual(bound['data']['prior_agent_definition']['text'],raw)
        self.assertFalse(bound['data']['prior_agent_definition']['human_verified'])
        self.assertNotIn('prior_agent_definition',job['action_stage_template']['data'])


if __name__=='__main__':unittest.main()
