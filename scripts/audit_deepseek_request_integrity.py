"""Offline client-record integrity audit, not remote attestation or attention measurement."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime import deepseek_trajectory_adapter as adapter
from runtime.cache_intent_journal import Journal
from scripts.prepare_staged_contract_review import load_archive


def check_price_pair(first, second):
    normalized = []
    for payload, expected in ((first,1),(second,8)):
        clone = deepcopy(payload)
        if len(clone['messages']) != 2 or clone['messages'][1]['role'] != 'user':
            raise ValueError('Expected pre-result two-message request')
        packet = json.loads(clone['messages'][1]['content'])
        if packet['context']['cache']['read_cost'] != expected:
            raise ValueError('Missing intended read price')
        packet['context']['cache']['read_cost'] = 1
        clone['messages'][1]['content'] = packet
        normalized.append(clone)
    if normalized[0] != normalized[1]:
        raise ValueError('Paired first requests differ beyond read price')


def check_generation(request, pending, context, provider, transport, response):
    payload = pending['payload']
    if payload != adapter.make_payload(request['messages']):
        raise ValueError('Provider payload differs from recorded trajectory request')
    sha = audit.digest(payload)
    if context['payload_sha256'] != sha or context.get('truncated') is not False:
        raise ValueError('Missing untruncated payload link')
    raw = provider['raw']
    if (transport.get('transport_status') != 'RESPONSE_RECEIVED'
            or json.loads(transport['response_text']) != raw):
        raise ValueError('Transport response differs from provider record')
    if (raw['model'] != payload['model']
            or raw['usage']['prompt_tokens'] != context['reference_input_tokens']):
        raise ValueError('Provider alias/input count mismatch')
    reply = response['response']
    if (response['request_sha256'] != audit.digest(request)
            or reply['request_sha256'] != sha
            or reply['content'] != raw['choices'][0]['message']['content']
            or reply['finish_reason'] != raw['choices'][0]['finish_reason']
            or reply['usage'] != raw['usage']):
        raise ValueError('Accepted generation is not the saved provider response')


def run(root, prepared_sha, experiment_sha, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError('Keep original archive unchanged')
    if audit.hash_file(root/'prepared_manifest.json') != prepared_sha:
        raise ValueError('Prepared manifest changed')
    prepared = json.loads((root/'prepared_manifest.json').read_text('utf-8'))
    project = Path(__file__).resolve().parents[1]
    # make_payload depends on these two source modules; require byte identity.
    for name in ('runtime/deepseek_trajectory_adapter.py','runtime/cache_intent_prompts.py'):
        if audit.hash_file(project/name) != prepared['source_files'][name]:
            raise ValueError('Audit reconstruction source differs from frozen experiment')
    experiment = root/'experiment'
    _, manifest = load_archive(experiment, experiment_sha, include_trace=True)
    def read(name):
        if audit.hash_file(experiment/name) != manifest[name]:
            raise ValueError('Experiment artifact changed')
        return json.loads((experiment/name).read_text('utf-8'))
    plan = read('batch/plan.json')
    if len(plan['jobs']) != 12 or plan['independent_targets'] != 2:
        raise ValueError('Wrong development plan')
    pairs, checked = {}, 0
    for job in plan['jobs']:
        prefix = job['id']+'/trace/'
        first = read(prefix+'g001.pending.json')
        if first['messages'] != job['envelope']['messages']:
            raise ValueError('First request differs from frozen envelope')
        generation_names = sorted(n for n in manifest if n.startswith(prefix+'api-g') and n.endswith('.pending.json'))
        for name in generation_names:
            generation = name.rsplit('/',1)[1][4:8]
            request = read(prefix+generation+'.pending.json')
            check_generation(request,read(name),read(prefix+'api-'+generation+'.context.json'),
                read(prefix+'api-'+generation+'.provider.json'),
                read(prefix+'transport-'+generation[1:]+'.json'),
                read(prefix+generation+'.response.json'))
            checked += 1
        pairs.setdefault(job['history_id'],{})[job['read_price']] = read(prefix+'api-g001.pending.json')['payload']
    if len(pairs) != 6 or any(set(p)!={1,8} for p in pairs.values()):
        raise ValueError('Missing paired coverage')
    for pair in pairs.values():
        check_price_pair(pair[1],pair[8])
    result = {'status':'CLIENT_REQUEST_LINKS_AND_PRICE_INTERVENTION_VERIFIED',
        'prepared_manifest_sha256':prepared_sha,'experiment_manifest_sha256':experiment_sha,
        'checked_generations':checked,'checked_pre_result_pairs':len(pairs),
        'only_pre_result_payload_difference':'context.cache.read_cost: 1 versus 8',
        'model_attention_to_price_established':False,'remote_execution_attested':False,
        'remote_weights_identified':False,'semantic_labels_generated':False,
        'new_model_calls':0,'new_network_requests':0,
        'scope':'Client saved requests and linked responses; later requests include model-generated differences and are not expected to be otherwise identical.'}
    journal = Journal(output)
    journal('receipt', result)
    journal('audit-source', {'source_utf8':Path(__file__).read_text('utf-8')})
    journal.seal()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('run','prepared-sha','experiment-sha','output'):
        parser.add_argument('--'+name,required=True)
    a = parser.parse_args()
    print(json.dumps(run(a.run,a.prepared_sha,a.experiment_sha,a.output),indent=2))
