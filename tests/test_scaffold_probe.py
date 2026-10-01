"""Software fixtures only. No empirical inference."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime.dependency_probe import sources
from runtime.e1 import canonical,digest,save
from runtime.format_probe_v2 import Client
from runtime.scaffold_probe import (ARMS,GATE,calculate,factor_metrics,outcome,prompt,
    readiness,run_cell,summarize,tasks,validate)
from runtime.stage_diagnostic import bayes,tasks as old_tasks


def design():return json.loads((Path(__file__).resolve().parents[1]/'configs/scaffold_v1.json').read_text())


def envelope(obj):return dict(model='SOFTWARE_FIXTURE_ONLY',choices=[dict(finish_reason='stop',message=dict(content=canonical(obj)))],usage=dict(prompt_tokens=10,completion_tokens=20))


class ScaffoldTests(unittest.TestCase):
    def test_freshness_and_tool(self):
        d=design();validate(d);old={tuple(sorted(c['reliabilities'])) for c in old_tasks(dict(protocol='stage-diagnostic-v1',seed=59283147))}
        for i,c in enumerate(tasks(d)):
            self.assertNotIn(tuple(sorted(c['reliabilities'])),old)
            for f in (0,1):
                for a in (0,1):
                    ps=[prompt(c,i,f,a,arm) for arm in ARMS[:3]]
                    self.assertEqual(ps[0][1],ps[1][1]);self.assertEqual(ps[0][1],ps[2][1])
                    obs=json.loads(ps[0][1]['content'])['observations']
                    tool=calculate(dict(format_valid=True,assessment=dict(observations=obs)))
                    self.assertEqual(tool['p_state_1'],bayes(obs)['p_state_1'])
                    self.assertEqual(tool['input_observations'],obs)
        self.assertEqual(calculate(dict(format_valid=False))['status'],'error')
        self.assertEqual(calculate(dict(format_valid=True,assessment=dict(observations=[])))['status'],'error')
        d['seed']+=1
        with self.assertRaises(ValueError):validate(d)

    def test_factor_parser(self):
        obs=[dict(id='O0',value=0,effective_reliability=.8),dict(id='O1',value=1,effective_reliability=.6)]
        obj=dict(observations=obs,**bayes(obs),answer=0,factors=[dict(id='O0',p_x_given_y0=.8,p_x_given_y1=.2),dict(id='O1',p_x_given_y0=.4,p_x_given_y1=.6)])
        out=outcome(envelope(obj),'factors','SOFTWARE_FIXTURE_ONLY');m=factor_metrics(out,obs,.0001)
        self.assertEqual(m['factors_correct'],1);self.assertEqual(m['products_consistent'],1)
        obj['factors'][0]['p_x_given_y0']=.2
        m=factor_metrics(outcome(envelope(obj),'factors','SOFTWARE_FIXTURE_ONLY'),obs,.0001)
        self.assertEqual(m['factors_correct'],0);self.assertEqual(m['products_consistent'],0)
        raw=envelope(obj);raw['choices'][0]['finish_reason']='length'
        self.assertFalse(outcome(raw,'factors',raw['model'])['format_valid'])
        self.assertFalse(readiness([dict(format_valid=False)])['passed'])

    def test_full_fixture_audit(self):
        d=design();cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096},**{k:d[k] for k in ('temperature','top_p','max_tokens')})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=d,config=cfg,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    payload=json.loads(req.data);system=payload['messages'][0]['content'];obs=json.loads(payload['messages'][1]['content'])['observations'];g=bayes(obs)
                    if 'exactly one field:' in system:obj=dict(observations=obs)
                    elif 'exactly six fields:' in system:
                        fs=[dict(id=o['id'],p_x_given_y0=o['effective_reliability'] if o['value']==0 else 1-o['effective_reliability'],p_x_given_y1=o['effective_reliability'] if o['value']==1 else 1-o['effective_reliability']) for o in obs]
                        obj=dict(observations=obs,factors=fs,answer=int(g['p_state_1']>.5),**g)
                    else:obj=dict(answer=int(g['p_state_1']>.5),p_state_1=g['p_state_1'],citations=['O0','O1'])
                    net.return_value.__enter__.return_value.read.return_value=canonical(envelope(obj)).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr,outcome)
                for i in range(12):run_cell(client,d,i,dr)
                self.assertEqual(net.call_count,240)
            s,rows,pairs,h=summarize(root)
            self.assertTrue(all(g['passed'] for g in s['readiness'].values()))
            self.assertEqual(s['groups']['factors']['factors_correct']['mean'],1)
            self.assertEqual(s['groups']['calculator']['tool_extraction_exact']['mean'],1)
            self.assertTrue(all(c['mae_delta']==0 for c in s['primary_mae_contrasts'].values()))
            path=dr/'result-0.json';rec=json.loads(path.read_text());next(iter(rec['calculator_records'].values()))['p_state_1']=.5;save(path,rec)
            with self.assertRaises(ValueError):summarize(root)


if __name__=='__main__':unittest.main()
