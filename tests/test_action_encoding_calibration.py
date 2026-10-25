"""Offline synthetic software tests; no real credentials/model/database."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import action_encoding_calibration as cal
from runtime.cache_intent_staged_loop import ACTION_INSTRUCTION, parse_action
from scripts import run_action_encoding_calibration as launch


class Counter:
    # Explicit fake token counter for control-flow tests only.
    def chat(self, messages): return 100
    def text(self, content): return 30


class Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(temp.cleanup); self.root=Path(temp.name)
        self.plan=cal.build_plan(); self.called=[]; self.now=0

    def scripted(self, changed=None):
        def factory(ident,log):
            def transport(payload, timeout):
                self.called.append(ident)
                job=next(j for j in self.plan['jobs'] if j['id']==ident)
                raw=dict(model='deepseek-flash',usage=dict(prompt_tokens=100,completion_tokens=30),
                    choices=[dict(message=dict(content=json.dumps(job['expected'])),finish_reason='stop')])
                if changed: raw=changed(len(self.called),raw)
                log('transport-001',dict(transport_status='RESPONSE_RECEIVED',response_text=json.dumps(raw)))
                return raw
            return transport
        return factory

    def run_script(self, changed=None):
        return cal.run(self.plan,counter=Counter(),transport_factory=self.scripted(changed),
            output=self.root/'run',mode='SCRIPTED_NOT_MODEL',clock=lambda:self.now)

    def test_fixed_pair_equality_order_and_no_task_leakage(self):
        cal.validate(self.plan)
        self.assertEqual(self.plan,cal.build_plan())
        first={'original':0,'separated':0}
        for i in range(0,16,2):
            a,b=self.plan['jobs'][i:i+2]; first[a['arm']]+=1
            self.assertEqual(a['case_id'],b['case_id'])
            self.assertEqual(a['expected'],b['expected'])
            pa,pb=deepcopy(a['payload']),deepcopy(b['payload'])
            self.assertNotEqual(pa['messages'][0],pb['messages'][0])
            pa['messages'].pop(0);pb['messages'].pop(0)
            self.assertEqual(pa,pb)
            self.assertEqual(parse_action(json.dumps(a['expected'])),a['expected'])
        self.assertEqual(first,{'original':4,'separated':4})
        self.assertEqual(self.plan['research_tasks'],0)
        original=next(j for j in self.plan['jobs'] if j['arm']=='original')
        self.assertEqual(original['payload']['messages'][0]['content'],cal.PREFIX+ACTION_INSTRUCTION)

    def test_tampered_order_prompt_expected_or_extra_slot_rejected(self):
        for mode in ('order','prompt','expected','extra'):
            p=deepcopy(self.plan)
            if mode=='order':p['jobs'].reverse()
            elif mode=='prompt':p['jobs'][0]['payload']['messages'][0]['content']+=' changed'
            elif mode=='expected':p['jobs'][0]['expected']={'kind':'answer','text':'invented'}
            else:p['jobs'].append(deepcopy(p['jobs'][0]))
            with self.assertRaises(ValueError):cal.validate(p)

    def test_valid_whitespace_unicode_and_special_characters(self):
        for _,kind,field,value,_ in cal.CASES:
            expected=dict(kind=kind,**{field:value})
            self.assertTrue(cal.score('\n'+json.dumps(expected)+'\t','stop',expected)['exact_encoding'])

    def test_invalid_and_valid_but_wrong_remain_distinct(self):
        expected=dict(kind='answer',text='2');good=json.dumps(expected)
        for raw in (good+good,good+"'}",'```'+good+'```','', '[1]'):
            self.assertFalse(cal.score(raw,'stop',expected)['admitted'])
        wrong=cal.score('{"kind":"answer","text":"3"}','stop',expected)
        self.assertTrue(wrong['admitted']);self.assertFalse(wrong['exact_encoding'])
        self.assertFalse(cal.score(good,'length',expected)['admitted'])

    def test_full_scripted_batch_has_sixteen_slots_no_tools(self):
        r=self.run_script()
        self.assertEqual(len(self.called),16);self.assertTrue(r['readout']['complete'])
        self.assertEqual(r['SQL_executions'],0);self.assertEqual(r['model_calls'],0)
        self.assertEqual(r['readout']['totals']['original']['exact_encoding'],8)
        self.assertEqual(r['readout']['totals']['separated']['exact_encoding'],8)
        self.assertEqual(len(r['readout']['paired_descriptive_differences']),8)

    def test_malformed_output_continues_fixed_slots_no_retry(self):
        def mutate(index,raw):
            if index==1:raw['choices'][0]['message']['content']*=2
            return raw
        r=self.run_script(mutate)
        self.assertEqual(len(self.called),16);self.assertEqual(len(set(self.called)),16)
        self.assertEqual(r['rows'][0]['status'],'OBSERVED')
        self.assertFalse(r['rows'][0]['score']['admitted'])

    def test_transport_failure_stops_and_preserves_unstarted(self):
        def mutate(index,raw):
            if index==2:raise TimeoutError('synthetic timeout')
            return raw
        r=self.run_script(mutate)
        self.assertEqual(len(self.called),2)
        self.assertEqual(r['rows'][1]['status'],'FAILED')
        self.assertTrue(all(x['status']=='NOT_STARTED' for x in r['rows'][2:]))
        self.assertEqual(r['readout']['paired_descriptive_differences'],[])
        self.assertGreater(r['cost_guard']['uncertain_reserved_micro_cny'],0)

    def test_provider_count_mismatch_and_late_response_stop(self):
        def mutate(index,raw):
            raw['usage']['prompt_tokens']=101
            return raw
        r=self.run_script(mutate)
        self.assertEqual(len(self.called),1);self.assertEqual(r['rows'][0]['status'],'FAILED')
        self.assertTrue((self.root/'run'/self.called[0]/'action.provider.json').is_file())

    def test_late_response_is_saved_not_classified(self):
        def mutate(index,raw):self.now=601;return raw
        r=self.run_script(mutate)
        self.assertEqual(len(self.called),1);self.assertNotIn('score',r['rows'][0])

    def test_overwrite_and_missing_slots_rejected(self):
        r=self.run_script()
        with self.assertRaises(FileExistsError):self.run_script()
        with self.assertRaises(ValueError):cal.summarize(r['rows'][:-1])
        self.assertEqual(len(self.called),16)

    def test_wrong_approval_or_mutable_launcher_before_key(self):
        for amount in (None,True,.10,.20,'0.12',.12):
            with patch.object(launch.getpass,'getpass') as key:
                with self.assertRaises(ValueError):launch.execute(self.root,'0'*64,amount)
                key.assert_not_called()

    def test_old_date_or_existing_claim_before_key(self):
        for mode in ('date','claim'):
            if mode=='claim':(self.root/'execution_claim.json').touch()
            with patch.object(launch,'frozen'),patch.object(launch,'verify'), \
                 patch.object(launch,'day',return_value='1900-01-01' if mode=='date' else launch.PRICE_DATE), \
                 patch.object(launch.getpass,'getpass') as key:
                with self.assertRaises(ValueError):launch.execute(self.root,'0'*64,.12)
                key.assert_not_called()

    def test_finished_audit_recomputes_all_synthetic_scores(self):
        # The OFFICIAL-shaped fixture below is entirely injected, not a real run.
        result=cal.run(self.plan,counter=Counter(),transport_factory=self.scripted(),
            output=self.root/'experiment',mode='OFFICIAL_INTERFACE_CALIBRATION',clock=lambda:0)
        launch.audit.save_new(self.root/'execution_claim.json',dict(manifest_sha256='fixture',approved_cny=.12))
        launch.audit.save_new(self.root/'launch_outcome.json',
            {k:result[k] for k in ('status','dispatch_attempts')})
        with patch.object(launch,'frozen'),patch.object(launch,'verify',
                return_value=({'input_tokens':[100]*16},self.plan,Counter())):
            report=launch.analyze(self.root,'fixture')
        self.assertEqual(report['status'],'SEALED_ENCODING_CALIBRATION_REPLAY_VERIFIED')
        self.assertEqual(report['model_calls'],0)
        self.assertEqual(report['readout'],result['readout'])

    def test_launch_key_only_in_owned_worker_packet(self):
        with patch.object(launch,'frozen'),patch.object(launch,'verify'), \
             patch.object(launch,'day',return_value=launch.PRICE_DATE), \
             patch.object(launch.getpass,'getpass',return_value='synthetic-test-secret'), \
             patch.object(launch,'owned_worker',return_value=dict(status='WORKER_TIMEOUT',
                     automatic_retry=False,billing_unknown=True)) as worker:
            result=launch.execute(self.root,'fixture',.12)
        args=worker.call_args.args
        self.assertNotIn('synthetic-test-secret',str(args[0]))
        self.assertEqual(json.loads(args[1])['key'],'synthetic-test-secret')
        self.assertEqual(result['status'],'WORKER_TIMEOUT')
        for path in self.root.rglob('*.json'):
            self.assertNotIn('synthetic-test-secret',path.read_text('utf-8'))


if __name__=='__main__':unittest.main()
