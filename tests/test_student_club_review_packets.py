"""Synthetic histories only; no human ratings, model calls or database access."""
from copy import deepcopy
import json
import unittest

from runtime.student_club_definition_history import DefinitionHistory
from runtime.student_club_definition_replay import replay,IncompleteHistory
from runtime.student_club_diagnostic_design import build
from runtime.student_club_review_packets import prepare
from tests.test_student_club_definition_history import response
from tests.test_student_club_diagnostic_design import common
from tests import test_student_club_native_batch as batch_fixture
from runtime.student_club_definition_replay import event_records


def history(c,job,*,invalid=False):
    records={}
    h=DefinitionHistory(c,job,lambda name,value:records.update({name:deepcopy(value)}))
    h.begin()
    if invalid:
        try:h.accept('malformed synthetic response')
        except ValueError:pass
        return records
    if job['protocol']!='ordinary':
        h.accept(response('record_definition','  ORIGINAL definition\n','d'));h.begin()
    h.accept(response('run_sql','SELECT 1','sql1'));h.observe('sql1','{"tool_observation":{"rows":[1]}}')
    h.begin()
    if job['protocol']!='ordinary':
        h.accept(response('revise_definition','REVISED definition','rev'));h.begin()
        h.accept(response('run_sql','SELECT 2','sql2'));h.observe('sql2','{"tool_observation":{"rows":[2]}}')
        h.begin()
    h.accept(response('answer','FINAL_ANSWER_SENTINEL','end'))
    return records


class Tests(unittest.TestCase):
    def setUp(self):
        self.c=common();self.jobs=build(self.c)['jobs']
        self.histories={j['id']:history(self.c,j) for j in self.jobs}

    def test_exact_definitions_and_revised_temporal_links_without_grading(self):
        packet=prepare(self.c,self.histories,masking_seed='private-test-seed-001')
        for reviewer in packet['reviewers']:
            self.assertEqual(len(reviewer['stage_a']['items']),8)
            self.assertEqual(len(reviewer['stage_b']['items']),16)
            for item in reviewer['stage_a']['items']:
                self.assertEqual(item['definition'],'  ORIGINAL definition\n')
                self.assertIsNone(item['assessment']);self.assertIsNone(item['reason'])
            for item in reviewer['stage_b']['items']:
                if item['issued_sql']=='SELECT 2':
                    self.assertEqual(item['preceding_definition'],'REVISED definition')
                    self.assertEqual(item['definition_timing'],'post_result')
                    self.assertTrue(item['definition_is_revision'])
                self.assertIsNone(item['program_vs_definition'])
                self.assertFalse(item['sql_execution_verified'])
        self.assertEqual(len(packet['PRIVATE_coverage']),12)
        self.assertEqual(packet['semantic_labels_generated'],0)
        self.assertFalse(packet['ordinary_contract_inferred_from_sql'])

    def test_stage_a_does_not_show_condition_sql_answers_or_operator_mapping(self):
        packet=prepare(self.c,self.histories,masking_seed='private-test-seed-001')
        for reviewer in packet['reviewers']:
            text=str(reviewer['stage_a'])
            for value in ('SELECT 1','FINAL_ANSWER_SENTINEL','HISTORY_REQUEST_SENTINEL','catalog_first','request_first','read_cost','job_id'):
                self.assertNotIn(value,text)
            self.assertTrue(reviewer['stage_b']['release_only_after_both_stage_a_locked'])
        a,b=packet['reviewers']
        self.assertEqual({x['item_id'] for x in a['stage_a']['items']},{x['item_id'] for x in b['stage_a']['items']})
        self.assertNotEqual(a['stage_a']['items'],b['stage_a']['items'])

    def test_invalid_output_retained_in_coverage_not_filled_with_reference(self):
        job=next(j for j in self.jobs if j['protocol']=='catalog_first')
        self.histories[job['id']]=history(self.c,job,invalid=True)
        packet=prepare(self.c,self.histories,masking_seed='private-test-seed-001')
        self.assertEqual(len(packet['reviewers'][0]['stage_a']['items']),7)
        row=next(x for x in packet['PRIVATE_coverage'] if x['job_id']==job['id'])
        self.assertEqual(row['state'],'INVALID_RESPONSE')
        self.assertEqual(row['interpretation_status'],'UNASSESSED')
        self.assertTrue(row['missing_link_is_not_negative'])

    def test_missing_condition_or_incomplete_history_refused(self):
        partial=deepcopy(self.histories);partial.pop(next(iter(partial)))
        with self.assertRaises(ValueError):prepare(self.c,partial,masking_seed='private-test-seed-001')
        job=self.jobs[0];records={}
        h=DefinitionHistory(self.c,job,lambda name,value:records.update({name:value}))
        h.begin()
        with self.assertRaises(IncompleteHistory):replay(records,self.c,job)
        h.fail('synthetic failure')
        with self.assertRaises(IncompleteHistory):replay(records,self.c,job)

    def test_fabricated_definition_relinked_action_and_removed_event_rejected(self):
        job=next(j for j in self.jobs if j['protocol']=='catalog_first');original=self.histories[job['id']]
        for kind in ('definition','link','remove'):
            records=deepcopy(original)
            if kind=='definition':
                next(v for v in records.values() if v['kind']=='definition_recorded')['value']['text']='operator-corrected'
            elif kind=='link':
                next(v for v in records.values() if v['kind']=='action_recorded')['value']['definition_event']='e9999'
            else:records.pop('e0002')
            with self.assertRaises(ValueError):replay(records,self.c,job)

    def test_complete_ordinary_trace_never_generates_definitions(self):
        job=next(j for j in self.jobs if j['protocol']=='ordinary')
        result=replay(self.histories[job['id']],self.c,job)
        self.assertEqual(result['state'],'TERMINAL')
        self.assertFalse(any(e['kind']=='definition_recorded' for e in result['events']))
        self.assertFalse(result['archive_authentication_verified'])
        self.assertFalse(result['api_accounting_verified'])


class BatchIntegration(unittest.TestCase):
    setUp=batch_fixture.Tests.setUp
    inputs=batch_fixture.Tests.inputs
    factory=batch_fixture.Tests.factory
    execute=batch_fixture.Tests.execute

    def test_real_loop_journals_replay_without_model_or_human_labels(self):
        self.inputs();result=self.execute()
        histories={}
        for row in result['rows']:
            directory=self.root/'out'/row['id']/'loop'
            records={p.stem:json.loads(p.read_text('utf-8')) for p in directory.glob('*.json')}
            histories[row['id']]=event_records(records)
        packet=prepare(self.c,histories,masking_seed='integration-private-seed')
        self.assertEqual(len(packet['PRIVATE_coverage']),12)
        self.assertEqual(len(packet['reviewers'][0]['stage_a']['items']),8)
        self.assertEqual(len(packet['reviewers'][0]['stage_b']['items']),8)
        self.assertEqual(packet['semantic_labels_generated'],0)
        self.assertTrue(packet['caller_must_authenticate_archive_and_provider_and_tools'])


if __name__=='__main__':unittest.main()
