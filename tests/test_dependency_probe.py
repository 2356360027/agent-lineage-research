"""Software-only fixtures. No network, weights, or experimental observations."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.e1 import Client, digest, save
from runtime.dependency_probe import (ARMS, call_seed, check_server, configure,
                                      run_item, sources, validate_design)
from runtime.probe_score import diagnostics, interval, oracle, summarize


def design_fixture():
    value=json.loads((Path(__file__).resolve().parents[1]/'configs/dependency_probe_v1.json').read_text())
    value['explore'].update(items=2,repetitions=2)
    value['bootstrap_resamples']=100
    return value


def config_fixture(design):
    return configure({'deployment':'native','model':'UNIT_TEST_ONLY',
        'model_path':str(Path('UNIT_TEST_NO_WEIGHTS').resolve()),
        'server_settings_reported':{'max_model_len':4096}},design,'explore')


def server_fixture(cfg):
    return {'data':[{'id':cfg['model'],'root':cfg['model_path'],'max_model_len':4096}]}


class RecordingFixture:
    def __init__(self): self.calls=[]
    def call(self,key,prompt,seed,visible):
        self.calls.append((key,prompt,seed,visible))
        return {'answer':1,'p_state_1':0.6,'citations':sorted(visible)}


def materialize_test_only_run(root):
    design=design_fixture();cfg=config_fixture(design)
    directory=root/'shard-000';directory.mkdir()
    bundle=sources()
    save(directory/'manifest.json',{'run_spec':{'config':cfg,'design':design,
         'stage':'explore','shards':1,'source_sha256':digest(bundle)},
         'shard':0,'source_bundle':bundle})
    save(directory/'server_metadata.json',server_fixture(cfg))
    raw={'model':'UNIT_TEST_ONLY','choices':[{'finish_reason':'stop',
        'message':{'content':json.dumps({'answer':1,'p_state_1':0.6,'citations':[]})}}],
        'usage':{'prompt_tokens':12,'completion_tokens':8}}
    with patch('urllib.request.urlopen') as mocked:
        mocked.return_value.__enter__.return_value.read.return_value=json.dumps(raw).encode()
        client=Client(cfg,'http://127.0.0.1:8000/v1',directory)
        for item in range(cfg['items']): run_item(client,cfg,item,directory)
        if mocked.call_count != 26: raise AssertionError('Unexpected test fixture call count')
    return directory


class ProbeTests(unittest.TestCase):
    def test_paired_prompts_seed_and_no_gold(self):
        cfg=config_fixture(design_fixture());fixture=RecordingFixture()
        with tempfile.TemporaryDirectory() as tmp:
            result=run_item(fixture,cfg,0,Path(tmp))
        self.assertEqual(len(fixture.calls),13)
        self.assertEqual(len({v[0] for v in fixture.calls}),13)
        updates={v[0]:v for v in fixture.calls[3:]}
        for rep in range(2):
            payloads={arm:json.loads(updates[f'i0-r{rep}-{arm}'][1][1]['content']) for arm in ARMS}
            for payload in payloads.values():
                self.assertEqual(set(payload),{'observations','your_previous_assessment','peer_assessments'})
                self.assertEqual(payload['observations'],result['observations'])
                self.assertEqual(payload['your_previous_assessment'],result['initial'][0])
                self.assertEqual(len(payload['observations']),3)
            self.assertEqual(payloads['hidden'],payloads['hidden_repeat'])
            self.assertTrue(all(x['answer']==0 for x in payloads['injected_zero']['peer_assessments']))
            self.assertTrue(all(x['answer']==1 for x in payloads['injected_one']['peer_assessments']))
            self.assertEqual(len({updates[f'i0-r{rep}-{a}'][2] for a in ARMS[:-1]}),1)
            self.assertNotEqual(updates[f'i0-r{rep}-hidden'][2],updates[f'i0-r{rep}-hidden_repeat'][2])

    def test_repeatability_and_no_gold_dependency(self):
        cfg=config_fixture(design_fixture());a=RecordingFixture();b=RecordingFixture()
        with tempfile.TemporaryDirectory() as tmp:
            run_item(a,cfg,4,Path(tmp))
            run_item(b,cfg,4,Path(tmp))
        self.assertEqual(a.calls,b.calls)
        self.assertNotEqual(call_seed(1,0,0,'paired'),call_seed(2,0,0,'paired'))

    def test_design_rejects_unsupported_changes(self):
        design=design_fixture();validate_design(design)
        for key,value in [('arms',ARMS+['hidden']),('bootstrap_resamples',0),('primary_endpoints',[]),('max_tokens',0)]:
            bad=copy.deepcopy(design);bad[key]=value
            with self.assertRaises(ValueError): validate_design(bad)
        for key,value in [('items',0),('repetitions',1.2),('world_seed','seed')]:
            bad=copy.deepcopy(design);bad['explore'][key]=value
            with self.assertRaises(ValueError): validate_design(bad)
        bad=copy.deepcopy(design);bad['validate']['world_seed']=bad['explore']['world_seed']
        with self.assertRaises(ValueError): validate_design(bad)

    def test_server_context_guard(self):
        cfg=config_fixture(design_fixture());models=server_fixture(cfg)
        check_server(cfg,models)
        models['data'][0]['max_model_len']=8192
        with self.assertRaises(ValueError): check_server(cfg,models)

    def test_oracle_and_cluster_interval(self):
        self.assertAlmostEqual(oracle([{'value':1,'reliability':.8}]),.8)
        self.assertAlmostEqual(oracle([{'value':1,'reliability':.8},{'value':0,'reliability':.8}]),.5)
        self.assertEqual(interval([.2,.2],7,100)['item_bootstrap_percentile_interval'],[.2,.2])

    def test_full_fixture_scoring_and_integrity_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=materialize_test_only_run(root)
            report,rows,hashes=summarize(root)
            self.assertEqual(report['calls'],26)
            self.assertEqual(report['independent_worlds'],2)
            self.assertEqual(report['prompt_tokens'],312)
            self.assertEqual(report['completion_tokens'],208)
            self.assertEqual(report['endpoints']['injected_one_minus_zero_probability']['mean'],0)
            self.assertEqual(len(rows),2)
            self.assertIn('shard-000/server_metadata.json',hashes)
            self.assertIn('mean_p_natural',rows[0])
            self.assertEqual(diagnostics(root)['call_status_counts'],{'ok':26})
            target=directory/'i0-r0-hidden.json';original=target.read_bytes()
            for field,value in [('request_hash','changed'),('status','pending')]:
                rec=json.loads(original);rec[field]=value;save(target,rec)
                with self.assertRaises(ValueError): summarize(root)
                target.write_bytes(original)
            resultpath=directory/'result-0.json';result=resultpath.read_bytes()
            resultpath.unlink()
            with self.assertRaises(ValueError): summarize(root)
            resultpath.write_bytes(result)
            manifestpath=directory/'manifest.json';manifest=json.loads(manifestpath.read_text())
            manifest['source_bundle']['e1.py']+='\n# changed test source\n'
            manifest['run_spec']['source_sha256']=digest(manifest['source_bundle'])
            save(manifestpath,manifest)
            with self.assertRaisesRegex(ValueError,'Analysis runtime differs'): summarize(root)


if __name__=='__main__': unittest.main()
