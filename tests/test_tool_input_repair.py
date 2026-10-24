"""SOFTWARE_FIXTURE_ONLY. Mocked responses are never experimental evidence."""
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime import tool_input_repair as m
from runtime import origin_representation as origin
from runtime.dependency_probe import sources
from runtime.e1 import canonical,digest,save
from runtime.format_probe_v2 import Client

def ref(obs):
    a=math.prod(o['effective_reliability'] if o['value'] else 1-o['effective_reliability'] for o in obs)
    b=math.prod(1-o['effective_reliability'] if o['value'] else o['effective_reliability'] for o in obs)
    return a/(a+b)

class RepairTests(unittest.TestCase):
    def test_intervention_and_no_report_answer_leak(self):
        for i in range(12):
            self.assertEqual(len(set(m.schedule(m.DESIGN,i))),32)
            for flip in (0,1):
                for relation,condition in m.CASES:
                    trials={a:m.trial(m.DESIGN,i,flip,relation,condition,a) for a in m.ARMS}
                    ps={a:json.loads(t[0][1]['content']) for a,t in trials.items()}
                    self.assertEqual(ps['original'],ps['explicit_check'])
                    self.assertEqual(trials['explicit_check'][0][0],trials['report'][0][0])
                    self.assertEqual(trials['report'][0][0],trials['repair'][0][0])
                    self.assertEqual(ps['report']['input_audit'],ps['repair']['input_audit'])
                    report=ps['report'].pop('input_audit')
                    self.assertEqual(set(report),{'valid','duplicate_origins','missing_origins','scope'})
                    self.assertEqual(ps['report'],ps['original'])
                    self.assertEqual(report['valid'],condition=='valid')
                    repaired=ps['repair']['external_calculator']
                    self.assertAlmostEqual(repaired['p_state_1'],ref(trials['repair'][1]),places=14)
                    for a in m.ARMS:
                        tool=json.loads(trials[a][0][1]['content'])['external_calculator']
                        self.assertAlmostEqual(tool['p_state_1'],ref(tool['input_observations']),places=14)
                    if condition=='valid':self.assertEqual(repaired,ps['original']['external_calculator'])

    def test_complements_and_fresh_cells(self):
        m.tasks(m.DESIGN)
        for i in range(12):
            for r,c in m.CASES:
                a=m.trial(m.DESIGN,i,0,r,c,'original')[1]
                b=m.trial(m.DESIGN,i,1,r,c,'original')[1]
                self.assertAlmostEqual(ref(a)+ref(b),1,places=14)

    def test_fixture_replay_failure_gate_and_tampering(self):
        d=m.DESIGN
        cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096},**{k:d[k] for k in ('temperature','top_p','max_tokens')})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=d,engineering_gate=m.GATE,config=cfg,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    payload=json.loads(json.loads(req.data)['messages'][1]['content'])
                    obs,roots=origin.decode(payload);seen=set();unique=[]
                    for o in obs:
                        if roots[o['id']] not in seen:unique.append(o);seen.add(roots[o['id']])
                    p=ref(unique);obj=dict(answer=int(p>.5),p_state_1=p,citations=[o['id'] for o in obs])
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=canonical(obj)))],usage=dict(prompt_tokens=10,completion_tokens=20))
                    net.return_value.__enter__.return_value.read.return_value=canonical(raw).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for i in range(12):m.run_cell(client,d,i,dr)
                self.assertEqual(net.call_count,384)
            s,rows,contrasts,hashes=m.summarize(root)
            self.assertTrue(s['engineering_gate']['passed'])
            self.assertTrue(all(g['correct_posterior_all_calls']==24 for g in s['groups'].values()))
            self.assertTrue(all(abs(c['complete_mean'])<1e-14 for c in contrasts.values()))
            file=dr/'result-0.json';obj=json.loads(file.read_text());obj['task']['regime']='tampered';save(file,obj)
            with self.assertRaises(ValueError):m.summarize(root)

if __name__=='__main__':unittest.main()
