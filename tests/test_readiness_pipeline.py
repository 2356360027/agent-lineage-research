"""Offline simulated fixtures only; no model outputs or experiment estimates."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.e1 import digest
from runtime.format_probe_v2 import prompt as selected_prompt
from runtime.readiness_pipeline import (STAGES, cells, count, evaluate, execute,
    gate, observations, oracle, prompt, summarize, validate)


def design():
    return json.loads((Path(__file__).resolve().parents[1]/'configs/readiness_pipeline_v1.json').read_text())


class PipelineTests(unittest.TestCase):
    def test_frozen_tasks_and_prompt_invariants(self):
        d = design(); validate(d)
        self.assertEqual(sum(count(s) for s in STAGES), 504)
        self.assertNotEqual(cells(d,'double'), cells(d,'communication'))
        from runtime.format_probe import LEVELS
        old = {round(r*t+(1-r)*(1-t),12) for rs,ts in (
            ([.65,.8,.9],[.7,.85,.95]),([.61,.74,.86,.93],[.68,.82,.97])) for r in rs for t in ts}
        self.assertTrue(set(d['single_reliabilities']).isdisjoint(old|set(LEVELS)))
        for stage in STAGES:
            for cell in cells(d,stage):
                self.assertAlmostEqual(sum(oracle(observations(cell,f)) for f in (0,1)),1)
                for f in (0,1):
                    hidden = prompt(cell,f,stage,'hidden')
                    if stage=='single':
                        self.assertEqual(hidden, selected_prompt(cell['reliabilities'][0],f,'joint'))
                    else:
                        payload = json.loads(hidden[1]['content'])
                        self.assertEqual(set(payload), {'observations','peer_assessments'})
                        repeated = json.loads(prompt(cell,f,stage,'repeat')[1]['content'])['observations']
                        self.assertAlmostEqual(oracle(repeated),oracle(payload['observations']))
                        self.assertEqual(len(repeated),3)
                        for arm in ('verdict0','verdict1'):
                            other=prompt(cell,f,stage,arm)
                            self.assertEqual(hidden[0],other[0])
                            self.assertEqual(payload['observations'],json.loads(other[1]['content'])['observations'])
        d['seed']+=1
        with self.assertRaises(ValueError):validate(d)

    def test_citation_extension_is_not_output_repair(self):
        obj={'answer':1,'p_state_1':.8,'citations':['O0','O1']}
        raw={'model':'TEST','choices':[{'finish_reason':'stop','message':{'content':json.dumps(obj)}}]}
        original=copy.deepcopy(raw)
        self.assertFalse(evaluate(raw,'joint','TEST',{'O0'})['format_valid'])
        self.assertEqual(evaluate(raw,'joint','TEST',{'O0','O1'})['assessment'],obj)
        self.assertEqual(raw,original)
        raw['choices'][0]['message']['content']='{"answer":1,"answer":0,"p_state_1":0.8,"citations":["O1"]}'
        self.assertFalse(evaluate(raw,'joint','TEST',{'O0','O1'})['format_valid'])

    def test_all_stages_and_gate_stop(self):
        d=design(); cfg={'deployment':'native','model':'SOFTWARE_FIXTURE_ONLY','model_path':'/fake',
            'served_model_metadata':{'root':'/fake'},'server_settings_reported':{'max_model_len':4096},
            **{k:d[k] for k in ('temperature','top_p','max_tokens')}}
        metadata={'data':[{'id':cfg['model'],'root':'/fake','max_model_len':4096}]}
        for bad in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'new'; calls=[]
                with patch('runtime.readiness_pipeline.verify'), patch('urllib.request.urlopen') as net, patch('builtins.print'):
                    def respond(req,timeout):
                        if isinstance(req,str):
                            net.return_value.__enter__.return_value.read.return_value=json.dumps(metadata).encode()
                            return net.return_value
                        payload=json.loads(req.data); calls.append(payload)
                        obs=json.loads(payload['messages'][1]['content'])['observations']; q=oracle(obs)
                        obj={'answer':int(q>.5),'p_state_1':.5 if bad else q,
                             'citations':sorted({o['id'] for o in obs})}
                        raw={'model':cfg['model'],'choices':[{'finish_reason':'stop','message':{'content':json.dumps(obj)}}],
                             'usage':{'prompt_tokens':10,'completion_tokens':5}}
                        net.return_value.__enter__.return_value.read.return_value=json.dumps(raw).encode()
                        return net.return_value
                    net.side_effect=respond; execute(d,cfg,root)
                state=json.loads((root/'pipeline.json').read_text())
                self.assertEqual(len(calls),144 if bad else 504)
                self.assertEqual(state['status'],'stopped_gate_not_passed' if bad else 'complete')
                self.assertEqual((root/'double').exists(),not bad)
                for stage in state['stages']:
                    report,rows,contrasts,hashes=summarize(root/stage)
                    self.assertEqual(report['calls'],count(stage))
                    self.assertEqual(report['overall']['invalid'],0)
                    if not bad:self.assertAlmostEqual(report['overall']['mae_among_valid'],0)
                target=root/'single/shard-000/i0-f0-hidden-r0.json'; rec=json.loads(target.read_text())
                rec['status']='pending'; target.write_text(json.dumps(rec))
                with self.assertRaises(ValueError):summarize(root/'single')

    def test_invalid_output_blocks_gate(self):
        self.assertFalse(gate([{'format_valid':False}],[])['passed'])


if __name__=='__main__':unittest.main()
