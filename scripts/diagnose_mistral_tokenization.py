"""Zero-generation, owned-service tokenization audit of the failed calibration.

No completion endpoint, no retry of the failed generation, no model scoring.
Compare add_special false/true using the exact frozen original public prompts.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_local_separation import save,sha,utc
from scripts.run_reuse_development import verify_prepared,verify_index
from runtime.reuse_mistral_transport import adapt_messages,verify_mistral_assets,verify_rendered_instructions
from runtime.reuse_local_transport import OwnedLocal
from runtime.reuse_protocol import messages


class TokenizationOnly(OwnedLocal):
    def request(self,path,payload=None,timeout=60):
        if path not in ('/health','/v1/models','/props','/apply-template','/tokenize'):
            raise ValueError('Generation and other endpoints prohibited in this diagnostic')
        return super().request(path,payload,timeout)


def run(prepared,manifest,output):
    prepared,output=prepared.resolve(),output.resolve()
    if not output.is_relative_to(ROOT/'runtime/runs'):raise ValueError('Local run directory required')
    verify_prepared(prepared,manifest);verify_index(prepared/'batch-00')
    terminal=json.loads((prepared/'batch-00/terminal.json').read_bytes())
    if terminal['status']!='FAILED' or not terminal['owned_server_stopped']:
        raise ValueError('Requires preserved terminal failed calibration, no live inference')
    conf=json.loads((prepared/'config.json').read_bytes());conf['server_seconds_limit']=120
    output.mkdir(parents=True,exist_ok=False)
    sources=('scripts/diagnose_mistral_tokenization.py','scripts/run_reuse_development.py',
             'scripts/run_local_separation.py','scripts/inspect_reuse_gguf.py',
             'runtime/reuse_local_transport.py','runtime/reuse_mistral_transport.py',
             'runtime/reuse_protocol.py','runtime/reuse_separation.py','runtime/reuse_scenarios.py')
    for name in sources:
        dest=output/'source'/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,dest)
    save(output/'plan.json',{'utc':utc(),'prepared_manifest':manifest,'generation_limit':0,
        'endpoints':list(('/health','/v1/models','/props','/apply-template','/tokenize')),
        'server_seconds_limit':120,'purpose':'Explain token mismatch without replacing any model response',
        'source_hashes':{n:sha(ROOT/n) for n in sources}})
    save(output/'config.json',conf)
    local=TokenizationOnly(conf,output);status='FAILED';rows=[];started=time.perf_counter()
    try:
        verify_mistral_assets(conf,json.loads((prepared/'model_spec.json').read_bytes()),
            json.loads((prepared/'asset_spec.json').read_bytes()),output)
        local.start()
        by_case={c['case_id']:c for c in json.loads((prepared/'cases.public.json').read_bytes())}
        jobs=json.loads((prepared/'jobs.json').read_bytes())
        previous={(p['job_id'],p['candidate']):p for p in
                  json.loads((prepared/'batch-00/token_preflight.json').read_bytes())}
        for job in jobs:
            for candidate in ('applicable','inapplicable','unknown','invalid_or_missing') if job['method']=='M4' else (None,):
                original=messages(by_case[job['case_id']],job,candidate)
                prompt=local.request('/apply-template',{'messages':adapt_messages(original)})['prompt']
                verify_rendered_instructions(original,prompt)
                false=local.request('/tokenize',{'content':prompt,'add_special':False,'parse_special':True})['tokens']
                true=local.request('/tokenize',{'content':prompt,'add_special':True,'parse_special':True})['tokens']
                if len(false)!=previous[job['job_id'],candidate]['prompt_tokens']:
                    raise ValueError('Original preflight tokenization not reproduced')
                if true!=[1]+false:raise ValueError('Difference is not exactly the metadata BOS token')
                if len(true)+256+64>4096:raise ValueError('Corrected context bound exceeded')
                rows.append({'job_id':job['job_id'],'candidate':candidate,
                    'without_special':len(false),'with_special':len(true),
                    'extra_token_id':1,'entire_instruction_preserved':True})
        raw=json.loads((prepared/'batch-00/000.response.json').read_bytes())
        dispatch=json.loads((prepared/'batch-00/000.dispatch.json').read_bytes())
        first=next(r for r in rows if r['job_id']==dispatch['job_id'] and r['candidate'] is None)
        if first['with_special']!=raw['usage']['prompt_tokens']:
            raise ValueError('Corrected count does not match preserved actual usage')
        save(output/'tokenization.json',rows)
        save(output/'diagnosis.json',{'status':'BOS_ACCOUNTING_MISMATCH_REPRODUCED_NO_GENERATION',
            'variants_checked':len(rows),'metadata_add_bos':True,'bos_id':1,
            'preserved_response_reported_prompt_tokens':raw['usage']['prompt_tokens'],
            'original_preflight':first['without_special'],'matching_add_special_true':first['with_special'],
            'all_true_tokens_equal_one_BOS_plus_false':True,'model_prompts_changed':False,
            'existing_failed_record_modified':False,'new_model_generations':0,
            'runtime_reference':'https://github.com/ggml-org/llama.cpp/blob/b11850/tools/server/server-context.cpp#L4765',
            'explanation':'Chat completion tokenization adds model-special tokens; original /tokenize preflight disabled them. '
                          'Future preflight must use add_special=true, not an arbitrary numeric +1 tolerance.'})
        status='COMPLETED'
    except BaseException as exc:
        save(output/'failure.json',{'type':type(exc).__name__,'detail':str(exc),'utc':utc()})
    finally:
        stopped=local.stop()
        result={'status':status,'new_model_generations':0,'utc':utc(),**stopped,
                'seconds_including_preflight':time.perf_counter()-started}
        save(output/'terminal.json',result)
        save(output/'sha256.json',{str(p.relative_to(output)).replace('\\','/'):sha(p)
            for p in output.rglob('*') if p.is_file()})
    print(json.dumps(result,indent=2));return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared',type=Path,required=True);p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if run(a.prepared,a.manifest_sha256,a.output)['status']!='COMPLETED':raise SystemExit(1)
