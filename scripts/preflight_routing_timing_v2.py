"""Read-only model-tokenizer context check; no inference calls."""
import argparse
import json
from pathlib import Path
from transformers import AutoTokenizer
from runtime import routing_timing_v2 as m

def main():
    p=argparse.ArgumentParser();p.add_argument('--native-config',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    cfg=json.loads(Path(a.native_config).read_text());t=AutoTokenizer.from_pretrained(cfg['model_path'],local_files_only=True)
    initial=[dict(format_valid=True,assessment=dict(answer=k%2,p_state_1=.2+k*.2,citations=[]),violation=None) for k in range(3)]
    sizes=[]
    def record(prompt):sizes.append(len(t.apply_chat_template(prompt,tokenize=True,add_generation_prompt=True)))
    for i in range(12):
        for f,r in m.interface.prior.case_order(m.DESIGN,i):
            for a in range(3):record(m.initial_prompt(m.DESIGN,i,f,r,a)[0])
            for method in m.METHODS:
                for stage in (1,2):
                    for a in range(3):record(m.stage_prompt(m.DESIGN,i,f,r,method,a,stage,initial[a],initial)[0])
                record(m.adjudicator_prompt(initial)[0])
            record(m.union_prompt(m.DESIGN,i,f,r)[0]);record(m.adjudicator_prompt(initial[:1])[0])
            record(m.adjudicator_prompt(initial)[0])
            for a in range(3):record(m.union_prompt(m.DESIGN,i,f,r)[0])
            record(m.adjudicator_prompt(initial)[0]);record(m.union_prompt(m.DESIGN,i,f,r,False)[0])
    assert len(sizes)==m.DESIGN['calls']
    result=dict(prompts=len(sizes),min_fixture_prompt=min(sizes),max_fixture_prompt=max(sizes),artifact_reserve=2048,
                output_budget=512,context=cfg['server_settings_reported']['max_model_len'],scope='Tokenizer-only software fixtures; no model inference')
    assert max(sizes)+2048+512<=result['context'],'Context reserve insufficient'
    with Path(a.output).open('x') as handle:json.dump(result,handle,indent=2)
    print(json.dumps(result))

if __name__=='__main__':main()
