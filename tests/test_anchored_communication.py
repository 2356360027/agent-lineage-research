"""SOFTWARE_FIXTURE_ONLY. Mock calls are never model experiment results."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime import anchored_communication as m
from runtime import origin_representation as origin
from runtime.dependency_probe import sources
from runtime.e1 import canonical,digest,save
from runtime.format_probe_v2 import Client

def reference(obs):
    a=math.prod(o['effective_reliability'] if o['value'] else 1-o['effective_reliability'] for o in obs)
    b=math.prod(1-o['effective_reliability'] if o['value'] else o['effective_reliability'] for o in obs)
    return a/(a+b)

def fixture_initials():
    return [dict(format_valid=True,assessment=dict(answer=k%2,p_state_1=.3+.1*k,citations=[]),violation=None) for k in range(3)]

class AnchoredTests(unittest.TestCase):
    def test_evidence_calculator_and_peer_controls(self):
        m.tasks(m.DESIGN)
        for i in range(12):
            self.assertEqual(len(set(m.case_order(m.DESIGN,i))),4)
            for f,r in m.case_order(m.DESIGN,i):
                self.assertEqual(set(m.arm_order(m.DESIGN,i,f,r)),set(m.ARMS))
                initial=fixture_initials();payloads={};systems=[]
                for a in range(3):
                    prompt,obs=m.initial_prompt(m.DESIGN,i,f,r,a)
                    p=json.loads(prompt[1]['content'])
                    self.assertEqual(len(p['messages']),1)
                    self.assertEqual(p['external_calculator']['input_observations'],obs)
                    self.assertAlmostEqual(p['external_calculator']['p_state_1'],reference(obs),places=14)
                    self.assertNotIn('peer_assessments',p)
                for arm in m.ARMS:
                    prompt,unique=m.final_prompt(m.DESIGN,i,f,r,arm,initial)
                    payloads[arm]=json.loads(prompt[1]['content']);systems.append(prompt[0])
                    self.assertAlmostEqual(payloads[arm]['external_calculator']['p_state_1'],reference(unique),places=14)
                self.assertTrue(all(s==systems[0] for s in systems))
                peers={a:payloads[a].pop('peer_assessments') for a in m.ARMS}
                self.assertTrue(all(p==payloads['no_peer'] for p in payloads.values()))
                self.assertEqual(peers['no_peer'],[]);self.assertEqual(peers['no_peer_repeat'],[])
                self.assertEqual([p['answer'] for p in peers['natural']],[1,0])
                for p in peers['natural']:self.assertEqual(set(p),{'agent','available','answer'})
                self.assertEqual([p['answer'] for p in peers['injected_zero']],[0,0])
                self.assertEqual([p['answer'] for p in peers['injected_one']],[1,1])
                changed=copy.deepcopy(initial);changed[1]['assessment']['p_state_1']=.999
                self.assertEqual(m.final_prompt(m.DESIGN,i,f,r,'natural',initial),m.final_prompt(m.DESIGN,i,f,r,'natural',changed))
                self.assertNotEqual(m.call_seed(m.DESIGN,i,f,r,'repeat'),m.call_seed(m.DESIGN,i,f,r,'final'))

    def test_invalid_initial_is_unavailable_not_corrected(self):
        initial=fixture_initials();initial[1]=dict(format_valid=False,assessment=None,violation='fixture')
        prompt,_=m.final_prompt(m.DESIGN,0,0,'copy','natural',initial)
        p=json.loads(prompt[1]['content'])
        self.assertEqual(p['peer_assessments'][0],{'agent':'B','available':False})
        initial[0]=initial[1]
        p=json.loads(m.final_prompt(m.DESIGN,0,0,'copy','no_peer',initial)[0][1]['content'])
        self.assertEqual(p['your_previous_assessment'],{'available':False})

    def test_full_fixture_replay_preserves_invalid_and_tampering(self):
        d=m.DESIGN
        cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096},**{k:d[k] for k in ('temperature','top_p','max_tokens')})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=d,engineering_gate=m.GATE,decision_rule=m.DECISION,config=cfg,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            count=0
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    nonlocal count
                    count+=1;p=json.loads(json.loads(req.data)['messages'][1]['content'])
                    obs,roots=origin.decode(p);seen=set();unique=[]
                    for o in obs:
                        if roots[o['id']] not in seen:unique.append(o);seen.add(roots[o['id']])
                    prob=reference(unique);obj=dict(answer=int(prob>.5),p_state_1=prob,citations=[o['id'] for o in obs])
                    content='fixture invalid JSON' if count==1 else canonical(obj)
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=content))],usage=dict(prompt_tokens=10,completion_tokens=20))
                    net.return_value.__enter__.return_value.read.return_value=canonical(raw).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for i in range(12):m.run_cell(client,d,i,dr)
                self.assertEqual(net.call_count,384)
            s,rows,contrasts,hashes=m.summarize(root)
            self.assertEqual(s['initial_calls'],144);self.assertEqual(s['initial_invalid'],1)
            self.assertEqual(s['final_invalid'],0);self.assertFalse(s['engineering_gate']['passed'])
            self.assertEqual(s['decision'],'DISCUSS_BEFORE_MORE_INFERENCE')
            self.assertTrue(any(c['complete_mean'] is None for c in contrasts.values()))
            self.assertTrue(all(g['correct_posterior_all_calls']==24 for g in s['groups'].values()))
            file=dr/'result-0.json';obj=json.loads(file.read_text());obj['task']['regime']='tampered';save(file,obj)
            with self.assertRaises(ValueError):m.summarize(root)

if __name__=='__main__':unittest.main()
