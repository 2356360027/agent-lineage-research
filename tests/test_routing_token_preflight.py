"""Tokenizer stand-in tests artifact writing only, not model context behavior."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch
from scripts import preflight_routing_timing_v2 as script

class RoutingTokenPreflightTests(unittest.TestCase):
    def test_arguments_survive_agent_loops_and_output_is_no_overwrite(self):
        tokenizer=MagicMock();tokenizer.apply_chat_template.return_value=list(range(123))
        auto=MagicMock();auto.from_pretrained.return_value=tokenizer
        dummy=([dict(role='system',content='SOFTWARE_FIXTURE_ONLY')],[])
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'native.json';output=Path(tmp)/'check.json'
            config.write_text(json.dumps(dict(model_path='/fixture',server_settings_reported=dict(max_model_len=4096))))
            with patch.dict('sys.modules',{'transformers':SimpleNamespace(AutoTokenizer=auto)}),patch('sys.argv',['preflight','--native-config',str(config),'--output',str(output)]),patch.object(script.m,'initial_prompt',return_value=dummy),patch.object(script.m,'stage_prompt',return_value=dummy),patch.object(script.m,'union_prompt',return_value=dummy),patch.object(script.m,'adjudicator_prompt',return_value=dummy):
                script.main()
                data=json.loads(output.read_text());self.assertEqual(data['prompts'],2208)
                self.assertEqual(data['max_fixture_prompt'],123);self.assertEqual(tokenizer.apply_chat_template.call_count,2208)
                with self.assertRaises(FileExistsError):script.main()

if __name__=='__main__':unittest.main()
