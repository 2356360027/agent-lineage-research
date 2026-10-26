import copy
import json
import unittest
from runtime.reuse_evaluation import evaluate
from runtime.reuse_scenarios import build_suite
from runtime.reuse_protocol import schedule,batches,messages
from runtime.reuse_local_transport import validate_usage
from scripts.run_reuse_development import validate_config


def response(verdict,action=None):
    obj={'admissibility':verdict,'basis':'Test fixture, not model evidence.'}
    if action is not None:obj['action']=action
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(obj)}}]}


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases=build_suite('development')
        cls.planner=[{'case_id':c['case_id'],'admissibility':'unknown','planner_status':'PLANNED'} for c in cls.cases]

    def test_complete_fixture_counts_and_M4_charges(self):
        by_case={c['case_id']:c for c in self.cases}
        replies={};measurements={}
        for job in schedule(self.cases):
            good=by_case[job['case_id']]['audit']['sufficient']
            verdict='applicable' if good else 'inapplicable'
            action=('reuse' if good and job['price']<10 else 'recompute') if job['method']=='M1' else None
            replies[job['job_id']]=response(verdict,action)
            measurements[job['job_id']]={'seconds':1,'usage':{'prompt_tokens':100,'completion_tokens':10}}
        result=evaluate(self.cases,replies,measurements,self.planner)
        self.assertEqual(result['responses_recorded'],480)
        self.assertEqual(result['methods']['M4']['logical_workflow_calls_planned'],288)
        self.assertEqual(result['methods']['M4']['logical_input_tokens_observed'],28800)
        self.assertEqual(result['methods']['M3']['useful_reuse'],24)
        self.assertEqual(result['methods']['M3']['unique_planned_judgments'],48)
        self.assertEqual(result['price_contrasts']['M1']['paired_bases'],24)

    def test_missing_entire_matrix_never_disappears(self):
        result=evaluate(self.cases,{}, {},self.planner)
        self.assertEqual(result['missing_calls'],480)
        self.assertEqual(result['methods']['M1']['unresolved_actions'],144)
        self.assertEqual(result['methods']['M2']['judgment_status_counts'],{'missing':144})
        self.assertEqual(result['price_contrasts']['M1']['unresolved_worst_case_difference_bounds'],[-1,1])
        self.assertEqual(result['methods']['M3']['useful_reuse'],0)

    def test_tokens_and_cache_fail_closed(self):
        raw={'usage':{'prompt_tokens':100,'completion_tokens':10},'timings':{'cache_n':0}}
        validate_usage(raw,100,4096,256)
        with self.assertRaises(ValueError):validate_usage(raw,101,4096,256)
        with self.assertRaises(ValueError):validate_usage(raw,100,200,256)
        raw['timings']['cache_n']=1
        with self.assertRaises(ValueError):validate_usage(raw,100,4096,256)

    def test_fixed_limits(self):
        conf={'scope':'DEVELOPMENT_ONLY','planned_calls':480,'batch_sizes':[200,200,80],
            'server_seconds_limit':600,'max_tokens':256,'context_size':4096,
            'max_attempts_new_goal_before':32,'service_seconds_new_goal_before':17.2614,
            'port':18090,'seed':20261027}
        validate_config(conf)
        for key,value in (('planned_calls',481),('server_seconds_limit',3601),
                          ('max_attempts_new_goal_before',9999),('service_seconds_new_goal_before',72000)):
            changed=dict(conf);changed[key]=value
            with self.assertRaises(ValueError):validate_config(changed)

    def test_no_private_fields_and_verifier_no_prices(self):
        jobs=schedule(self.cases);by_case={c['case_id']:c for c in self.cases}
        self.assertEqual([len(b) for b in batches(jobs)],[200,200,80])
        for job in jobs:
            data=json.loads(messages(by_case[job['case_id']],job,'inapplicable')[1]['content'])
            self.assertNotIn('audit',data)
            self.assertNotIn('positive_transform_sql',data)
            if job['method']=='M4':
                self.assertNotIn('costs',data)
                self.assertNotIn('candidate_action',data)
                self.assertEqual(data['candidate_admissibility'],'inapplicable')


if __name__=='__main__':unittest.main()
