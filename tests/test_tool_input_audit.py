"""SOFTWARE_FIXTURE_ONLY: mocked outputs never count as measured results."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime import tool_input_audit as module
from runtime import origin_representation as origin
from runtime import prompt_id_bridge as bridge
from runtime.dependency_probe import sources
from runtime.e1 import canonical,digest,save
from runtime.format_probe_v2 import Client

def reference(obs):
    a=math.prod(o['effective_reliability'] if o['value'] else 1-o['effective_reliability'] for o in obs)
    b=math.prod(1-o['effective_reliability'] if o['value'] else o['effective_reliability'] for o in obs)
    return a/(a+b)

class ToolInputAuditTests(unittest.TestCase):
    def test_matched_interventions_and_arithmetic(self):
        prior={tuple(c['reliabilities']) for c in origin.tasks(origin.DESIGN)+bridge.tasks(bridge.DESIGN)}
        for i,cell in enumerate(module.tasks(module.DESIGN)):
            self.assertNotIn(tuple(cell['reliabilities']),prior)
            self.assertEqual(len(set(module.schedule(module.DESIGN,i))),16)
            for flip in (0,1):
                payloads={}
                for rel,condition in module.CASES:
                    a=module.trial(module.DESIGN,i,flip,'legacy',rel,condition)
                    b=module.trial(module.DESIGN,i,flip,'current',rel,condition)
                    self.assertEqual(a[0][1],b[0][1])
                    prompt,obs,roots,unique,tool,audit=a
                    self.assertAlmostEqual(tool['p_state_1'],reference(tool['input_observations']),places=14)
                    self.assertAlmostEqual(audit['corrected_calculator']['p_state_1'],reference(unique),places=14)
                    self.assertEqual(audit['valid'],condition=='valid')
                    if condition!='valid':self.assertGreater(abs(tool['p_state_1']-reference(unique)),.01)
                    payload=json.loads(prompt[1]['content']);payload.pop('external_calculator')
                    self.assertNotIn('programmatic_audit',prompt[1]['content'])
                    if rel in payloads:self.assertEqual(payload,payloads[rel])
                    payloads[rel]=payload
                self.assertEqual(payloads['copy']['messages'],payloads['independent']['messages'])

    def test_validator_rejects_identity_conflicts(self):
        _,obs,roots,unique,tool,audit=module.trial(module.DESIGN,0,0,'legacy','copy','valid')
        bad=copy.deepcopy(unique);bad[0]['id']='unknown'
        with self.assertRaises(ValueError):module.validate_inputs(obs,roots,bad)
        bad=copy.deepcopy(unique);bad[0]['value']^=1
        with self.assertRaises(ValueError):module.validate_inputs(obs,roots,bad)
        bad=copy.deepcopy(obs);bad[-1]['value']^=1
        with self.assertRaises(ValueError):module.validate_inputs(bad,roots,unique)

    def test_fixture_replay_and_tampering(self):
        d=module.DESIGN
        cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096},**{k:d[k] for k in ('temperature','top_p','max_tokens')})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=d,config=cfg,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    payload=json.loads(json.loads(req.data)['messages'][1]['content'])
                    obs,roots=origin.decode(payload);seen=set();unique=[]
                    for o in obs:
                        if roots[o['id']] not in seen:unique.append(o);seen.add(roots[o['id']])
                    p=reference(unique);obj=dict(answer=int(p>.5),p_state_1=p,citations=[o['id'] for o in obs])
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=canonical(obj)))],usage=dict(prompt_tokens=10,completion_tokens=20))
                    net.return_value.__enter__.return_value.read.return_value=canonical(raw).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for i in range(12):module.run_cell(client,d,i,dr)
                self.assertEqual(net.call_count,192)
            summary,rows,pairs,hashes=module.summarize(root)
            self.assertEqual(summary['invalid_calls'],0)
            self.assertTrue(all(abs(c['complete_mean'])<1e-14 for c in summary['primary_descriptive_contrasts'].values()))
            self.assertTrue(all(g['posterior_correct_all_calls']==24 for g in summary['groups'].values()))
            self.assertEqual(summary['groups']['current:copy:duplicated']['wrong_tool_agreement_all_calls'],0)
            file=dr/'result-0.json';obj=json.loads(file.read_text());obj['task']['regime']='tampered';save(file,obj)
            with self.assertRaises(ValueError):module.summarize(root)

if __name__=='__main__':unittest.main()
