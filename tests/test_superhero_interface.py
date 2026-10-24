import json
import unittest
from runtime import cache_action_preview as action
from runtime import cache_intent_recovery_preview as elicited
from tests.test_cache_intent_prompts import inputs
from scripts.audit_superhero_interface import verify_pair


class Tests(unittest.TestCase):
    def test_factors_not_confused(self):
        pair = action.make_pair(**inputs())
        self.assertEqual(verify_pair(pair, 'materialized_available')[-1], 'materialized_available')
        with self.assertRaises(ValueError): verify_pair(pair, 'read_cost')
        pair = elicited.make_pair(**inputs())
        self.assertEqual(verify_pair(pair, 'read_cost')[-1], 'read_cost')

    def test_extra_change_rejected(self):
        pair = list(elicited.make_pair(**inputs()))
        packet = json.loads(pair[1]['messages'][1]['content'])
        packet['current_request'] += ' changed'
        pair[1]['messages'][1]['content'] = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        with self.assertRaises(ValueError): verify_pair(pair, 'read_cost')


if __name__ == '__main__': unittest.main()
