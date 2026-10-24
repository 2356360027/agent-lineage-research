"""SOFTWARE_FIXTURE_ONLY: mocked results never count as empirical evidence."""
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime import interface_replication as m
from runtime.dependency_probe import sources
from runtime.e1 import canonical,digest,save
from runtime.format_probe_v2 import Client
from test_decision_interface import initials,reference


class InterfaceReplicationTests(unittest.TestCase):
    def test_fresh_cells_exact_templates_and_four_arms(self):
        cells=m.tasks(m.DESIGN)
        self.assertEqual(len(cells),24)
        for i in range(24):
            bd,bi=m.block(m.DESIGN,i)
            self.assertEqual(cells[i],m.previous.prior.tasks(bd)[bi])
            for f,r in m.previous.prior.case_order(m.DESIGN,i):
                arms=m.schedule(m.DESIGN,i,f,r)
                self.assertEqual(set(arms),set(itertools.product(m.ORDERS,m.PEERS)))
                baseline=None
                for order,peer in arms:
                    p,obs=m.final_prompt(m.DESIGN,i,f,r,order,peer,initials())
                    self.assertEqual((p,obs),m.previous.final_prompt(bd,bi,f,r,'explicit',order,peer,initials()))
                    data=json.loads(p[1]['content']);peers=data.pop('peer_assessments')
                    if baseline is None:baseline=data
                    self.assertEqual(data,baseline)
                    self.assertEqual([x['answer'] for x in peers],[] if peer=='no_peer' else [1,0])
                    self.assertIn(m.previous.RULE,p[0]['content'])
                    self.assertAlmostEqual(reference(obs),data['external_calculator']['p_state_1'],places=14)
                for a in range(3):
                    self.assertEqual(m.initial_prompt(m.DESIGN,i,f,r,a),m.previous.initial_prompt(bd,bi,f,r,a))
        with self.assertRaises(ValueError):m.tasks(dict(m.DESIGN,cells=25))

    def test_cluster_pairing_and_missing_preserved(self):
        rows=[]
        for i,f,r,order,peer in itertools.product(range(24),(0,1),m.RELATIONS,m.ORDERS,m.PEERS):
            rows.append(dict(cell=i,flip=f,relation=r,order=order,peer=peer,own_initial_valid=True,initials_valid=True,format_valid=True,
                mae=(i+1)*(.01 if order=='answer_first' else .02)+(f*.001),answer_probability_consistent=int(order=='answer_first')))
        cs=m.contrasts_for(rows,m.DESIGN)
        c=cs['copy:candidate_minus_probability_first:mae']
        self.assertAlmostEqual(c['complete_mean'],-.125)
        self.assertEqual(c['complete_cells'],24)
        self.assertAlmostEqual(c['per_cell']['0'],-.01)
        self.assertEqual(c['descriptive_interval'],m.interval(list(c['per_cell'].values()),81721,2000))
        rows[0]['format_valid']=False
        cs=m.contrasts_for(rows,m.DESIGN)
        c=cs['copy:candidate_minus_probability_first:mae']
        self.assertEqual(c['complete_cells'],23)
        self.assertIsNone(c['complete_mean']);self.assertIsNone(c['descriptive_interval'])

    def test_full_fixture_replay_retains_invalid_and_detects_tampering(self):
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
                    obs,roots=m.previous.origin.decode(p);unique={}
                    for o in obs:unique.setdefault(roots[o['id']],o)
                    q=reference(list(unique.values()))
                    obj=dict(answer=int(q>.5),p_state_1=q,citations=[o['id'] for o in obs])
                    content='fixture invalid' if count==1 else json.dumps(obj)
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=content))],usage=dict(prompt_tokens=10,completion_tokens=20))
                    net.return_value.__enter__.return_value.read.return_value=canonical(raw).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for i in range(24):m.run_cell(client,d,i,dr)
                self.assertEqual(net.call_count,672)
            s,rows,cs,hashes=m.summarize(root)
            self.assertEqual(s['calls'],672);self.assertEqual(s['initial_calls'],288)
            self.assertEqual(s['initial_invalid'],1);self.assertEqual(s['final_invalid'],0)
            self.assertEqual(s['engineering_gate']['baseline_calls'],96)
            self.assertFalse(s['engineering_gate']['passed']);self.assertEqual(s['decision'],'DISCUSS_BEFORE_MORE_INFERENCE')
            self.assertTrue(any(c['complete_mean'] is None for c in cs.values()))
            for key,g in s['groups'].items():
                self.assertEqual(g['correct_posterior_all_calls'],48)
                self.assertEqual(g['order_compliant_all_calls'],0 if 'probability_first' in key else 48)
            path=dr/'result-23.json';obj=json.loads(path.read_text());obj['cell']=22;save(path,obj)
            with self.assertRaises(ValueError):m.summarize(root)


if __name__=='__main__':unittest.main()
