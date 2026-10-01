"""Software fixtures only; no empirical model calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime.dependency_probe import sources
from runtime.e1 import digest,save
from runtime.format_probe_v2 import Client
from runtime.stage_diagnostic import ARMS,bayes,metrics,outcome,prompt,run_cell,summarize,tasks,validate
from runtime.id_wording_probe import tasks as previous_tasks
from runtime.readiness_pipeline import oracle


def design():return json.loads((Path(__file__).resolve().parents[1]/'configs/stage_diagnostic_v1.json').read_text())


def envelope(obj):return dict(model='SOFTWARE_FIXTURE_ONLY',choices=[dict(finish_reason='stop',message=dict(content=json.dumps(obj)))],usage=dict(prompt_tokens=10,completion_tokens=20))


class StageTests(unittest.TestCase):
    def test_fresh_and_invariant(self):
        d=design();validate(d);ts=tasks(d)
        previous={tuple(sorted(c['reliabilities'])) for c in previous_tasks(dict(protocol='id-wording-v1',seed=48172639,cells=12))}
        self.assertEqual(len(ts),12);self.assertEqual(len({tuple(c['reliabilities']) for c in ts}),12)
        for i,c in enumerate(ts):
            self.assertNotIn(tuple(sorted(c['reliabilities'])),previous)
            for f in (0,1):
                for a in (0,1):
                    ps=[prompt(c,i,f,a,arm) for arm in ARMS]
                    self.assertEqual(ps[0][1],ps[1][1]);self.assertEqual(ps[0][1],ps[2][1])
                    obs=json.loads(ps[0][1]['content'])['observations']
                    self.assertAlmostEqual(bayes(obs)['p_state_1'],oracle(obs))
                    self.assertNotIn('p_state_1 always means',ps[1][0]['content'])
                    self.assertEqual(int(bayes(obs)['p_state_1']>.5),f)

    def test_metrics_and_invalids(self):
        obs=[dict(id='O0',value=0,effective_reliability=.8),dict(id='O1',value=1,effective_reliability=.6)]
        gold=bayes(obs);self.assertAlmostEqual(gold['p_state_1'],3/11)
        obj=dict(observations=obs,answer=0,**gold)
        raw=envelope(obj);out=outcome(raw,'calculate',raw['model'])
        m=metrics(out,obs,'calculate',.0001)
        self.assertEqual(m['extraction_exact'],1);self.assertEqual(m['likelihoods_correct'],1)
        self.assertEqual(m['normalization_consistent'],1)
        wrong=dict(obj,likelihood_y0=.5)
        m=metrics(outcome(envelope(wrong),'calculate',raw['model']),obs,'calculate',.0001)
        self.assertEqual(m['extraction_exact'],1);self.assertEqual(m['likelihoods_correct'],0)
        self.assertEqual(m['normalization_consistent'],0)
        for copied in ([],[obs[0],obs[0]]):self.assertIsNone(bayes(copied))
        bad=envelope({'observations':obs,'answer':0})
        self.assertFalse(outcome(bad,'extract',bad['model'])['format_valid'])
        raw['choices'][0]['finish_reason']='length'
        self.assertFalse(outcome(raw,'calculate',raw['model'])['format_valid'])

    def test_full_fixture_replay(self):
        d=design();cfg=dict(deployment='native',model='SOFTWARE_FIXTURE_ONLY',model_path='/fake',
            served_model_metadata={'root':'/fake'},server_settings_reported={'max_model_len':4096},
            **{k:d[k] for k in ('temperature','top_p','max_tokens')})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dr=root/'shard-000';dr.mkdir();bundle=sources()
            save(dr/'manifest.json',dict(design=d,config=cfg,source_bundle=bundle,source_sha256=digest(bundle)))
            save(dr/'server_metadata.json',dict(data=[dict(id=cfg['model'],root='/fake',max_model_len=4096)]))
            with patch('urllib.request.urlopen') as net:
                def respond(req,timeout):
                    payload=json.loads(req.data);system=payload['messages'][0]['content'];obs=json.loads(payload['messages'][1]['content'])['observations'];g=bayes(obs)
                    if 'exactly one field:' in system:obj=dict(observations=obs)
                    elif 'exactly five fields:' in system:obj=dict(observations=obs,answer=int(g['p_state_1']>.5),**g)
                    else:obj=dict(answer=int(g['p_state_1']>.5),p_state_1=g['p_state_1'],citations=['O0','O1'])
                    net.return_value.__enter__.return_value.read.return_value=json.dumps(envelope(obj)).encode();return net.return_value
                net.side_effect=respond;client=Client(cfg,'http://127.0.0.1:8000/v1',dr,outcome)
                for i in range(12):run_cell(client,d,i,dr)
                self.assertEqual(net.call_count,144)
            s,rows,pairs,h=summarize(root)
            self.assertEqual(s['primary']['valid_pairs'],48)
            self.assertEqual(s['primary']['calculate_minus_direct_mae'],0)
            self.assertEqual(s['groups']['extract']['extraction_exact']['mean'],1)
            self.assertEqual(s['groups']['calculate']['normalization_consistent']['mean'],1)
            path=next(dr.glob('i*.json'));rec=json.loads(path.read_text());rec['request']['seed']+=1;save(path,rec)
            with self.assertRaises(ValueError):summarize(root)


if __name__=='__main__':unittest.main()
