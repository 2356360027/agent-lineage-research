"""SOFTWARE FIXTURES ONLY, not experimental observations."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime.duplicate_control import DESIGN,ARMS,TREATMENTS,tasks,base_obs,messages,registry,run_cell,summarize
from runtime.scaffold_probe import calculate
from runtime.tool_relay import tasks as old_tasks
from runtime.dependency_probe import sources
from runtime.e1 import canonical,digest,save
from runtime.format_probe_v2 import Client

class DuplicateControlTests(unittest.TestCase):
    def test_registry(self):
        a=dict(id='O0',value=0,effective_reliability=.8);b=dict(a,id='O1')
        kept,receipts=registry([a,a,b]);self.assertEqual(kept,[a,b]);self.assertEqual(len(receipts),1)
        with self.assertRaises(ValueError):registry([a,dict(a,value=1)])

    def test_fresh_and_invariants(self):
        ts=tasks(DESIGN);old={tuple(c['reliabilities']) for c in old_tasks(dict(protocol='tool-relay-v1',seed=71405382))}
        self.assertEqual(len(ts),20)
        for i,c in enumerate(ts):
            self.assertNotIn(tuple(c['reliabilities']),old)
            for f in (0,1):
                obs=base_obs(c,i,f);tool=calculate(dict(format_valid=True,assessment=dict(observations=obs)))
                base=messages(c,i,f,tool,'base')[0]
                for arm in ARMS:
                    p,receipt=messages(c,i,f,tool,arm,10);payload=json.loads(p[1]['content'])
                    self.assertEqual(p[0],base[0]);self.assertEqual(payload['external_calculator'],tool)
                    if arm.startswith('dedup_'):self.assertEqual(p,base);self.assertEqual(len(receipt),1)
                    if arm.startswith('dup_'):self.assertEqual(registry(payload['observations'])[0] if arm.endswith('back') else sorted(registry(payload['observations'])[0],key=canonical),obs if arm.endswith('back') else sorted(obs,key=canonical))

    def test_full_audit_fixture(self):
        cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096},**{k:DESIGN[k] for k in ('temperature','top_p','max_tokens')})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=DESIGN,config=cfg,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    req=json.loads(req.data);p=json.loads(req['messages'][1]['content']);obs=p['observations']
                    if 'exactly one field:' in req['messages'][0]['content']:obj=dict(observations=obs)
                    else:
                        q=p['external_calculator']['p_state_1'];obj=dict(answer=int(q>.5),p_state_1=q,citations=['O0','O1'])
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=canonical(obj)))],usage=dict(prompt_tokens=100,completion_tokens=20))
                    net.return_value.__enter__.return_value.read.return_value=canonical(raw).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for i in range(20):run_cell(client,DESIGN,i,dr,count=lambda p:100)
                self.assertEqual(net.call_count,480)
            s,r,p,h=summarize(root);self.assertTrue(s['token_control_valid']);self.assertEqual(s['exact_extractions'],40)
            self.assertTrue(all(v['complete_cell_mean']==0 for v in s['contrasts'].values()))
            path=dr/'result-0.json';rec=json.loads(path.read_text());rec['cases']['0']['tool']['p_state_1']=.123;save(path,rec)
            with self.assertRaises(ValueError):summarize(root)

if __name__=='__main__':unittest.main()
