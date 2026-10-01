"""SOFTWARE_FIXTURE_ONLY: tests are not empirical model results."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime import prompt_id_bridge as bridge
from runtime import origin_representation as origin
from runtime.dependency_probe import sources
from runtime.e1 import canonical, digest, save
from runtime.format_probe_v2 import Client

class PromptIdBridgeTests(unittest.TestCase):
    def test_factorial_invariants(self):
        d = bridge.DESIGN
        old = {tuple(c['reliabilities']) for c in origin.tasks(origin.DESIGN)}
        for i, cell in enumerate(bridge.tasks(d)):
            self.assertNotIn(tuple(cell['reliabilities']), old)
            self.assertEqual(len(set(bridge.schedule(d,i))),32)
            for rel in bridge.RELATIONS:
                for flip in (0,1):
                    for stage in bridge.STAGES:
                        for naming in bridge.IDS:
                            a = bridge.trial(d,i,rel,flip,'legacy',naming,stage)
                            b = bridge.trial(d,i,rel,flip,'current',naming,stage)
                            self.assertEqual(a[0][1],b[0][1])
                            self.assertNotEqual(a[0][0],b[0][0])
                        a = bridge.trial(d,i,rel,flip,'legacy','sequential',stage)
                        b = bridge.trial(d,i,rel,flip,'legacy','opaque',stage)
                        self.assertEqual(a[0][0],b[0][0])
                        self.assertEqual([(o['value'],o['effective_reliability']) for o in a[1]],[(o['value'],o['effective_reliability']) for o in b[1]])
                        self.assertEqual([a[2][o['id']]==a[2][p['id']] for o in a[1] for p in a[1]], [b[2][o['id']]==b[2][p['id']] for o in b[1] for p in b[1]])
                        if stage=='final':
                            ta,tb=[json.loads(t[0][1]['content'])['external_calculator'] for t in (a,b)]
                            self.assertEqual(ta['p_state_1'],tb['p_state_1'])

    def test_fixture_replay_and_tampering(self):
        d = bridge.DESIGN
        cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096},**{k:d[k] for k in ('temperature','top_p','max_tokens')})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=d,config=cfg,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    payload=json.loads(json.loads(req.data)['messages'][1]['content'])
                    obs,roots=origin.decode(payload)
                    if 'external_calculator' not in payload:
                        seen=set();ids=[]
                        for o in obs:
                            if roots[o['id']] not in seen:ids.append(o['id']);seen.add(roots[o['id']])
                        obj=dict(selected_message_ids=ids)
                    else:
                        p=payload['external_calculator']['p_state_1']
                        obj=dict(answer=int(p>.5),p_state_1=p,citations=[o['id'] for o in obs])
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=canonical(obj)))],usage=dict(prompt_tokens=10,completion_tokens=20))
                    net.return_value.__enter__.return_value.read.return_value=canonical(raw).encode()
                    return net.return_value
                net.side_effect=respond
                client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for i in range(12):bridge.run_cell(client,d,i,dr)
                self.assertEqual(net.call_count,384)
            s,rows,p,h=bridge.summarize(root)
            self.assertEqual(s['invalid_calls'],0)
            self.assertEqual(s['calls'],384)
            self.assertTrue(all(c['complete_mean']==0 for c in s['primary_copy_contrasts'].values()))
            self.assertEqual(s['groups']['copy:legacy:sequential:select']['correct_origins'],24)
            path=dr/'result-0.json';obj=json.loads(path.read_text());obj['task']['regime']='changed';save(path,obj)
            with self.assertRaises(ValueError):bridge.summarize(root)

if __name__=='__main__':unittest.main()
