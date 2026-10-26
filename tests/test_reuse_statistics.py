import unittest
from runtime.reuse_statistics import sensitivity,paired_price_rows,clustered_difference


class Tests(unittest.TestCase):
    def test_sensitivity_is_assumption_grid(self):
        values=sensitivity()['rows']
        self.assertEqual(len(values),64)
        selected=next(r for r in values if r['bases']==96 and r['assumed_within_template_rho']==.25
                      and r['assumed_paired_discordance']==.25)
        self.assertAlmostEqual(selected['effective_n_approx'],96/2.75)
        self.assertAlmostEqual(selected['normal_95_halfwidth_at_zero_difference'],.1659,places=3)

    def fixture(self):
        return [{'base_id':'b','template':'t','family':'f','method':'M1','sufficient':False,
                 'price':p,'admissibility':a} for p,a in ((1,'applicable'),(20,'unknown'))]

    def test_unresolved_retained_in_conservative_bounds(self):
        row=paired_price_rows(self.fixture(),'M1',['b'])[0]
        self.assertEqual(row['value'],1)
        self.assertEqual((row['conservative_difference_min'],row['conservative_difference_max']),(0,1))
        self.assertEqual(row['high_unresolved'],1)

    def test_incomplete_and_duplicate_pairs_fail(self):
        with self.assertRaises(ValueError):paired_price_rows(self.fixture()[:1],'M1',['b'])
        with self.assertRaises(ValueError):paired_price_rows(self.fixture()+self.fixture(),'M1',['b'])
        with self.assertRaises(ValueError):paired_price_rows(self.fixture(),'M1',['b','absent'])
        with self.assertRaises(ValueError):paired_price_rows([],'M1',['b'])
        with self.assertRaises(ValueError):paired_price_rows(self.fixture(),'M1',[])
        missing=[dict(r,admissibility='missing') for r in self.fixture()]
        row=paired_price_rows(missing,'M1',['b'])[0]
        self.assertEqual((row['conservative_difference_min'],row['conservative_difference_max']),(-1,1))

    def test_cluster_weights_not_call_weights(self):
        rows=[{'base_id':'a'+str(i),'template':'A','family':'F','value':1} for i in range(8)]
        rows += [{'base_id':'b','template':'B','family':'F','value':-1}]
        result=clustered_difference(rows,reps=100)
        self.assertEqual(result['weighted_difference'],0)
        self.assertEqual(result['template_clusters'],2)
        self.assertEqual(result,clustered_difference(rows,reps=100))
        with self.assertRaises(ValueError):clustered_difference(rows+rows,reps=10)

    def test_zero_band_is_explicitly_not_proof_of_zero_risk(self):
        rows=[{'base_id':str(i),'template':str(i//2),'family':'F','value':0} for i in range(8)]
        result=clustered_difference(rows,reps=100)
        self.assertTrue(result['degenerate_band'])
        self.assertIn('does NOT',result['claim_limit'])


if __name__=='__main__':unittest.main()
