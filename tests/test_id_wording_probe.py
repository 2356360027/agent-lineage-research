"""Software fixtures only. No model results or network inference."""
import itertools
import json
from decimal import Decimal
from functools import partial
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime.dependency_probe import sources
from runtime.e1 import save,digest
from runtime.format_probe_v2 import Client
from runtime.id_wording_probe import (FAMILIES,OLD_PHRASE,NEW_PHRASE,configs,prompt,
    run_cell,schedule,strict_evaluate,summarize,tasks,validate)
from runtime.readiness_pipeline import oracle,cells as old_cells


def design():
    return json.loads((Path(__file__).resolve().parents[1]/'configs/id_wording_v1.json').read_text())


class IdWordingTests(unittest.TestCase):
    def test_freshness_and_prompt_invariants(self):
        d=design();validate(d);ts=tasks(d);self.assertEqual(len(ts),12)
        old={tuple(sorted(c['reliabilities'])) for stage in ('double','communication') for c in
             old_cells(dict(protocol='readiness-pipeline-v1',seed=25108369),stage)}
        self.assertTrue(all(tuple(sorted(c['reliabilities'])) not in old for c in ts))
        self.assertEqual(len({tuple(c['reliabilities']) for c in ts}),12)
        for i,c in enumerate(ts):
            for flip,fam,assign in itertools.product((0,1),range(3),(0,1)):
                a=prompt(c,i,flip,fam,assign,'legacy');b=prompt(c,i,flip,fam,assign,'revised')
                self.assertEqual(a[1],b[1]);self.assertEqual(a[0]['content'].replace(OLD_PHRASE,NEW_PHRASE),b[0]['content'])
                payload=json.loads(a[1]['content']);obs=payload['observations']
                self.assertEqual(payload['peer_assessments'],[])
                self.assertEqual(set(o['id'] for o in obs),set(FAMILIES[fam]))
                # Independent decimal oracle from the actual request.
                l1=l0=Decimal(1)
                for o in obs:
                    r=Decimal(str(o['effective_reliability']))
                    l1*=r if o['value'] else 1-r;l0*=1-r if o['value'] else r
                self.assertAlmostEqual(oracle(obs),float(l1/(l1+l0)))
                self.assertEqual(int(oracle(obs)>.5),flip)
        entries=[s for i in range(12) for s in schedule(d,i)]
        self.assertEqual(len(entries),672)
        self.assertEqual(sum(s[0]=='sample' for s in entries),576)
        d['seed']+=1
        with self.assertRaises(ValueError):validate(d)

    def test_citation_allowlist(self):
        raw=dict(model='fixture',choices=[dict(finish_reason='stop',message=dict(content=json.dumps(
            dict(answer=0,p_state_1=.2,citations=['O0']))))])
        self.assertFalse(strict_evaluate(raw,'joint','fixture',{'oak','pine'})['format_valid'])
        self.assertTrue(strict_evaluate(raw,'joint','fixture',{'O0','O1'})['format_valid'])

    def test_full_fixture_audit(self):
        d=design();cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',
            served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096})
        cfgs=configs(cfg,d)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=d,configs=cfgs,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    data=json.loads(req.data);obs=json.loads(data['messages'][1]['content'])['observations']
                    q=oracle(obs);obj=dict(answer=int(q>.5),p_state_1=q,citations=[o['id'] for o in obs])
                    raw=dict(model=cfg['model'],choices=[dict(finish_reason='stop',message=dict(content=json.dumps(obj)))],
                             usage=dict(prompt_tokens=10,completion_tokens=5))
                    net.return_value.__enter__.return_value.read.return_value=json.dumps(raw).encode();return net.return_value
                net.side_effect=respond
                clients={k:Client(v,'http://127.0.0.1:8000/v1',dr) for k,v in cfgs.items()}
                for i in range(12):run_cell(clients,d,i,dr)
                self.assertEqual(net.call_count,672)
            s,rows,pairs,h=summarize(root)
            self.assertEqual(s['overall']['mae'],0)
            self.assertEqual(s['overall']['direction'],1)
            self.assertEqual(s['contrasts']['sample/family0/revised/id']['valid_pairs'],48)
            self.assertEqual(s['contrasts']['sample/family0/revised/noise']['valid_pairs'],48)
            self.assertEqual(s['contrasts']['sample/family0/revised_minus_legacy_mae']['valid_pairs'],96)
            target=next(dr.glob('i*.json'));rec=json.loads(target.read_text());rec['status']='pending';save(target,rec)
            with self.assertRaises(ValueError):summarize(root)


if __name__=='__main__':unittest.main()
