"""SOFTWARE_FIXTURE_ONLY; no synthetic fixture output is empirical evidence."""
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from statistics import mean
from runtime import routing_timing_v2 as m
from runtime.dependency_probe import sources
from runtime.e1 import canonical,digest,save
from runtime.format_probe_v2 import Client
from test_decision_interface import initials,reference


class RoutingTimingV2Tests(unittest.TestCase):
    def test_fresh_cells_receiver_dedup_and_calculator_invariants(self):
        self.assertEqual(len(m.tasks(m.DESIGN)),12)
        for i,f,r,a,stage in itertools.product(range(12),(0,1),m.RELATIONS,range(3),(1,2)):
            raw,unique=m.delivery(m.DESIGN,i,f,r,a,stage,'raw')
            dedup,same=m.delivery(m.DESIGN,i,f,r,a,stage,'dedup')
            self.assertEqual(unique,same);self.assertEqual(unique,dedup['messages'])
            self.assertEqual(len(unique),2 if r=='copy' else 3)
            self.assertEqual(len(raw['messages']),3 if stage==1 else 5)
            self.assertEqual(raw['messages'][0],m.world(m.DESIGN,i,f,r)[0][a])
            self.assertEqual(unique[0],raw['messages'][0])
            roots=raw['origin_registry'];first={}
            for o in raw['messages']:first.setdefault(roots[o['id']],o)
            self.assertEqual(list(first.values()),unique)
            self.assertEqual(raw['external_calculator'],dedup['external_calculator'])
            self.assertAlmostEqual(reference(unique),raw['external_calculator']['p_state_1'],places=14)
            for receipt in dedup['duplicate_receipts']:
                self.assertEqual(roots[receipt['message_id']],roots[receipt['prior_message_id']])
        with self.assertRaises(ValueError):m.tasks(dict(m.DESIGN,cells=13))

    def test_timing_peer_identity_no_extra_observations_or_gold(self):
        private=initials()
        for route,a,stage in itertools.product(('raw','dedup'),range(3),(1,2)):
            base=None
            for timing in ('open','delay','never'):
                if route=='raw' and timing=='never':continue
                p,_=m.stage_prompt(m.DESIGN,0,0,'copy',route+'_'+timing,a,stage,private[a],private)
                data=json.loads(p[1]['content']);peers=data.pop('peer_assessments')
                if base is None:base=data
                self.assertEqual(base,data)
                shown=timing=='open' or (timing=='delay' and stage==2)
                self.assertEqual([v['agent'] for v in peers],[x for x in m.AGENTS if x!=m.AGENTS[a]] if shown else [])
                self.assertEqual([v['answer'] for v in peers],[private[b]['assessment']['answer'] for b in range(3) if b!=a] if shown else [])
                self.assertIn(m.interface.RULE,p[0]['content'])
                self.assertTrue(p[0]['content'].endswith(m.interface.ORDER_TEXT['answer_first']))
                self.assertNotIn('gold',data)
        p,_=m.union_prompt(m.DESIGN,0,0,'copy',False)
        self.assertNotIn('external_calculator',json.loads(p[1]['content']))
        self.assertNotIn('external_calculator',p[0]['content'])
        p,visible=m.adjudicator_prompt(private)
        self.assertEqual(set(json.loads(p[1]['content'])),{'terminal_assessments'})
        self.assertNotIn('messages',json.loads(p[1]['content']))
        private[0]=dict(format_valid=False,assessment=None,violation='fixture')
        p,_=m.stage_prompt(m.DESIGN,0,0,'copy','dedup_open',1,1,private[1],private)
        self.assertEqual(json.loads(p[1]['content'])['peer_assessments'][0],{'agent':'A','available':False})

    def test_paired_factorial_and_missing_policy(self):
        rows=[]
        val={'raw_open':.2,'raw_delay':.15,'dedup_open':.12,'dedup_delay':.06,'dedup_never':.03,
             'single_union':.02,'private_ensemble':.3,'union_ensemble':.01,'unassisted_union':.4}
        for i,f,r,job in itertools.product(range(12),(0,1),m.RELATIONS,m.METHODS+m.CONTROLS):
            rows.append(dict(cell=i,flip=f,relation=r,job=job,format_valid=True,pipeline_valid=True,mae=val[job],direction=1))
        c=m.pair_contrasts(rows,m.DESIGN)
        self.assertAlmostEqual(c['delay_with_dedup:mae']['complete_mean'],-.06)
        self.assertAlmostEqual(c['dedup_with_open:mae']['complete_mean'],-.08)
        self.assertAlmostEqual(c['routing_timing_interaction:mae']['complete_mean'],-.01)
        rows[0]['pipeline_valid']=False
        c=m.pair_contrasts(rows,m.DESIGN)
        self.assertIsNone(c['dedup_with_open:mae']['complete_mean'])
        self.assertEqual(c['dedup_with_open:mae']['complete_cells'],11)

    def test_full_fixture_raw_replay_invalids_and_tampering(self):
        d=m.DESIGN
        cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096},**{k:d[k] for k in ('temperature','top_p','max_tokens')})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=d,gate=m.GATE,decision_rule=m.DECISION,config=cfg,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            count=0
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    nonlocal count
                    count+=1;p=json.loads(json.loads(req.data)['messages'][1]['content'])
                    if 'terminal_assessments' in p:
                        outs=[x['assessment'] for x in p['terminal_assessments'] if x['available']]
                        q=mean(x['p_state_1'] for x in outs);ids=sorted({v for x in outs for v in x['citations']})
                    else:
                        obs,roots=m.interface.origin.decode(p);unique={}
                        for o in obs:unique.setdefault(roots[o['id']],o)
                        q=reference(list(unique.values()));ids=[o['id'] for o in obs]
                    obj=dict(answer=int(q>.5),p_state_1=q,citations=ids)
                    content='fixture invalid' if count==1 else json.dumps(obj)
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=content))],usage=dict(prompt_tokens=10,completion_tokens=20))
                    net.return_value.__enter__.return_value.read.return_value=canonical(raw).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for i in range(12):m.run_cell(client,d,i,dr)
                self.assertEqual(net.call_count,2208)
            s,rows,cs,hashes=m.summarize(root)
            self.assertEqual(s['invalid_calls'],1);self.assertFalse(s['readiness']['all_format_valid'])
            self.assertEqual(s['reference_calls'],48);self.assertEqual(s['reference_correct'],48)
            self.assertFalse(s['developmental_gain_rule_passed']);self.assertFalse(s['primary_complete'])
            self.assertEqual(s['decision'],'DISCUSS_BEFORE_MORE_INFERENCE')
            self.assertTrue(any(x['complete_mean'] is None for x in cs.values()))
            # Shared S0 does not create extra independent cells or fictitious calls.
            self.assertEqual(len(rows['initials']),144);self.assertEqual(len(rows['system']),432)
            self.assertEqual(len(rows['agents']),1440)
            path=dr/'result-0.json';obj=json.loads(path.read_text());obj['task']['regime']='tamper';save(path,obj)
            with self.assertRaises(ValueError):m.summarize(root)


if __name__=='__main__':unittest.main()
