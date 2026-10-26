import copy
import json
import unittest

from runtime.reuse_scenarios import build_suite
from runtime.reuse_protocol import messages as main_messages
from runtime.reuse_robustness import (schedule, select_cases, messages, parse,
    audit_design, LABEL_CODES, ACTION_CODES)


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = build_suite('confirmation_candidate')
        cls.jobs = schedule(cls.cases)

    def test_fixed_selection(self):
        self.assertEqual(audit_design(self.cases)['calls'], 144)
        subset = select_cases(self.cases)
        self.assertEqual(sum(c['audit']['sufficient'] for c in subset), 12)
        self.assertEqual(schedule(list(reversed(self.cases))), self.jobs)
        with self.assertRaises(ValueError): select_cases(self.cases[:-1])

    def test_unchanged_public_and_no_gold(self):
        for case in select_cases(self.cases):
            for job in self.jobs:
                if job['case_id'] != case['case_id']: continue
                changed = copy.deepcopy(case)
                changed['audit'] = {'sufficient': 'SECRET_ORACLE', 'worlds': 'SECRET_WORLD'}
                self.assertEqual(messages(case, job), messages(changed, job))
                if job['arm'] == 'coded':
                    self.assertEqual(messages(case, job)[1], main_messages(case, job)[1])

    def test_mapping_roundtrip(self):
        job = {'method': 'M1', 'arm': 'coded'}
        for label, code in LABEL_CODES.items():
            for action, action_code in ACTION_CODES.items():
                raw = self.raw({'admissibility': code, 'action': action_code, 'basis': 'check'})
                got = parse(raw, job)
                self.assertTrue(got['valid'])
                self.assertEqual((got['admissibility'], got['action']), (label, action))

    @staticmethod
    def raw(obj, finish='stop'):
        return {'choices': [{'message': {'content': json.dumps(obj)}, 'finish_reason': finish}]}

    def test_invalid_never_rescued(self):
        job = {'method': 'M1', 'arm': 'coded'}
        for raw in [None, self.raw(None), self.raw({'admissibility': 'applicable', 'action': 'X', 'basis': ''}),
                    self.raw({'admissibility': 'a', 'action': 'X', 'basis': ''}),
                    self.raw({'admissibility': 'A', 'action': 'X', 'basis': ''}, 'length'),
                    self.raw({'admissibility': 'A', 'action': 'X', 'basis': '', 'extra': 1})]:
            self.assertFalse(parse(raw, job)['valid'])
        raw = self.raw({})
        raw['choices'][0]['message']['content'] = '{"admissibility":"A","admissibility":"B","action":"X","basis":""}'
        self.assertFalse(parse(raw, job)['valid'])

    def test_blind_no_proposal_and_no_price(self):
        for c in select_cases(self.cases):
            j = next(j for j in self.jobs if j['case_id'] == c['case_id'] and j['arm'] == 'candidate_blind')
            msg = messages(c, j)
            self.assertEqual(json.loads(msg[1]['content']), c['public'])
            self.assertNotIn('candidate_admissibility', msg[1]['content'])
            self.assertTrue(parse(self.raw({'admissibility': 'unknown', 'basis': 'check'}), j)['valid'])

    def test_resource_guards(self):
        from scripts.run_reuse_robustness import validate
        conf = dict(scope='PRESELECTED_ROBUSTNESS_ONLY', planned_calls=144, batch_sizes=[144],
                    server_seconds_limit=600, context_size=4096, max_tokens=256, temperature=0,
                    seed=20261027, cache_prompt=False, parallel=1, port=18090,
                    max_attempts_new_goal_before=4549, service_seconds_new_goal_before=1881,
                    model_family='qwen')
        validate(conf)
        for key, value in [('planned_calls', 145), ('server_seconds_limit', 601),
                           ('max_attempts_new_goal_before', 9857),
                           ('service_seconds_new_goal_before', 71401), ('temperature', 1)]:
            with self.assertRaises(ValueError): validate(dict(conf, **{key: value}))

    def test_fixed_denominator_invalid_not_success(self):
        from scripts.run_reuse_robustness import summarize_rows
        rows = [dict(arm='coded', method='M3', price=None, sufficient=i >= 12,
                     valid=False, admissibility='invalid', action='invalid', failure='missing')
                for i in range(24)]
        row = summarize_rows(rows)['coded:M3:pNone']
        self.assertEqual(row['negative_denominator'], 12)
        self.assertEqual(row['negative_invalid'], 12)
        self.assertEqual(row['positive_invalid'], 12)
        self.assertEqual(row['missing'], 24)
        self.assertEqual(row['actions']['1']['useful_reuse'], 0)


if __name__ == '__main__': unittest.main()
