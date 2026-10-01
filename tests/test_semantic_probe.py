"""Software fixtures only: none of these generated outputs is research data."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime.e1 import Client, digest, save
from runtime.dependency_probe import sources
from runtime.semantic_probe import ARMS, CLARIFICATION, prompt, run_item, validate
from runtime.semantic_score import summarize

def design():
    d=json.loads((Path(__file__).resolve().parents[1]/'configs/semantic_probe_v1.json').read_text())
    d.update(items=2,repetitions=2,bootstrap_resamples=100)
    return d

class Recorder:
    def __init__(self):self.calls=[]
    def call(self,key,p,s,v):
        self.calls.append((key,p,s,v))
        return {'answer':0,'p_state_1':.8,'citations':[]} # Intentionally inconsistent: retain.

class SemanticTests(unittest.TestCase):
    def test_invariants(self):
        d=design();c=Recorder()
        with tempfile.TemporaryDirectory() as tmp:run_item(c,d,0,Path(tmp))
        self.assertEqual(len(c.calls),13)
        finals=c.calls[1:]
        payloads=[json.loads(x[1][1]['content']) for x in finals]
        self.assertTrue(all(set(p)=={'observations','your_previous_assessment','peer_assessments'} for p in payloads))
        self.assertEqual(len({digest(p['observations']) for p in payloads}),1)
        self.assertEqual(len({digest(p['your_previous_assessment']) for p in payloads}),1)
        for rep in range(2):
            self.assertEqual(len({x[2] for x in finals if f'-r{rep}-' in x[0]}),1)
        for v in ('hidden','zero','one'):
            a=prompt([],{},'legacy_'+v);b=prompt([],{},'explicit_'+v)
            self.assertEqual(a[1],b[1]);self.assertEqual(b[0]['content'],a[0]['content']+CLARIFICATION)

    def test_invalid_design(self):
        d=design();validate(d)
        for k,v in [('items',0),('world_seed','x'),('repetitions',1.5),('primary_endpoints',[])]:
            bad=copy.deepcopy(d);bad[k]=v
            with self.assertRaises(ValueError):validate(bad)

    def test_audit_and_no_repairs(self):
        d=design();cfg={'deployment':'native','model':'TEST_ONLY',
            'model_path':'/fake/model','served_model_metadata':{'root':'/fake/model'},
            'server_settings_reported':{'max_model_len':4096},
            **{k:d[k] for k in ('temperature','top_p','max_tokens')}}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'shard-000';directory.mkdir();bundle=sources()
            save(directory/'manifest.json',{'design':d,'config':cfg,'source_bundle':bundle,'source_sha256':digest(bundle)})
            save(directory/'server_metadata.json',{'data':[{'id':'TEST_ONLY','root':'/fake/model','max_model_len':4096}]})
            raw={'model':'TEST_ONLY','choices':[{'finish_reason':'stop','message':{'content':json.dumps(
                {'answer':0,'p_state_1':.8,'citations':[]})}}],'usage':{'prompt_tokens':10,'completion_tokens':5}}
            with patch('urllib.request.urlopen') as net:
                net.return_value.__enter__.return_value.read.return_value=json.dumps(raw).encode()
                client=Client(cfg,'http://127.0.0.1:8000/v1',directory)
                for item in range(d['items']):run_item(client,d,item,directory)
                self.assertEqual(net.call_count,26)
            report,rows,hashes=summarize(root)
            self.assertEqual(report['calls'],26);self.assertEqual(report['worlds'],2)
            self.assertEqual(report['prompt_tokens'],260)
            self.assertEqual(report['arms']['explicit_zero']['inconsistency'],1)
            self.assertEqual(report['arms']['explicit_zero']['probability'],.8)
            self.assertEqual(report['endpoints']['explicit_verdict_sensitivity']['mean'],0)
            target=directory/'i0-r0-legacy_zero.json';original=target.read_bytes()
            for key,value in [('status','pending'),('request_hash','bad')]:
                rec=json.loads(original);rec[key]=value;save(target,rec)
                with self.assertRaises(ValueError):summarize(root)
                target.write_bytes(original)
            (directory/'result-1.json').unlink()
            with self.assertRaises(ValueError):summarize(root)

if __name__=='__main__':unittest.main()
