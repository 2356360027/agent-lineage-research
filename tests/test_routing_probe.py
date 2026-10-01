"""CPU rule and audit fixtures; never experimental measurements."""
import copy
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime.e1 import Client,digest,save
from runtime.dependency_probe import sources
from runtime.routing_probe import batches,cpu_audit,ledger_route,oracle,run_item,unique,validate,world
from runtime.routing_score import summarize

def design():
    d=json.loads((Path(__file__).resolve().parents[1]/'configs/routing_probe_v1.json').read_text())
    d.update(items=2,bootstrap_resamples=100);return d

class Recorder:
    def __init__(self):self.calls=[]
    def call(self,key,p,s,v):
        self.calls.append((key,p,s,v));return {'answer':0,'p_state_1':.8,'citations':[]}

class RoutingTests(unittest.TestCase):
    def test_rules_and_equivalence(self):
        audit=cpu_audit();self.assertTrue(audit['recipient_ledger_equals_strong_ID_dedup'])
        self.assertEqual(audit['equivalence_checks'],1600)
        obs,_=world(55,3)
        self.assertEqual(ledger_route([obs[0]],[[obs[0],obs[1]]]),obs[:2])
        self.assertEqual(ledger_route([obs[2]],[[obs[0]]]),[obs[2],obs[0]])
        bad=copy.deepcopy(obs[0]);bad['value']=1-bad['value']
        with self.assertRaises(ValueError):unique([obs[0],bad])

    def test_oracle_exact_and_dependent(self):
        o={'id':'O0','source':'R0','value':1,'source_reliability':.8,'observation_reliability':.9}
        self.assertAlmostEqual(oracle([o]),.8*.9+.2*.1)
        self.assertEqual(oracle([o,o,o]),oracle([o]))
        o2=dict(o,id='O1')
        self.assertAlmostEqual(oracle([o,o2]),(.8*.9**2+.2*.1**2)/(.9**2+.1**2))
        independent=dict(o2,source='R1')
        self.assertGreater(oracle([o,independent]),oracle([o,o2]))
        # Independently enumerate both source states as a check on grouped formula.
        obs,_=world(501,5);weights=[]
        for y in (0,1):
            total=0.
            for zs in itertools.product((0,1),repeat=2):
                w=.5
                for s in range(2):
                    r=obs[2*s]['source_reliability'];w*=r if zs[s]==y else 1-r
                for k,v in enumerate(obs):
                    t=v['observation_reliability'];w*=t if v['value']==zs[k//2] else 1-t
                total+=w
            weights.append(total)
        self.assertAlmostEqual(oracle(obs),weights[1]/sum(weights))

    def test_call_graph_no_gold_and_stage_snapshot(self):
        c=Recorder();d=design();r=run_item(c,d,0)
        self.assertEqual(len(c.calls),50)
        self.assertEqual(len({x[0] for x in c.calls}),50)
        for key,p,s,v in c.calls:
            b=json.loads(p[1]['content'])
            self.assertTrue(set(b)<= {'observations','your_previous_assessment','peer_assessments'})
            if '-gate-' in key and '-s1' in key:self.assertEqual(b['peer_assessments'],[])
        # Earlier raw snapshots must not grow when history receives the next bundle.
        self.assertEqual(len(r['trajectories']['relay-raw-0'][0]['visible_observations']),2)
        self.assertEqual(len(r['trajectories']['relay-raw-0'][1]['visible_observations']),4)
        for case in ('relay','same_source','recipient','mixed'):
            for receiver in (0,1):
                a=r['trajectories'][f'{case}-raw-{receiver}'][-1]['visible_observations']
                for policy in ('dedup','gate'):
                    self.assertEqual(unique(a),r['trajectories'][f'{case}-{policy}-{receiver}'][-1]['visible_observations'])
        d['items']=0
        with self.assertRaises(ValueError):validate(d)

    def test_full_audit(self):
        d=design();cfg={'deployment':'native','model':'TEST_ONLY','model_path':'/fake',
            'served_model_metadata':{'root':'/fake'},'server_settings_reported':{'max_model_len':4096},
            **{k:d[k] for k in ('temperature','top_p','max_tokens')}}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',{'design':d,'config':cfg,'source_bundle':bundle,'source_sha256':digest(bundle),'cpu_audit':cpu_audit()})
            save(dr/'server_metadata.json',{'data':[{'id':'TEST_ONLY','root':'/fake','max_model_len':4096}]})
            raw={'model':'TEST_ONLY','choices':[{'finish_reason':'stop','message':{'content':json.dumps({'answer':0,'p_state_1':.8,'citations':[]})}}],
                'usage':{'prompt_tokens':10,'completion_tokens':5}}
            with patch('urllib.request.urlopen') as net:
                net.return_value.__enter__.return_value.read.return_value=json.dumps(raw).encode()
                c=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for item in range(2):run_item(c,d,item,dr)
                self.assertEqual(net.call_count,100)
            s,r,h=summarize(root);self.assertEqual(s['calls'],100)
            self.assertEqual(s['primary_endpoints']['dedup_minus_raw_oracle_error']['mean'],0)
            self.assertEqual(s['primary_endpoints']['dedup_same_source_minus_relay_brier']['mean'],0)
            target=dr/'i0-relay-dedup-r0-s1.json';rec=json.loads(target.read_text());rec['status']='pending';save(target,rec)
            with self.assertRaises(ValueError):summarize(root)

if __name__=='__main__':unittest.main()
