"""Software fixtures only, not experimental responses."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime.e1 import Client,digest,save
from runtime.dependency_probe import sources
from runtime.routing_probe import SYSTEM,oracle
from runtime.comprehension_probe import grid,prompt,run_cell,validate
from runtime.comprehension_score import summarize

def design():return json.loads((Path(__file__).resolve().parents[1]/'configs/comprehension_probe_v1.json').read_text())

class ComprehensionTests(unittest.TestCase):
    def test_grid_prompts_and_oracle(self):
        d=design();validate(d);self.assertEqual(len(grid(d)),9)
        for r,t in grid(d):
            probs=[]
            for value in (0,1):
                a=prompt(r,t,value,'layered','current');b=prompt(r,t,value,'effective','current')
                self.assertEqual(a[0]['content'],SYSTEM)
                obs=json.loads(a[1]['content'])['observations'][0]
                eff=json.loads(b[1]['content'])['observations'][0]['effective_reliability']
                self.assertAlmostEqual(oracle([obs]),eff if value else 1-eff)
                self.assertEqual(set(json.loads(a[1]['content'])),{'observations'})
                probs.append(oracle([obs]))
                self.assertEqual(a[1],prompt(r,t,value,'layered','explicit')[1])
            self.assertAlmostEqual(sum(probs),1)
        d['repetitions']=4
        with self.assertRaises(ValueError):validate(d)

    def test_audit_fixture_and_failure(self):
        d=design();cfg={'deployment':'native','model':'TEST_ONLY','model_path':'/fake',
            'served_model_metadata':{'root':'/fake'},'server_settings_reported':{'max_model_len':4096},
            **{k:d[k] for k in ('temperature','top_p','max_tokens')}}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',{'design':d,'config':cfg,'source_bundle':bundle,'source_sha256':digest(bundle)})
            save(dr/'server_metadata.json',{'data':[{'id':'TEST_ONLY','root':'/fake','max_model_len':4096}]})
            raw={'model':'TEST_ONLY','choices':[{'finish_reason':'stop','message':{'content':json.dumps({'answer':0,'p_state_1':.8,'citations':[]})}}],
                'usage':{'prompt_tokens':10,'completion_tokens':5}}
            with patch('urllib.request.urlopen') as net:
                net.return_value.__enter__.return_value.read.return_value=json.dumps(raw).encode()
                c=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for cell in range(9):run_cell(c,d,cell,dr)
                self.assertEqual(net.call_count,216)
            s,r,h=summarize(root)
            self.assertEqual(len(r),216);self.assertEqual(s['arms']['layered_current']['inconsistent'],1)
            self.assertEqual(s['arms']['layered_current']['direction_correct'],.5)
            self.assertAlmostEqual(s['mean_complement_symmetry_error']['layered_current'],.6)
            target=dr/'i0-v0-layered-current-r0.json';rec=json.loads(target.read_text());rec['status']='pending';save(target,rec)
            with self.assertRaises(ValueError):summarize(root)

if __name__=='__main__':unittest.main()
