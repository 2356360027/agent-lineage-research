"""SOFTWARE_FIXTURE_ONLY: mocked outputs are not experiment evidence."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime import decision_interface as m
from runtime.dependency_probe import sources
from runtime.e1 import canonical,digest,save
from runtime.format_probe_v2 import Client


def initials():
    return [dict(format_valid=True,assessment=dict(answer=k%2,p_state_1=.2+k*.2,citations=[]),violation=None) for k in range(3)]


def reference(obs):
    a=math.prod(o['effective_reliability'] if o['value'] else 1-o['effective_reliability'] for o in obs)
    b=math.prod(1-o['effective_reliability'] if o['value'] else o['effective_reliability'] for o in obs)
    return a/(a+b)


class DecisionInterfaceTests(unittest.TestCase):
    def test_fresh_tasks_and_full_factorial_controls(self):
        m.tasks(m.DESIGN)
        for i in range(12):
            for f,r in m.prior.case_order(m.DESIGN,i):
                schedule=m.schedule(m.DESIGN,i,f,r);self.assertEqual(len(set(schedule)),8)
                base=None
                for rule,order,peer in schedule:
                    prompt,unique=m.final_prompt(m.DESIGN,i,f,r,rule,order,peer,initials())
                    payload=json.loads(prompt[1]['content']);peers=payload.pop('peer_assessments')
                    if base is None:base=payload
                    self.assertEqual(payload,base)
                    self.assertEqual([p['answer'] for p in peers],[] if peer=='no_peer' else [1,0])
                    self.assertEqual(m.RULE in prompt[0]['content'],rule=='explicit')
                    self.assertTrue(prompt[0]['content'].endswith(m.ORDER_TEXT[order]))
                    self.assertAlmostEqual(reference(unique),payload['external_calculator']['p_state_1'],places=14)
        out=initials();out[1]=dict(format_valid=False,assessment=None,violation='fixture')
        payload=json.loads(m.final_prompt(m.DESIGN,0,0,'copy','explicit','answer_first','natural',out)[0][1]['content'])
        self.assertEqual(payload['peer_assessments'][0],dict(agent='B',available=False))

    def test_order_is_retained_not_invalidated_and_tie_rule(self):
        raw=dict(model='fixture',choices=[dict(finish_reason='stop',message=dict(content='{"answer":0,"p_state_1":0.5,"citations":["a"]}'))])
        out=m.evaluate(raw,'final','fixture',{'a'},'probability_first')
        self.assertTrue(out['format_valid']);self.assertFalse(out['order_compliant'])
        before=copy.deepcopy(out)
        row=m.measured(out,[dict(id='a',value=1,effective_reliability=.5)])
        self.assertEqual(row['answer_probability_consistent'],1)
        self.assertEqual(out,before)

    def test_pairing_averages_cells_and_preserves_missing(self):
        rows=[]
        for i in range(12):
            for f in (0,1):
                for rule in m.RULES:
                    for order in m.ORDERS:
                        rows.append(dict(cell=i,flip=f,relation='copy',rule=rule,order=order,peer='no_peer',own_initial_valid=True,format_valid=True,mae=(i+1)*(.01 if rule=='explicit' else .02)))
        c=m.paired(rows,'copy','rule','mae')
        self.assertAlmostEqual(c['complete_mean'],-.065)
        rows[0]['format_valid']=False
        c=m.paired(rows,'copy','rule','mae')
        self.assertEqual(c['complete_cells'],11);self.assertIsNone(c['complete_mean'])

    def test_full_fixture_replay_invalid_retention_and_tamper(self):
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
                    count+=1;messages=json.loads(req.data)['messages'];p=json.loads(messages[1]['content'])
                    obs,roots=m.origin.decode(p);unique={}
                    for o in obs:unique.setdefault(roots[o['id']],o)
                    prob=reference(list(unique.values()))
                    obj=dict(answer=int(prob>.5),p_state_1=prob,citations=[o['id'] for o in obs])
                    # Intentionally always answer-first: noncompliance must survive.
                    content='fixture invalid' if count==1 else json.dumps(obj)
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=content))],usage=dict(prompt_tokens=10,completion_tokens=20))
                    net.return_value.__enter__.return_value.read.return_value=canonical(raw).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for i in range(12):m.run_cell(client,d,i,dr)
                self.assertEqual(net.call_count,528)
            s,rows,contrasts,hashes=m.summarize(root)
            self.assertEqual(s['initial_calls'],144);self.assertEqual(s['initial_invalid'],1)
            self.assertEqual(s['final_invalid'],0);self.assertFalse(s['engineering_gate']['passed'])
            self.assertEqual(s['decision'],'DISCUSS_BEFORE_MORE_INFERENCE')
            self.assertTrue(any(c['complete_mean'] is None for c in contrasts.values()))
            for key,g in s['groups'].items():
                self.assertEqual(g['correct_posterior_all_calls'],24)
                self.assertEqual(g['order_compliant_all_calls'],0 if 'probability_first' in key else 24)
            path=dr/'result-0.json';obj=json.loads(path.read_text());obj['task']['regime']='tampered';save(path,obj)
            with self.assertRaises(ValueError):m.summarize(root)


if __name__=='__main__':unittest.main()
