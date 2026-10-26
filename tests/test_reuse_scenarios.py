import copy
import sqlite3
import random
import unittest

from runtime.reuse_scenarios import (DEVELOPMENT,CONFIRMATION,build_pair,build_suite,template_spec,
    connect_world,execute_sql,execute_transform,python_oracle,same_rows)
from scripts.prepare_reuse_suite import calcite_input


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dev=build_suite('development')
        cls.held=build_suite('confirmation_candidate')

    def test_fixed_scope_unique_public_inputs_and_splits(self):
        self.assertEqual((len(self.dev),len(self.held)),(48,192))
        self.assertEqual(len({c['base_id'] for c in self.dev}),24)
        self.assertEqual(len({c['base_id'] for c in self.held}),96)
        self.assertEqual(len({c['family'] for c in self.dev}),6)
        self.assertEqual(len({c['template'] for c in self.dev}),12)
        self.assertEqual(len({c['template'] for c in self.held}),12)
        self.assertFalse({c['template'] for c in self.dev}&{c['template'] for c in self.held})
        self.assertEqual(len({c['public_sha256'] for c in self.dev+self.held}),240)

    def test_all_witnesses_and_positive_transforms(self):
        for c in self.dev+self.held:
            with self.subTest(c['case_id']):
                audit=c['audit']
                self.assertTrue(audit['python_oracle_verified'])
                if audit['sufficient']:
                    self.assertTrue(audit['positive_transform_checked_on_both_worlds'])
                    self.assertEqual(execute_transform(c['public']['cache'],audit['positive_transform_sql'])['columns'],
                                     audit['current_result']['columns'])
                else:
                    self.assertTrue(audit['witness_cache_equal'])
                    self.assertFalse(same_rows(audit['current_result']['rows'],audit['other_current_result']['rows']))

    def test_empty_world_positive_transform_for_every_template(self):
        for families in (DEVELOPMENT,CONFIRMATION):
            for family,names in families.items():
                for name in names:
                    with self.subTest(name):
                        spec=template_spec(name,0)
                        empty={table:[] for table in spec['world']}
                        con=connect_world(spec,empty)
                        try:
                            expected=execute_sql(con,spec['current_sql'])
                            self.assertTrue(same_rows(expected['rows'],python_oracle(spec,empty)))
                            cached=execute_sql(con,spec['sufficient_sql'])
                            actual=execute_transform(cached,spec['transform_sql'])
                            self.assertEqual(actual['columns'],expected['columns'])
                            self.assertTrue(same_rows(actual['rows'],expected['rows']))
                        finally: con.close()

    def test_all_null_scores_do_not_become_zero_means(self):
        for families in (DEVELOPMENT,CONFIRMATION):
            for family,names in families.items():
                for name in names:
                    spec=template_spec(name,0)
                    if 'items' not in spec['world']: continue
                    nulls=copy.deepcopy(spec['world'])
                    for row in nulls['items']: row[4]=None
                    con=connect_world(spec,nulls)
                    try:
                        expected=execute_sql(con,spec['current_sql'])
                        self.assertTrue(same_rows(expected['rows'],python_oracle(spec,nulls)),name)
                        transformed=execute_transform(execute_sql(con,spec['sufficient_sql']),spec['transform_sql'])
                        self.assertTrue(same_rows(transformed['rows'],expected['rows']),name)
                    finally: con.close()

    def test_calcite_adapter_has_no_original_rows_or_gold(self):
        for c in self.dev+self.held:
            public=calcite_input(c)
            self.assertNotIn('world',public)
            self.assertNotIn('audit',public)
            self.assertNotIn('sufficient',public)
            self.assertNotIn('transform_sql',public)
            for table in public['tables']:
                self.assertNotIn('rows',table)
            self.assertEqual(public['cache'],c['public']['cache'])
        composite=next(c for c in self.held if c['template']=='entity_composite')
        self.assertEqual(calcite_input(composite)['tables'][0]['primary_key'],['k1','k2'])

    def test_foreign_key_violation_is_not_a_legal_witness(self):
        spec=template_spec('join_antijoin',0)
        bad=copy.deepcopy(spec['world'])
        bad['attendance'].append([999,1])
        with self.assertRaises(sqlite3.IntegrityError): connect_world(spec,bad)

    def test_query_only_disallows_updates(self):
        spec=template_spec('entity_global',0)
        con=connect_world(spec,spec['world'])
        try:
            with self.assertRaises(sqlite3.OperationalError): con.execute('DELETE FROM items')
        finally: con.close()

    def test_nullable_aggregation_oracle_is_not_sql_based(self):
        spec=template_spec('avg_weighted_rollup',0)
        spec['current_sql']='THIS IS DELIBERATELY NOT SQL'
        self.assertEqual(python_oracle(spec,spec['world']),[[3.0]])
        self.assertTrue(same_rows(python_oracle(spec,spec['other']),[[7/3]]))

    def test_positive_rules_on_768_additional_fixed_random_worlds(self):
        rng=random.Random(2026102701)
        checked=0
        for family_set in (DEVELOPMENT,CONFIRMATION):
            for names in family_set.values():
                for name in names:
                    spec=template_spec(name,0)
                    for _ in range(32):
                        if 'items' in spec['world']:
                            world={'items':[[i,rng.choice(['A','B']),rng.choice(['A','B','Gold','Blue']),
                                rng.choice([2025,2026,2027,2028]),rng.choice([None,-2,0,1,5]),
                                rng.choice([0,1])] for i in range(rng.randrange(8))]}
                        elif 'pairs' in spec['world']:
                            world={'pairs':[[i//2,i%2,rng.choice(['A','B'])] for i in range(rng.randrange(8))]}
                        else:
                            members=[[i,rng.choice([-2,0,1,5])] for i in range(rng.randrange(6))]
                            events=[[i,rng.choice(['X','Y','Z'])] for i in range(rng.randrange(5))]
                            edges=[[e[0],m[0]] for e in events for m in members if rng.random()<.3]
                            world={'member':members,'event':events,'attendance':edges}
                        con=connect_world(spec,world)
                        try:
                            expected=execute_sql(con,spec['current_sql'])
                            self.assertTrue(same_rows(expected['rows'],python_oracle(spec,world)),name)
                            cached=execute_sql(con,spec['sufficient_sql'])
                            actual=execute_transform(cached,spec['transform_sql'])
                            self.assertEqual(actual['columns'],expected['columns'],name)
                            self.assertTrue(same_rows(actual['rows'],expected['rows']),name)
                            checked+=1
                        finally: con.close()
        self.assertEqual(checked,768)


if __name__=='__main__':unittest.main()
