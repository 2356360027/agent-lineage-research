"""Fixture-only tests for order/ID intervention; no empirical model data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from functools import partial
from runtime.dependency_probe import sources
from runtime.e1 import digest,save
from runtime.format_probe_v2 import Client
from runtime.order_probe import prompt,tasks,validate,run_cell,summarize
from runtime.readiness_pipeline import evaluate,oracle


def design():return json.loads((Path(__file__).resolve().parents[1]/'configs/order_probe_v1.json').read_text())


class OrderTests(unittest.TestCase):
    def test_invariants(self):
        d=design();validate(d);self.assertEqual(len(tasks(d)),6)
        for cell in tasks(d):
            for flip in (0,1):
                base=prompt(cell,flip,0,0);obs=json.loads(base[1]['content'])['observations'];q=oracle(obs)
                for reverse in (0,1):
                    for ids in (0,1):
                        p=prompt(cell,flip,reverse,ids);after=json.loads(p[1]['content'])['observations']
                        self.assertEqual(base[0],p[0]);self.assertAlmostEqual(oracle(after),q)
                        self.assertEqual(sorted((o['value'],o['effective_reliability']) for o in obs),sorted((o['value'],o['effective_reliability']) for o in after))
                        self.assertEqual({o['id'] for o in after},{'O0','O1'})
        d['seed']+=1
        with self.assertRaises(ValueError):validate(d)

    def test_audit_with_deliberate_last_item_fixture(self):
        d=design();cfg={'deployment':'native','model':'SOFTWARE_FIXTURE_ONLY','model_path':'/fake',
            'served_model_metadata':{'root':'/fake'},'server_settings_reported':{'max_model_len':4096},
            **{k:d[k] for k in ('temperature','top_p','max_tokens')}}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',{'design':d,'config':cfg,'source_bundle':bundle,'source_sha256':digest(bundle)})
            save(dr/'server_metadata.json',{'data':[{'id':cfg['model'],'root':'/fake','max_model_len':4096}]})
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    payload=json.loads(req.data);obs=json.loads(payload['messages'][1]['content'])['observations'];last=obs[-1]
                    obj={'answer':last['value'],'p_state_1':.75 if last['value'] else .25,'citations':[last['id']]}
                    raw={'model':cfg['model'],'choices':[{'finish_reason':'stop','message':{'content':json.dumps(obj)}}],
                         'usage':{'prompt_tokens':10,'completion_tokens':5}}
                    net.return_value.__enter__.return_value.read.return_value=json.dumps(raw).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr,partial(evaluate,visible={'O0','O1'}))
                for i in range(6):run_cell(client,d,i,dr)
                self.assertEqual(net.call_count,144)
            s,r,c,h=summarize(root)
            self.assertEqual(s['overall']['follows_last'],1)
            self.assertEqual(s['primary_order_contrasts']['stronger_last_minus_first_direction'],1)
            self.assertEqual(s['primary_order_contrasts']['absolute_order_probability_delta'],.5)
            self.assertEqual(s['secondary_id_contrast']['absolute_id_probability_delta'],0)
            target=dr/'i0-f0-o0-id0-r0.json';rec=json.loads(target.read_text());rec['status']='pending';save(target,rec)
            with self.assertRaises(ValueError):summarize(root)


if __name__=='__main__':unittest.main()
