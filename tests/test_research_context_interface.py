import copy
import unittest

from runtime.cache_intent_staged_loop import ACTION_INSTRUCTION
from runtime.action_encoding_calibration import SEPARATED
from runtime.research_context_interface import CONTEXT_IDS, build_plan, score_format


def requests():
    return {ident: dict(model='deepseek-flash', thinking={'type':'disabled'}, temperature=0,
                       max_tokens=512, stream=False, response_format={'type':'json_object'},
                       messages=[dict(role='system', content='Background\n'+ACTION_INSTRUCTION),
                                 dict(role='user', content='Fixture task '+ident)]) for ident in CONTEXT_IDS}


class Tests(unittest.TestCase):
    def test_all_contexts_pairing_and_no_mutation(self):
        source = requests(); before = copy.deepcopy(source)
        plan = build_plan(source)
        self.assertEqual(source, before)
        self.assertEqual(plan, build_plan(source))
        self.assertEqual(len(plan['jobs']), 16)
        self.assertFalse(plan['inference_authorized'])
        first = []
        for i in range(0, 16, 2):
            pair = plan['jobs'][i:i+2]
            first.append(pair[0]['arm'])
            by_arm = {j['arm']: j for j in pair}
            ident = pair[0]['context_id']
            self.assertEqual(by_arm['original']['payload'], source[ident])
            variant = copy.deepcopy(by_arm['separated']['payload'])
            variant['messages'][0]['content'] = variant['messages'][0]['content'].replace(SEPARATED, ACTION_INSTRUCTION)
            self.assertEqual(variant, source[ident])
            self.assertNotIn('expected', pair[0])
        self.assertEqual(first.count('original'), 4)
        self.assertEqual(first.count('separated'), 4)

    def test_no_failed_cell_only_selection(self):
        source = requests(); source.pop('positive-cell-1')
        with self.assertRaises(ValueError): build_plan(source)

    def test_missing_or_repeated_instruction_rejected(self):
        for text in ('Missing', ACTION_INSTRUCTION+ACTION_INSTRUCTION):
            source = requests(); source[CONTEXT_IDS[0]]['messages'][0]['content'] = text
            with self.assertRaises(ValueError): build_plan(source)

    def test_user_instruction_collision_rejected(self):
        source = requests(); source[CONTEXT_IDS[0]]['messages'][1]['content'] = ACTION_INSTRUCTION
        with self.assertRaises(ValueError): build_plan(source)

    def test_full_response_strict_no_semantic_grade(self):
        valid = '{"kind":"answer","text":"not evaluated as correct"}'
        self.assertTrue(score_format(valid, 'stop')['admitted'])
        self.assertFalse(score_format(valid, 'length')['admitted'])
        for text in (valid+valid, valid+"'}", '```json\n'+valid+'\n```', '{"kind":"run_sql"}'):
            self.assertFalse(score_format(text, 'stop')['admitted'])
        self.assertFalse(score_format(valid, 'stop')['task_correctness_assessed'])


if __name__ == '__main__':
    unittest.main()
