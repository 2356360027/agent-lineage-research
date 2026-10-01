"""Software fixtures only, not empirical relay results."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime.dependency_probe import sources
from runtime.e1 import canonical,digest,save
from runtime.format_probe_v2 import Client
from runtime.tool_relay import CONDITIONS,evidence,message,run_cell,summarize,tasks,validate
from runtime.scaffold_probe import calculate,tasks as old_tasks


def design():return json.loads((Path(__file__).resolve().parents[1]/'configs/tool_relay_v1.json').read_text())


class ToolRelayTests(unittest.TestCase):
    def test_intervention_invariants(self):
        d=design();validate(d);ts=tasks(d)
        old={tuple(sorted(c['reliabilities'])) for c in old_tasks(dict(protocol='likelihood-scaffold-v1',seed=60394271))}
        for i,c in enumerate(ts):
            self.assertNotIn(tuple(sorted(c['reliabilities'])),old)
            for flip in (0,1):
                obs,sobs=evidence(c,i,flip);self.assertEqual(len(sobs),1);self.assertIn(sobs[0],obs)
                tool=calculate(dict(format_valid=True,assessment=dict(observations=obs)))
                peer=dict(agent='A',answer=0,p_state_1=.2)
                ps={k:message(c,i,flip,obs,'calculator',tool,peer,k,sobs) for k in CONDITIONS}
                self.assertTrue(all(p[0]==ps['hidden'][0] for p in ps.values()))
                payload={k:json.loads(p[1]['content']) for k,p in ps.items()}
                self.assertTrue(all(p['external_calculator']==tool for p in payload.values()))
                self.assertEqual(payload['peer']['observations'],payload['hidden']['observations'])
                lineage=dict(payload['lineage']);lineage.pop('peer_evidence_provenance')
                self.assertEqual(lineage,payload['peer'])
                self.assertEqual(payload['duplicate']['observations'],obs+sobs)

    def test_full_fixture_audit(self):
        d=design();cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096},**{k:d[k] for k in ('temperature','top_p','max_tokens')})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=d,config=cfg,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    req=json.loads(req.data);p=json.loads(req['messages'][1]['content']);obs=p['observations']
                    if 'exactly one field:' in req['messages'][0]['content']:obj=dict(observations=obs)
                    else:
                        q=p['external_calculator']['p_state_1'];obj=dict(answer=int(q>.5),p_state_1=q,citations=list(dict.fromkeys(o['id'] for o in obs)))
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=canonical(obj)))],usage=dict(prompt_tokens=10,completion_tokens=20))
                    net.return_value.__enter__.return_value.read.return_value=canonical(raw).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr)
                for i in range(12):run_cell(client,d,i,dr)
                self.assertEqual(net.call_count,168)
            s,r,p,h=summarize(root)
            self.assertTrue(s['hidden_readiness']['passed']);self.assertEqual(s['exact_extractions'],48)
            self.assertEqual(s['sender_groups']['False']['agrees_with_full_evidence'],0)
            self.assertEqual(s['sender_groups']['True']['agrees_with_full_evidence'],12)
            self.assertTrue(all(c['mae_delta']==0 for c in s['contrasts'].values()))
            path=dr/'result-0.json';rec=json.loads(path.read_text());rec['cases']['0']['peer_message']['answer']^=1;save(path,rec)
            with self.assertRaises(ValueError):summarize(root)


if __name__=='__main__':unittest.main()
