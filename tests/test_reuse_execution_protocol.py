import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from runtime.reuse_execution_protocol import (build_cells,select_cases,transform_messages,
    execute_cell,score_execution,measure_cache_construction)
from runtime.reuse_scenarios import build_suite


def response(sql):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'sql':sql})}}]}


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.cases=build_suite('confirmation_candidate')

    def cell(self,case,action='reuse',method='M1'):
        return {'cell_id':'unit-test-not-model-result','case_id':case['case_id'],
                'method':method,'price':1,'action':action}

    def test_all_cells_and_outcome_independent_selection(self):
        rows=[dict(self.cell(c,method=m),price=p) for c in self.cases
              for m in ('M1','M2','M3','M4','M5','M6') for p in (1,10,20)]
        plan=build_cells(self.cases,rows)
        self.assertEqual(len(plan),432)
        self.assertEqual(sum(c['requires_generation'] for c in plan),288)
        self.assertEqual(plan,build_cells(self.cases,rows[::-1]))
        with self.assertRaises(ValueError):build_cells(self.cases,rows+rows)
        with self.assertRaises(ValueError):select_cases(build_suite('development'))

    def test_prompt_does_not_leak_audit_or_decision(self):
        case=copy.deepcopy(self.cases[0]);case['audit']['sentinel']='SECRET_GOLD_ONLY'
        prompt=transform_messages(case)
        self.assertNotIn('SECRET_GOLD_ONLY',str(prompt))
        data=json.loads(prompt[1]['content'])
        self.assertEqual(data['task'],case['public'])
        self.assertNotIn('costs',data['task'])
        self.assertNotIn('audit',data['task'])
        self.assertEqual(set(data),{'task','execution'})

    def test_wrong_executable_answer_never_oracle_fallback(self):
        case=self.cases[0]
        record=execute_cell(case,self.cell(case),response('SELECT -999 AS answer'))
        before=copy.deepcopy(record);score=score_execution(case,record)
        self.assertFalse(record['fallback_used'])
        self.assertFalse(score['snapshot_final_correct'])
        self.assertEqual(record,before)
        self.assertEqual(len(score['finite_worlds']),2)

    def test_refusal_invalid_or_denied_preserves_first_failure(self):
        case=self.cases[0]
        for raw in (response(None),response('DELETE FROM cache'),
                    {'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}):
            record=execute_cell(case,self.cell(case),raw)
            self.assertTrue(record['fallback_used'])
            self.assertTrue(score_execution(case,record)['snapshot_final_correct'])
            self.assertNotEqual(record['first_status'],'EXECUTED')

    def test_duplicate_sql_fields_are_invalid_not_last_wins(self):
        case=self.cases[0]
        raw={'choices':[{'finish_reason':'stop','message':{'content':'{"sql":null,"sql":"SELECT 1"}'}}]}
        result=execute_cell(case,self.cell(case),raw)
        self.assertEqual(result['first_status'],'INVALID_OUTPUT')
        self.assertTrue(result['fallback_used'])

    def test_missing_or_unresolved_not_converted_to_success(self):
        case=self.cases[0]
        for record in (execute_cell(case,self.cell(case),None),
                       execute_cell(case,self.cell(case,action='invalid'))):
            self.assertIsNone(record['final_result']);self.assertFalse(record['fallback_used'])
            self.assertFalse(score_execution(case,record)['snapshot_final_correct'])

    def test_predefined_positive_fixture_transform_not_model_evidence(self):
        for case in select_cases(self.cases):
            if not case['audit']['sufficient']:continue
            record=execute_cell(case,self.cell(case),response(case['audit']['positive_transform_sql']))
            self.assertFalse(record['fallback_used'])
            score=score_execution(case,record)
            self.assertTrue(score['snapshot_final_correct'])
            self.assertTrue(all(x['answer_matches'] for x in score['finite_worlds']))

    def test_recompute_and_cache_cost(self):
        case=self.cases[0]
        record=execute_cell(case,self.cell(case,action='recompute'))
        self.assertTrue(score_execution(case,record)['snapshot_final_correct'])
        cost=measure_cache_construction(case)
        self.assertEqual(cost['amortized_seconds']['100'],cost['total_seconds']/100)
        self.assertGreaterEqual(record['final_result']['database_setup_seconds'],0)

    def test_actual_isolated_worker_preserves_wrong_answer_and_denial(self):
        case=self.cases[0]
        script=Path(__file__).resolve().parents[1]/'scripts/run_reuse_execution.py'
        for sql,falls_back in (('SELECT -999 AS answer',False),('DELETE FROM cache',True)):
            packet={'case':case,'cell':self.cell(case),'raw':response(sql),'planner':None}
            proc=subprocess.run([sys.executable,'-B','-X','utf8',str(script),'--worker'],
                input=json.dumps(packet).encode(),capture_output=True,timeout=5)
            self.assertEqual(proc.returncode,0,proc.stderr.decode(errors='replace'))
            result=json.loads(proc.stdout)
            self.assertEqual(result['execution']['fallback_used'],falls_back)
            self.assertEqual(result['score']['snapshot_final_correct'],falls_back)

    def test_cost_totals_and_score_signature_exclude_rescore_timing(self):
        from scripts.summarize_reuse_execution import totals,score_signature
        m={'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12},'seconds':.5}
        self.assertEqual(totals([m,m]),{'calls':2,'input_tokens':20,'output_tokens':4,'http_seconds':1.0})
        bad=copy.deepcopy(m);bad['usage']['total_tokens']=13
        with self.assertRaises(ValueError):totals([bad])
        case=self.cases[0]
        record=execute_cell(case,self.cell(case),response('SELECT -999 AS answer'))
        a=score_execution(case,record);b=score_execution(case,record)
        self.assertEqual(score_signature(a),score_signature(b))

if __name__=='__main__':unittest.main()
