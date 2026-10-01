"""Offline fixtures only; deliberately inconsistent toy outputs are not data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime.e1 import Client,digest,save
from runtime.dependency_probe import sources
from runtime.timing_probe import run_item,validate
from runtime.timing_score import summarize

def design():
    d=json.loads((Path(__file__).resolve().parents[1]/'configs/timing_probe_v1.json').read_text())
    d.update(items=2,bootstrap_resamples=100);return d

class Recorder:
    def __init__(self):self.calls=[]
    def call(self,key,p,s,v):
        self.calls.append((key,p,s,v))
        return {'answer':0,'p_state_1':len(self.calls)/100,'citations':[]}

class TimingTests(unittest.TestCase):
    def test_graph_and_evidence(self):
        c=Recorder();r=run_item(c,design(),1);self.assertEqual(len(c.calls),17)
        calls={x[0]:x for x in c.calls}
        for regime in ('new','repeat'):
            early=r['regimes'][regime]['early']
            for arm in ('open_zero','open_one','gate_zero','gate_one','evidence'):
                payload=json.loads(calls[f'i1-{regime}-final-{arm}'][1][1]['content'])
                self.assertEqual(set(payload),{'observations','your_previous_assessment','peer_assessments'})
                self.assertEqual(payload['your_previous_assessment'],early[arm] if arm.startswith('open') else early['hidden'])
                self.assertEqual(len(payload['observations']),3)
                self.assertEqual(len({o['id'] for o in payload['observations']}),3 if regime=='new' else 1)
                self.assertEqual(len(payload['peer_assessments']),0 if arm=='evidence' else 2)
            for verdict in ('zero','one'):
                a=json.loads(calls[f'i1-{regime}-final-open_{verdict}'][1][1]['content'])
                b=json.loads(calls[f'i1-{regime}-final-gate_{verdict}'][1][1]['content'])
                self.assertEqual(a['observations'],b['observations'])
                self.assertEqual(a['peer_assessments'],b['peer_assessments'])
        d=design();d['items']=0
        with self.assertRaises(ValueError):validate(d)

    def test_full_audit_and_tampering(self):
        d=design();cfg={'deployment':'native','model':'TEST_ONLY','model_path':'/fake',
            'served_model_metadata':{'root':'/fake'},'server_settings_reported':{'max_model_len':4096},
            **{k:d[k] for k in ('temperature','top_p','max_tokens')}}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',{'design':d,'config':cfg,'source_bundle':bundle,'source_sha256':digest(bundle)})
            save(dr/'server_metadata.json',{'data':[{'id':'TEST_ONLY','root':'/fake','max_model_len':4096}]})
            raw={'model':'TEST_ONLY','choices':[{'finish_reason':'stop','message':{'content':json.dumps(
                {'answer':0,'p_state_1':.8,'citations':[]})}}],'usage':{'prompt_tokens':10,'completion_tokens':5}}
            with patch('urllib.request.urlopen') as net:
                net.return_value.__enter__.return_value.read.return_value=json.dumps(raw).encode()
                c=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for item in range(2):run_item(c,d,item,dr)
                self.assertEqual(net.call_count,34)
            s,r,h=summarize(root);self.assertEqual(s['calls'],34)
            self.assertEqual(s['primary_endpoints']['gate_minus_open_oracle_error']['mean'],0)
            self.assertEqual(s['primary_endpoints']['evidence_new_minus_repeat_brier']['mean'],0)
            self.assertEqual(s['arms']['new_gate_zero']['inconsistency'],1)
            target=dr/'i0-new-final-gate_zero.json';original=target.read_bytes()
            for k,v in [('status','pending'),('request_hash','tampered')]:
                rec=json.loads(original);rec[k]=v;save(target,rec)
                with self.assertRaises(ValueError):summarize(root)
                target.write_bytes(original)
            resultpath=dr/'result-0.json';rec=json.loads(resultpath.read_text());rec['regimes']['new']['final']['gate_zero']['p_state_1']=.1;save(resultpath,rec)
            with self.assertRaises(ValueError):summarize(root)

if __name__=='__main__':unittest.main()
