"""Injected fixtures only; no model, credentials or SQL execution."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import research_context_interface_runner as cal
from scripts import run_research_context_interface as launch
from tests.test_research_context_interface import requests


class Counter:
    def chat(self, messages): return 100
    def text(self, content): return 30


class Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(temp.cleanup); self.root=Path(temp.name)
        self.plan=cal.build_plan(requests()); self.called=[]; self.now=0

    def scripted(self, changed=None):
        def factory(ident,log):
            def transport(payload, timeout):
                self.called.append(ident)
                raw=dict(model='deepseek-flash',usage=dict(prompt_tokens=100,completion_tokens=30),
                    choices=[dict(message=dict(content='{"kind":"run_sql","sql":"SELECT 1"}'),finish_reason='stop')])
                if changed: raw=changed(len(self.called),raw)
                log('transport-001',dict(transport_status='RESPONSE_RECEIVED',response_text=json.dumps(raw)))
                return raw
            return transport
        return factory

    def run_script(self, changed=None):
        return cal.run(self.plan,counter=Counter(),transport_factory=self.scripted(changed),
            output=self.root/'run',mode='SCRIPTED_NOT_MODEL',clock=lambda:self.now)

    def test_caps_consistent_and_full_fixture_run(self):
        self.assertEqual(launch.LIMITS['ceiling_micro_cny'],cal.CEILING)
        self.assertEqual(cal.CEILING,150000)
        r=self.run_script()
        self.assertEqual(len(self.called),16)
        self.assertEqual(r['SQL_executions'],0)
        self.assertEqual(r['model_calls'],0)
        self.assertEqual(r['readout']['totals']['original']['admitted'],8)
        self.assertEqual(r['readout']['totals']['separated']['admitted'],8)
        self.assertFalse(r['readout']['task_correctness_assessed'])
        self.assertFalse(list((self.root/'run').rglob('t*.pending.json')))

    def test_modified_pair_order_or_extra_expected_rejected(self):
        for mode in ('order','prompt','extra'):
            p=deepcopy(self.plan)
            if mode=='order': p['jobs'].reverse()
            elif mode=='prompt':
                next(j for j in p['jobs'] if j['arm']=='separated')['payload']['messages'][1]['content']+='changed'
            else: p['jobs'][0]['expected']={'kind':'answer','text':'1'}
            with self.assertRaises(ValueError): cal.validate(p)

    def test_malformed_continues_no_retry(self):
        def mutate(i,raw):
            if i==1: raw['choices'][0]['message']['content'] *= 2
            return raw
        r=self.run_script(mutate)
        self.assertEqual(len(set(self.called)),16)
        self.assertFalse(r['rows'][0]['score']['admitted'])
        self.assertEqual(r['rows'][0]['status'],'OBSERVED')

    def test_failure_stops_reserves_no_prefix_effect(self):
        def mutate(i,raw):
            if i==2: raise TimeoutError('fixture')
            return raw
        r=self.run_script(mutate)
        self.assertEqual(len(self.called),2)
        self.assertEqual(r['rows'][1]['status'],'FAILED')
        self.assertTrue(all(x['status']=='NOT_STARTED' for x in r['rows'][2:]))
        self.assertEqual(r['readout']['paired_descriptive_differences'],[])
        self.assertGreater(r['cost_guard']['uncertain_reserved_micro_cny'],0)

    def test_count_mismatch_stops_and_keeps_response(self):
        def mutate(i,raw): raw['usage']['prompt_tokens']=101; return raw
        r=self.run_script(mutate)
        self.assertEqual(len(self.called),1)
        self.assertEqual(r['rows'][0]['status'],'FAILED')
        self.assertTrue((self.root/'run'/self.called[0]/'action.provider.json').exists())

    def test_late_response_preserved_not_observed(self):
        def mutate(i,raw): self.now=601; return raw
        r=self.run_script(mutate)
        self.assertEqual(len(self.called),1)
        self.assertNotIn('score',r['rows'][0])

    def test_overwrite_and_missing_slots_rejected(self):
        r=self.run_script()
        with self.assertRaises(FileExistsError): self.run_script()
        with self.assertRaises(ValueError): cal.summarize(r['rows'][:-1],self.plan)

    def test_wrong_approval_unfrozen_date_claim_before_key(self):
        for value in (None,True,.12,.2,'0.15',.15):
            with patch.object(launch.getpass,'getpass') as key:
                with self.assertRaises(ValueError): launch.execute(self.root,'fixture',value)
                key.assert_not_called()
        for mode in ('date','claim'):
            if mode=='claim': (self.root/'execution_claim.json').touch()
            with patch.object(launch,'frozen'),patch.object(launch,'verify'), \
                 patch.object(launch,'day',return_value='1900-01-01' if mode=='date' else launch.PRICE_DATE), \
                 patch.object(launch.getpass,'getpass') as key:
                with self.assertRaises(ValueError): launch.execute(self.root,'fixture',.15)
                key.assert_not_called()

    def test_complete_audit_replays_scores_and_costs(self):
        # OFFICIAL-shaped journal is produced by an injected fake transport only.
        r=cal.run(self.plan,counter=Counter(),transport_factory=self.scripted(),
            output=self.root/'experiment',mode='OFFICIAL_CONTEXT_INTERFACE',clock=lambda:0)
        launch.audit.save_new(self.root/'execution_claim.json',dict(manifest_sha256='fixture',approved_cny=.15))
        launch.audit.save_new(self.root/'launch_outcome.json',{k:r[k] for k in ('status','dispatch_attempts')})
        with patch.object(launch,'frozen'),patch.object(launch,'verify',
                return_value=({'input_tokens':[100]*16},self.plan,Counter())):
            report=launch.analyze(self.root,'fixture')
        self.assertEqual(report['status'],'SEALED_CONTEXT_INTERFACE_REPLAY_VERIFIED')
        self.assertEqual(report['readout'],r['readout'])

    def test_secret_only_owned_stdin_and_no_saved_key(self):
        with patch.object(launch,'frozen'),patch.object(launch,'verify'), \
             patch.object(launch,'day',return_value=launch.PRICE_DATE), \
             patch.object(launch.getpass,'getpass',return_value='synthetic-test-secret'), \
             patch.object(launch,'owned_worker',return_value=dict(status='WORKER_TIMEOUT',
                        automatic_retry=False,billing_unknown=True)) as worker:
            launch.execute(self.root,'fixture',.15)
        args=worker.call_args.args
        self.assertNotIn('synthetic-test-secret',str(args[0]))
        self.assertEqual(json.loads(args[1])['key'],'synthetic-test-secret')
        for path in self.root.rglob('*.json'):
            self.assertNotIn('synthetic-test-secret',path.read_text('utf-8'))

    def test_full_cost_cap_before_transport(self):
        with patch.object(Counter,'chat',return_value=4000):
            with self.assertRaises(ValueError): self.run_script()
        self.assertEqual(self.called,[])
        self.assertFalse((self.root/'run').exists())


if __name__=='__main__': unittest.main()
