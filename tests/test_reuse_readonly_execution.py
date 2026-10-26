import copy
import json
import unittest
from runtime.reuse_readonly_execution import execute_cache_sql,compare_answer,parse_transform,Limits
from runtime.reuse_scenarios import build_suite


class Tests(unittest.TestCase):
    def setUp(self):self.cache={'columns':['id','score'],'rows':[[1,1],[2,None],[3,5]]}

    def test_select_aggregate_and_null(self):
        result=execute_cache_sql(self.cache,'SELECT AVG(score) AS answer FROM cache')
        self.assertEqual(result['status'],'EXECUTED')
        self.assertTrue(compare_answer(result,{'columns':['answer'],'rows':[[3.0]]}))
        self.assertEqual(result['read_relations'],['cache'])
        self.assertFalse(result['semantic_admissibility_proven'])

    def test_empty_count_and_avg(self):
        cache={'columns':['score'],'rows':[]}
        result=execute_cache_sql(cache,'SELECT COUNT(*) AS n,AVG(score) AS avg FROM cache')
        self.assertEqual(result['rows'],[[0,None]])

    def test_columnless_count_authorization(self):
        result=execute_cache_sql(self.cache,'SELECT COUNT(*) AS answer FROM cache')
        self.assertEqual(result['rows'],[[3]])
        self.assertEqual(result['denied_operations'],[])

    def test_write_attach_pragma_and_extension_denied(self):
        original=copy.deepcopy(self.cache)
        for sql in ('DELETE FROM cache','INSERT INTO cache VALUES(4,3)',
                    'DROP TABLE cache',"ATTACH DATABASE ':memory:' AS other",
                    'PRAGMA query_only=OFF',"SELECT load_extension('unavailable')",
                    'VACUUM',"SELECT readfile('unavailable')",'SELECT * FROM sqlite_master',
                    'SELECT rowid FROM cache','SELECT * FROM items'):
            with self.subTest(sql=sql):
                result=execute_cache_sql(self.cache,sql)
                self.assertNotEqual(result['status'],'EXECUTED')
                self.assertIsNone(result['rows'])
        self.assertEqual(original,self.cache)

    def test_multiple_statements_rejected(self):
        self.assertNotEqual(execute_cache_sql(self.cache,'SELECT id FROM cache; DELETE FROM cache')['status'],'EXECUTED')

    def test_bounded_recursive_computation(self):
        sql='WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT SUM(n) FROM x'
        result=execute_cache_sql(self.cache,sql,Limits(vm_steps=1000))
        self.assertEqual(result['status'],'RESOURCE_LIMIT')

    def test_rows_never_silently_truncated(self):
        sql='SELECT a.id,b.id FROM cache a CROSS JOIN cache b'
        result=execute_cache_sql(self.cache,sql,Limits(rows=3))
        self.assertEqual(result['status'],'RESOURCE_LIMIT')
        self.assertIsNone(result['rows'])

    def test_large_function_and_nondeterminism_denied(self):
        for sql in ('SELECT randomblob(1000000000)','SELECT random()','SELECT zeroblob(1000000000)',
                    "SELECT datetime('now')"):
            self.assertEqual(execute_cache_sql(self.cache,sql)['status'],'REJECTED')

    def test_schema_and_input_validation(self):
        for cache in ({'columns':['x','X'],'rows':[]},{'columns':['x'],'rows':[[float('inf')]]},
                      {'columns':['x'],'rows':[[2**100]]},{'columns':['x'],'rows':[[1,2]]}):
            self.assertEqual(execute_cache_sql(cache,'SELECT * FROM cache')['status'],'REJECTED')
        self.assertEqual(execute_cache_sql(self.cache,'SELECT 1\x00')['status'],'REJECTED')

    def test_semicolon_in_literal_allowed(self):
        result=execute_cache_sql(self.cache,"SELECT ';' AS answer")
        self.assertEqual(result['rows'],[[';']])

    def test_alias_null_and_duplicate_rows_are_distinct_checks(self):
        actual={'status':'EXECUTED','columns':['x'],'rows':[[1],[None],[1]]}
        self.assertTrue(compare_answer(actual,{'columns':['x'],'rows':[[None],[1],[1]]}))
        self.assertFalse(compare_answer(actual,{'columns':['y'],'rows':[[None],[1],[1]]}))
        self.assertFalse(compare_answer(actual,{'columns':['x'],'rows':[[None],[1],[2]]}))

    def test_parse_does_not_repair_model_output(self):
        def raw(content,finish='stop'):return {'choices':[{'finish_reason':finish,'message':{'content':content}}]}
        self.assertTrue(parse_transform(raw(json.dumps({'sql':'SELECT 1'})))['valid'])
        refusal=parse_transform(raw('{"sql":null}'))
        self.assertTrue(refusal['valid'])
        self.assertTrue(refusal['abstained'])
        for value in (raw('```json\n{"sql":"SELECT 1"}\n```'),raw('{}'),
                      raw('{"sql":"SELECT 1","extra":true}'),raw('{"sql":"SELECT 1"}','length')):
            self.assertFalse(parse_transform(value)['valid'])

    def test_large_bag_matching_has_no_recursion_failure(self):
        actual={'status':'EXECUTED','columns':['x'],'rows':[[1]]*1000}
        self.assertTrue(compare_answer(actual,{'columns':['x'],'rows':[[1]]*1000}))

    def test_numeric_tolerance_matching_is_not_greedy(self):
        actual={'status':'EXECUTED','columns':['x'],'rows':[[0.6e-12],[0.0]]}
        self.assertTrue(compare_answer(actual,{'columns':['x'],'rows':[[0.0],[1.5e-12]]}))

    def test_all_predefined_positive_transforms_pass_gate(self):
        # Programmatic fixtures only: this is NOT model-generated E3 evidence.
        for split in ('development','confirmation_candidate'):
            for case in build_suite(split):
                if not case['audit']['sufficient']:continue
                with self.subTest(case=case['case_id']):
                    result=execute_cache_sql(case['public']['cache'],case['audit']['positive_transform_sql'])
                    self.assertEqual(result['status'],'EXECUTED')
                    self.assertTrue(compare_answer(result,case['audit']['current_result']))


if __name__=='__main__':unittest.main()
