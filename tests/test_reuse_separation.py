import copy
import json
from pathlib import Path
import tempfile
import unittest

from runtime.reuse_separation import (PRICES, audit_design, calibration_cases, classical_transform,
    classical_verdict, jobs, messages, parse_response, query_world, route, summarize, without_costs)
from scripts.run_local_separation import prepare, sha


class Tests(unittest.TestCase):
    def test_scope_and_collision_oracles(self):
        self.assertEqual(audit_design()['model_calls'], 32)
        self.assertEqual(len({c['base_id'] for c in calibration_cases()}), 2)
        self.assertEqual(len({j['id'] for j in jobs()}), 32)
        for case in calibration_cases():
            audit, public = case['audit'], case['public']
            self.assertEqual(query_world(public['schema_sql'], audit['world_rows'],
                public['history_sql'])['rows'], public['cache']['rows'])
            if not audit['sufficient']:
                self.assertEqual(audit['witness_cache']['rows'], public['cache']['rows'])
                self.assertNotEqual(audit['witness_current_result'], audit['current_result'])

    def test_classical_baseline_uses_no_oracle(self):
        for case in calibration_cases():
            public = case['public']
            expected = 'applicable' if case['audit']['sufficient'] else 'inapplicable'
            self.assertEqual(classical_verdict(public), expected)
            if expected == 'applicable':
                self.assertEqual(classical_transform(public), case['audit']['current_result']['rows'][0][0])
            changed = copy.deepcopy(public)
            changed['cache']['complete'] = False
            self.assertEqual(classical_verdict(changed), 'unknown')
            changed['cache']['complete'] = True
            changed['current_sql'] = 'SELECT 7 AS answer'
            self.assertEqual(classical_verdict(changed), 'unknown')

    def test_empty_cache_not_automatically_lossy(self):
        for case in calibration_cases():
            if case['audit']['sufficient']:
                continue
            public = copy.deepcopy(case['public'])
            public['cache']['rows'] = [] if case['template'] == 'entity_projection' else [[0]]
            self.assertEqual(classical_verdict(public), 'applicable')

    def test_price_only_and_no_oracle_in_prompt(self):
        for case in calibration_cases():
            for method in ('M1', 'M2'):
                group = [messages(case, j) for j in jobs()
                         if j['case_id'] == case['id'] and j['method'] == method]
                self.assertEqual(len(group), 3)
                self.assertEqual(without_costs(group[0]), without_costs(group[1]))
                self.assertEqual(without_costs(group[1]), without_costs(group[2]))
                for m in group:
                    user = json.loads(m[1]['content'])
                    self.assertNotIn('sufficient', user)
                    self.assertNotIn('audit', user)
                    self.assertNotIn('world_rows', user)
                    self.assertNotIn('current_result', user)
            blind = next(j for j in jobs() if j['case_id'] == case['id'] and j['method'] == 'M3')
            visible = next(j for j in jobs() if j['case_id'] == case['id'] and j['method'] == 'M2')
            self.assertEqual(messages(case, blind), without_costs(messages(case, visible)))

    def test_verifier_is_new_priceblind_context(self):
        completed = set()
        cases = {c['id']: c for c in calibration_cases()}
        for job in jobs():
            if job['method'] == 'M4':
                self.assertIn(job['proposal_id'], completed)
                m = messages(cases[job['case_id']], job, 'applicable')
                self.assertEqual([v['role'] for v in m], ['system','user'])
                user = json.loads(m[1]['content'])
                self.assertNotIn('costs', user)
                self.assertNotIn('candidate_reason', user)
                self.assertNotIn('candidate_action', user)
                self.assertEqual(user['candidate_admissibility'], 'applicable')
            completed.add(job['id'])

    def test_controller(self):
        self.assertEqual([route('applicable', p) for p in PRICES], ['reuse','recompute','recompute'])
        for verdict in ('inapplicable','unknown','invalid'):
            self.assertEqual(route(verdict, 1), 'recompute')
            self.assertEqual(route(verdict, 1, recompute_available=False), 'abstain')

    def test_strict_parse_and_invalid_denominator(self):
        def raw(obj, stop='stop'):
            return {'choices': [{'finish_reason': stop, 'message': {'content': json.dumps(obj)}}]}
        self.assertFalse(parse_response(raw({'admissibility':'applicable'}), 'M2')['valid'])
        self.assertFalse(parse_response(raw({'admissibility':'APP','basis':'x'}), 'M2')['valid'])
        self.assertFalse(parse_response(raw({'admissibility':'applicable','basis':'x'}, 'length'), 'M2')['valid'])
        j = next(j for j in jobs() if j['method'] == 'M2')
        result = summarize({j['id']: raw({'bad': 'output'})})
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['methods']['M2']['invalid'], 1)
        self.assertEqual(result['methods']['M2']['decision_cells'], 1)
        self.assertEqual(result['judgments'][0]['admissibility'], 'invalid')

    def test_prepare_freezes_no_generation_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'new'
            result = prepare(root, Path(temp) / 'assets', Path(temp) / 'runtime')
            self.assertEqual(result['manifest_sha256'], sha(root / 'manifest.json'))
            self.assertFalse((root / 'execution_started.json').exists())
            self.assertEqual(len(json.loads((root / 'jobs.json').read_bytes())), 32)
            with self.assertRaises(FileExistsError):
                prepare(root, Path(temp), Path(temp))


if __name__ == '__main__':
    unittest.main()
