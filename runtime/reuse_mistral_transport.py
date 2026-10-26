"""Explicit template adaptation for the pinned Mistral v0.3 GGUF.

This artifact's embedded template supports only alternating user/assistant
roles. The complete original system instruction is prepended verbatim to user
data. No method instructions are removed or rewritten. Not interchangeable
with the Qwen system-role transport; disclose this model-family difference.
"""
import hashlib
from pathlib import Path

from runtime.reuse_local_transport import verify_assets
from scripts.inspect_reuse_gguf import inspect
from scripts.run_local_separation import sha,save

MODEL_SHA='0374072546aaf1257d1e34610edb11b691730d8ef7d44b5629693a2dd8e8d472'
TEMPLATE_SHA='26a59556925c987317ce5291811ba3b7f32ec4c647c400c6cc7e3a9993007ba7'


def adapt_messages(messages):
    if len(messages)!=2 or [m.get('role') for m in messages]!=['system','user']:
        raise ValueError('Only the frozen single-task system/user pair is supported')
    if any(set(m)!= {'role','content'} or not isinstance(m['content'],str) for m in messages):
        raise ValueError('Unexpected message fields')
    return [{'role':'user','content':messages[0]['content']+'\n\n'+messages[1]['content']}]


def verify_mistral_assets(conf,model_spec,runtime_spec,output):
    path=Path(conf['assets'])/conf['model_file']
    if (model_spec['sha256']!=MODEL_SHA or path.name!=model_spec['filename']
        or path.stat().st_size!=model_spec['bytes'] or sha(path)!=MODEL_SHA):
        raise ValueError('Mistral pinned model mismatch')
    with path.open('rb') as f:metadata=inspect(f)
    template=metadata['metadata'].get('tokenizer.chat_template','')
    if hashlib.sha256(template.encode()).hexdigest()!=TEMPLATE_SHA:
        raise ValueError('Embedded Mistral template changed')
    runtime_conf=dict(conf,assets=conf['runtime_assets'])
    archives={'assets':[a for a in runtime_spec['assets'] if a['name'].endswith('.zip')]}
    verify_assets(runtime_conf,archives,output)
    save(Path(output)/'mistral_verified.json',{
        'model_sha256':MODEL_SHA,'model_bytes':model_spec['bytes'],
        'template_sha256':TEMPLATE_SHA,'embedded_template':template,
        'message_adaptation':'system + two newlines + user, verbatim, one user role',
        'third_party_quantization':True,'upstream_revision_used_by_quantizer':'NOT_ESTABLISHED'})


def verify_rendered_instructions(original_messages,rendered_prompt):
    combined=adapt_messages(original_messages)[0]['content']
    if combined not in rendered_prompt:
        raise ValueError('Rendered template lost or modified task instruction/data')
