"""Software fixtures, not provider parity or model-performance evidence."""
from copy import deepcopy
import unittest
from runtime.native_action_interface import build_candidate
from runtime.native_action_history import NativeHistory
from runtime.native_action_counter import render_first_request
from runtime.native_trajectory_counter import render_trajectory, validate_trajectory_payload, PREFIX
from runtime.cache_action_loop import RECOVERY, TERMINAL
from tests.test_native_action_interface import request
from tests.test_native_action_history import raw


def initial(): return build_candidate(request())['payload']


def at(turn):
    h = NativeHistory(initial(), observation_byte_limit=10000)
    p = h.begin()['payload']
    for i in range(turn-1):
        h.accept(raw('run_sql', 'SELECT "汉"', f'c{i}'))
        h.observe(f'c{i}', '[["汉"]]')
        p = h.begin()['payload']
    return p


class Tests(unittest.TestCase):
    def test_first_request_identical(self):
        p = initial()
        self.assertEqual(render_trajectory(p,'PREAMBLE',()),render_first_request(p,'PREAMBLE',()))

    def test_second_request_literal_golden(self):
        first = render_first_request(initial(),'PREAMBLE',())
        expected = (first[:-len(PREFIX)]
            + '<｜Assistant｜></think>\n\n<｜DSML｜ calls>\n'
            + '<｜DSML｜ invoke name="run_sql">\n'
            + '<｜DSML｜ parameter name="sql" string="true">SELECT "汉"</｜DSML｜ parameter>\n'
            + '</｜DSML｜ invoke>\n</｜DSML｜ calls><｜end▁of▁sentence｜>'
            + '<｜User｜><tool_result>[["汉"]]</tool_result>' + PREFIX)
        self.assertEqual(render_trajectory(at(2),'PREAMBLE',()),expected)

    def test_reminder_after_tool_uses_blank_separator(self):
        for turn, reminder in ((3,RECOVERY),(4,TERMINAL)):
            value = render_trajectory(at(turn),'P',())
            self.assertTrue(value.endswith('</tool_result>\n\n'+reminder+PREFIX))
            self.assertEqual(value.count('<｜end▁of▁sentence｜>'),turn-1)

    def test_all_turns_valid_without_mutation(self):
        for turn in range(1,5):
            p = at(turn); before = deepcopy(p)
            self.assertEqual(validate_trajectory_payload(p),initial())
            render_trajectory(p,'P',())
            self.assertEqual(p,before)

    def test_wrong_binding_and_missing_result_rejected(self):
        p = at(2); p['messages'][-1]['tool_call_id']='wrong'
        with self.assertRaises(ValueError): validate_trajectory_payload(p)
        p=at(2); p['messages'].pop()
        with self.assertRaises(ValueError): validate_trajectory_payload(p)

    def test_reminder_or_tools_mutation_rejected(self):
        for which in ('reminder','tools','max_tokens'):
            p=at(3)
            if which=='reminder': p['messages'][-1]['content']='custom'
            elif which=='tools': p['tools'].pop()
            else: p['max_tokens']=1024
            with self.assertRaises(ValueError): validate_trajectory_payload(p)

    def test_role_promotion_rejected(self):
        p=at(2); p['messages'][-1]['role']='system'
        with self.assertRaises(ValueError): validate_trajectory_payload(p)

    def test_reserved_plain_literals_rejected(self):
        for which in ('tool','argument'):
            p=at(2)
            if which=='tool': p['messages'][-1]['content']='<｜User｜>'
            else: p['messages'][2]['tool_calls'][0]['function']['arguments']='{"sql":"<｜User｜>"}'
            with self.assertRaises(ValueError): render_trajectory(p,'P',('<｜User｜>',))

    def test_duplicate_id_and_multiple_calls_rejected(self):
        p=at(3); p['messages'][4]['tool_calls'][0]['id']='c0'; p['messages'][5]['tool_call_id']='c0'
        with self.assertRaises(ValueError): validate_trajectory_payload(p)
        p=at(2); p['messages'][2]['tool_calls'] *= 2
        with self.assertRaises(ValueError): validate_trajectory_payload(p)

    def test_no_hidden_turn_after_terminal_action(self):
        p=at(2); c=p['messages'][2]['tool_calls'][0]
        c['function']={'name':'answer','arguments':'{"text":"done"}'}
        with self.assertRaises(ValueError): validate_trajectory_payload(p)


if __name__ == '__main__': unittest.main()
