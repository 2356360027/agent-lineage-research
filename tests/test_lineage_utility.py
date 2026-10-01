"""SOFTWARE_FIXTURE_ONLY; synthetic API fixtures are not real results."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime.lineage_utility import DESIGN,POLICIES,RELATIONS,tasks,world,route,tool_for,messages,evaluate,run_cell,summarize
from runtime.duplicate_control import tasks as old_tasks,DESIGN as OLD
from runtime.dependency_probe import sources
from runtime.e1 import canonical,digest,save
from runtime.format_probe_v2 import Client

class LineageUtilityTests(unittest.TestCase):
    def test_world_and_router_invariants(self):
        ts=tasks(DESIGN);old={tuple(c['reliabilities']) for c in old_tasks(OLD)}
        for i,c in enumerate(ts):
            self.assertNotIn(tuple(c['reliabilities']),old)
            for f in (0,1):
                copy_obs,copy_map=world(c,i,f,'copy');ind_obs,ind_map=world(c,i,f,'independent')
                self.assertEqual(copy_obs,ind_obs);self.assertNotEqual(copy_map,ind_map)
                for rel in RELATIONS:
                    obs,orig=world(c,i,f,rel)
                    selected,_=route(obs,orig,'verified_root')
                    self.assertEqual(len(selected),2 if rel=='copy' else 3)
                    self.assertEqual(len(route(obs,orig,'transport_id')[0]),3)
                    self.assertEqual(len(route(obs,orig,'content_key')[0]),2)
                    q=tool_for(selected)['p_state_1']
                    other,om=world(c,i,1-f,rel)
                    self.assertAlmostEqual(q+tool_for(route(other,om,'verified_root')[0])['p_state_1'],1)
                bad=[dict(o) for o in copy_obs];bad[-1]['value']^=1
                with self.assertRaises(ValueError):route(bad,copy_map,'verified_root')

    def test_strict_parser(self):
        def raw(obj):return dict(model='test',choices=[dict(finish_reason='stop',message=dict(content=canonical(obj)))])
        self.assertTrue(evaluate(raw(dict(answer=1,p_state_1=.7,citations=['O2'])),'final','test',{'O2'})['format_valid'])
        self.assertFalse(evaluate(raw(dict(answer=1,p_state_1=.7,citations=['O2'])),'final','test',{'O0'})['format_valid'])
        self.assertFalse(evaluate(raw(dict(selected_message_ids=['O0','O0'])),'select','test',{'O0'})['format_valid'])
        self.assertTrue(evaluate(raw(dict(selected_message_ids=[])),'select','test',{'O0'})['format_valid'])
        self.assertEqual(tool_for([])['status'],'error')

    def test_full_fixture_audit(self):
        cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096},**{k:DESIGN[k] for k in ('temperature','top_p','max_tokens')})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=DESIGN,config=cfg,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    req=json.loads(req.data);payload=json.loads(req['messages'][1]['content']);obs=payload['observations']
                    if 'selected_message_ids:' in req['messages'][0]['content']:
                        selected,_=route(obs,payload['experiment_authenticated_origins'],'verified_root');obj=dict(selected_message_ids=[o['id'] for o in selected])
                    else:
                        q=payload['external_calculator']['p_state_1'];obj=dict(answer=int(q>.5),p_state_1=q,citations=[o['id'] for o in obs])
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=canonical(obj)))],usage=dict(prompt_tokens=10,completion_tokens=20))
                    net.return_value.__enter__.return_value.read.return_value=canonical(raw).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for i in range(20):run_cell(client,DESIGN,i,dr)
                self.assertEqual(net.call_count,480)
            s,r,p,h=summarize(root)
            self.assertEqual(s['selectors']['copy']['exact'],40);self.assertEqual(s['selectors']['independent']['exact'],40)
            self.assertTrue(all(v['complete_mean']==0 for v in s['primary_contrasts'].values()))
            self.assertGreater(s['groups']['copy:transport_id']['mae']['mean'],0)
            self.assertGreater(s['groups']['independent:content_key']['mae']['mean'],0)
            path=dr/'result-0.json';rec=json.loads(path.read_text());rec['cases']['0-copy']['origins']['O2']='R2';save(path,rec)
            with self.assertRaises(ValueError):summarize(root)

if __name__=='__main__':unittest.main()
