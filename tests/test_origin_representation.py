"""SOFTWARE_FIXTURE_ONLY; these test outputs are not model measurements."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime.origin_representation import DESIGN,FORMATS,RELATIONS,tasks,world,encode,decode,schedule,trial,run_cell,summarize
from runtime import lineage_utility as lu
from runtime.dependency_probe import sources
from runtime.e1 import canonical,digest,save
from runtime.format_probe_v2 import Client

class OriginRepresentationTests(unittest.TestCase):
    def test_lossless_encodings_and_pure(self):
        old={tuple(c['reliabilities']) for c in lu.tasks(lu.DESIGN)}
        for i,c in enumerate(tasks(DESIGN)):
            self.assertNotIn(tuple(c['reliabilities']),old)
            self.assertEqual(len(schedule(DESIGN,i)),34)
            self.assertEqual(len(set(schedule(DESIGN,i))),34)
            for rel in RELATIONS:
                obs,origin=world(DESIGN,i,0,rel)
                for rep in FORMATS:
                    self.assertEqual(decode(encode(obs,origin,rep)),(obs,origin))
                    pure=encode(obs,origin,rep,True);pobs,porigin=decode(pure)
                    self.assertEqual(pobs,[{'id':o['id']} for o in obs]);self.assertEqual(porigin,origin)
                    prompt,_,_,_=trial(DESIGN,i,rel,-1,'pure',rep)
                    self.assertNotIn('effective_reliability',prompt[1]['content']);self.assertNotIn('binary state',prompt[0]['content'])
                finals=[trial(DESIGN,i,rel,0,'final',r)[0] for r in FORMATS+('premerged',)]
                self.assertTrue(all(p[0]==finals[0][0] for p in finals))
                tools=[json.loads(p[1]['content'])['external_calculator'] for p in finals]
                self.assertTrue(all(t==tools[0] for t in tools))

    def test_full_audit_fixture(self):
        cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096},**{k:DESIGN[k] for k in ('temperature','top_p','max_tokens')})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=DESIGN,config=cfg,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    req=json.loads(req.data);payload=json.loads(req['messages'][1]['content']);obs,origin=decode(payload)
                    if 'selected_message_ids:' in req['messages'][0]['content']:
                        roots=set();ids=[]
                        for o in obs:
                            r=origin[o['id']]
                            if r not in roots:ids.append(o['id']);roots.add(r)
                        obj=dict(selected_message_ids=ids)
                    else:
                        q=payload['external_calculator']['p_state_1'];obj=dict(answer=int(q>.5),p_state_1=q,citations=[o['id'] for o in obs])
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=canonical(obj)))],usage=dict(prompt_tokens=10,completion_tokens=20))
                    net.return_value.__enter__.return_value.read.return_value=canonical(raw).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for i in range(12):run_cell(client,DESIGN,i,dr)
                self.assertEqual(net.call_count,408)
            s,rows,p,h=summarize(root);self.assertEqual(s['invalid_calls'],0)
            self.assertTrue(all(v['complete_mean']==0 for v in s['primary_copy_contrasts'].values()))
            self.assertEqual(s['groups']['copy:pure:map']['correct_origins_all_calls'],12)
            self.assertEqual(s['groups']['independent:numeric_select:grouped']['correct_origins_all_calls'],24)
            path=dr/'result-0.json';rec=json.loads(path.read_text());key=next(iter(rec['outputs']));rec['outputs'][key]['representation']='changed';save(path,rec)
            with self.assertRaises(ValueError):summarize(root)

if __name__=='__main__':unittest.main()
