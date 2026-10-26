import unittest
from runtime.reuse_mistral_transport import adapt_messages,verify_rendered_instructions
from runtime.reuse_protocol import messages,schedule,strip_price
from runtime.reuse_scenarios import build_suite


class Tests(unittest.TestCase):
    def test_preserve_full_instruction_and_data(self):
        case=build_suite('development')[0]
        for method in ('M1','M2','M3','M4'):
            original=messages(case,{'method':method,'price':1},'inapplicable')
            adapted=adapt_messages(original)
            self.assertEqual(adapted[0]['role'],'user')
            self.assertEqual(adapted[0]['content'],original[0]['content']+'\n\n'+original[1]['content'])
            verify_rendered_instructions(original,'<s>[INST] '+adapted[0]['content']+' [/INST]')
            with self.assertRaises(ValueError):verify_rendered_instructions(original,original[1]['content'])

    def test_price_removal_still_matches_blind_input(self):
        case=build_suite('development')[0]
        priced=messages(case,{'method':'M2','price':20})
        hidden=messages(case,{'method':'M3','price':None})
        self.assertEqual(adapt_messages(strip_price(priced)),adapt_messages(hidden))

    def test_other_conversation_shapes_fail_closed(self):
        for value in ([],[{'role':'user','content':'x'}],
                      [{'role':'system','content':'s'},{'role':'assistant','content':'x'}]):
            with self.assertRaises(ValueError):adapt_messages(value)

    def test_fixed_calibration_subset_and_limits(self):
        from scripts.run_reuse_development import calibration_cases,validate_config,transport_messages
        cases=calibration_cases(build_suite('development'))
        self.assertEqual(len(cases),4)
        self.assertEqual(len(schedule(cases)),40)
        self.assertEqual(sorted({c['base_id'] for c in cases}),
                         sorted({c['base_id'] for c in build_suite('development')})[:2])
        with self.assertRaises(ValueError):calibration_cases(build_suite('confirmation_candidate'))
        conf={'scope':'DEVELOPMENT_INTERFACE_ONLY','planned_calls':40,'batch_sizes':[40],
              'server_seconds_limit':600,'max_tokens':256,'context_size':4096,
              'max_attempts_new_goal_before':2432,'service_seconds_new_goal_before':800.7,
              'port':18090,'seed':20261027,'model_family':'mistral',
              'model_file':'Mistral-7B-Instruct-v0.3-Q5_K_M.gguf',
              'model_alias':'local-mistral7b-v03-q5','runtime_assets':'test-fixture-not-live'}
        validate_config(conf)
        for changes in ({'planned_calls':41},{'model_family':'other'},{'runtime_assets':None},
                        {'model_file':'other.gguf'},{'scope':'CONFIRMATION_ONLY'}):
            with self.assertRaises(ValueError):validate_config(dict(conf,**changes))
        original=messages(cases[0],{'method':'M1','price':1})
        self.assertEqual(transport_messages(conf,original),adapt_messages(original))
        self.assertEqual(transport_messages({'model_family':'qwen'},original),original)


if __name__=='__main__':unittest.main()
