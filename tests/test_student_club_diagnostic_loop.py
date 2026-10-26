"""Scripted end-to-end software checks on a synthetic database, never an API."""
from copy import deepcopy
import json
import unittest

from runtime import bird_readonly_audit as audit
from runtime import student_club_readonly as scoped
from runtime.cache_intent_journal import Journal, verify
from runtime.cache_route_symmetric_loop import ObservationBound
from runtime.student_club_diagnostic_design import build
from runtime.student_club_diagnostic_loop import run
from scripts.check_student_club_reference import ENTITY_H, ENTITY_C
from tests.test_student_club_definition_history import response
from tests.test_student_club_diagnostic_design import common
from tests import test_student_club_readonly as scope_fixture


class Tests(unittest.TestCase):
    setUp = scope_fixture.Tests.setUp

    def setup_run(self, protocol='catalog_first', available=True, price=1, label='one'):
        c = common() | {'database_sha256': self.before, 'history_sql': ENTITY_H}
        job = next(j for j in build(c)['jobs'] if (j['protocol'],j['available'],j['price']) == (protocol,available,price))
        cache = scoped.execute(self.db, ENTITY_H)
        cache['sql_sha256'] = audit.digest(ENTITY_H)
        session = scoped.ToolSession(database_path=self.db, database_sha256=self.before,
            history_sql=ENTITY_H, cached_result=cache, input_receipt={'synthetic':True},
            output=self.root/(label+'-tools'), materialized_available=available, cache_read_cost=price)
        journal = Journal(self.root/(label+'-loop'))
        return c,job,session,journal

    def finish(self, session, journal):
        closed = session.close()
        journal.seal()
        verify(session.output); verify(journal.directory)
        self.assertEqual(audit.hash_file(self.db),self.before)
        return closed

    def records(self, journal):
        return [json.loads(p.read_text('utf-8')) for p in sorted(journal.directory.glob('e*.json'))]

    def test_all_cells_reuse_revise_recompute_with_exact_costs(self):
        callbacks = tools = 0
        for protocol in ('ordinary','request_first','catalog_first'):
            for available,price in ((False,1),(False,20),(True,1),(True,20)):
                label=f'{protocol}-{available}-{price}'
                c,j,s,log = self.setup_run(protocol,available,price,label)
                outputs = []
                if protocol != 'ordinary': outputs.append(response('record_definition','SYNTHETIC initial description','d'))
                outputs.append(response('read_cache','v000','r'))
                if protocol != 'ordinary': outputs.append(response('revise_definition','SYNTHETIC revised description','rev'))
                outputs += [response('run_sql',ENTITY_C,'sql'),response('answer','1','answer')]
                seen=[]
                def generate(request):
                    seen.append(deepcopy(request))
                    return outputs[len(seen)-1]
                result=run(c,j,s,generate,log,ObservationBound(lambda _:100),deadline=500,clock=lambda:0)
                self.assertEqual(result['status'],'ANSWERED')
                self.assertFalse(result['semantic_labels_generated'])
                self.assertEqual(result['tool_attempts'],2)
                callbacks += result['generation_callback_attempts']; tools += result['tool_attempts']
                closed=self.finish(s,log)
                self.assertEqual(closed['spent_declared_units'],10+(price if available else 0))
                tool_messages=[m for req in seen for m in req['messages'] if m['role']=='tool']
                cache_messages=[m for m in tool_messages if m['tool_call_id']=='r']
                self.assertTrue(cache_messages)
                for m in cache_messages:
                    observed=json.loads(m['content'])['tool_observation']
                    self.assertEqual(observed['status'],'EXECUTED' if available else 'CACHE_UNAVAILABLE')
                    if not available:self.assertNotIn('rows',observed)
                self.assertNotIn('PRIVATE_',str(seen))
                records=self.records(log)
                definitions=[r for r in records if r['kind']=='definition_recorded']
                actions=[r for r in records if r['kind']=='action_recorded']
                if protocol == 'ordinary':
                    self.assertEqual(definitions,[])
                    self.assertTrue(all(a['value']['definition_event'] is None for a in actions))
                else:
                    self.assertEqual(definitions[-1]['value']['observation'],'post_result')
                    self.assertEqual(actions[0]['value']['definition_event'],definitions[0]['event_id'])
                    self.assertEqual(actions[1]['value']['definition_event'],definitions[1]['event_id'])
        self.assertEqual((callbacks,tools),(52,24))

    def test_invalid_output_and_callback_failure_preserved_without_retry(self):
        for case in ('invalid','callback'):
            c,j,s,log=self.setup_run(label=case)
            seen=[]
            def generate(req):
                seen.append(req)
                if case=='callback': raise OSError('synthetic transport failure')
                return 'invalid JSON'
            if case=='callback':
                with self.assertRaises(OSError):run(c,j,s,generate,log,ObservationBound(lambda _:100),deadline=500,clock=lambda:0)
                self.assertTrue((log.directory/'g001.failure.json').exists())
            else:
                result=run(c,j,s,generate,log,ObservationBound(lambda _:100),deadline=500,clock=lambda:0)
                self.assertEqual(result['status'],'INVALID_ACTION_RESPONSE')
                self.assertIn('invalid JSON',(log.directory/'g001.response.json').read_text())
            self.assertEqual(len(seen),1);self.assertEqual(s.attempts,0)
            self.finish(s,log)

    def test_late_response_retained_without_tool_dispatch(self):
        c,j,s,log=self.setup_run(protocol='ordinary')
        now=[0]
        def late(req):
            now[0]=501
            return response('run_sql',ENTITY_C)
        with self.assertRaises(TimeoutError):
            run(c,j,s,late,log,ObservationBound(lambda _:100),deadline=500,clock=lambda:now[0])
        self.assertTrue((log.directory/'g001.response.json').exists())
        self.assertEqual(s.attempts,0)
        self.finish(s,log)

    def test_state_drift_and_unavailable_leak_stop_before_exposure(self):
        for case in ('drift','leak'):
            c,j,s,log=self.setup_run(protocol='ordinary',available=False,label=case)
            original=s.call
            def corrupt(tool,args):
                out=original(tool,args)
                if case=='drift':s.balance-=1
                else:out['rows']=[[['integer','999']]]
                return out
            s.call=corrupt
            with self.assertRaises(ValueError):
                run(c,j,s,lambda req:response('read_cache','v000'),log,ObservationBound(lambda _:100),deadline=500,clock=lambda:0)
            self.assertTrue((log.directory/'t001.response.json').exists())
            self.assertFalse((log.directory/'t001.exposure.json').exists())
            self.assertFalse(any(r['kind']=='tool_observation' for r in self.records(log)))
            # Closure seals bookkeeping; it does not certify a valid trajectory.
            # Preserve the injected mismatch instead of silently repairing it.
            if case=='drift':
                closed=self.finish(s,log)
                self.assertEqual(closed['spent_declared_units'],1)
                self.assertTrue((log.directory/'failure.json').exists())
            else:self.finish(s,log)

    def test_oversize_is_explicit_not_partial_and_raw_remains(self):
        c,j,s,log=self.setup_run(protocol='ordinary')
        outputs=iter([response('read_cache','v000'),response('abstain','oversize','end')])
        def counter(messages):
            return 2048 if '"rows"' in messages[-1]['content'] else 100
        result=run(c,j,s,lambda req:next(outputs),log,ObservationBound(counter),deadline=500,clock=lambda:0)
        self.assertEqual(result['status'],'ABSTAINED')
        raw=json.loads((log.directory/'t001.response.json').read_text())['observation']
        exposed=json.loads((log.directory/'t001.exposure.json').read_text())['observation']
        self.assertIn('rows',raw);self.assertNotIn('rows',exposed)
        self.assertEqual(exposed['status'],'OBSERVATION_TOO_LARGE')
        self.assertEqual(exposed['charged_units'],1)
        self.finish(s,log)

    def test_wrong_history_binding_rejected_before_generation(self):
        c,j,s,log=self.setup_run()
        s.cache['sql_sha256']='0'*64
        def never(req):raise AssertionError('Must not generate')
        with self.assertRaises(ValueError):
            run(c,j,s,never,log,ObservationBound(lambda _:100),deadline=500,clock=lambda:0)
        self.assertEqual(s.attempts,0)
        self.finish(s,log)


if __name__=='__main__':unittest.main()
