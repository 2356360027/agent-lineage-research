"""Offline logical-request to native API payload conversion; no transport.

Checks interface structure, not authentic model provenance or task admission.
The caller must compare the request against its owned DefinitionHistory and
use pinned input admission before any paid dispatch. Old adapters/counters do
not accept this profile; do not bypass their checks to send these payloads.
"""
from copy import deepcopy
import json

from runtime.native_action_interface import tool_definitions, _load
from runtime.student_club_definition_history import definition_tool, parse, INTERFACE_INSTRUCTION
from runtime.student_club_diagnostic_design import canonical

VERSION = 'student-club-native-wire-offline-v1'
MODEL = 'deepseek-flash'
MAX_OUTPUT = 512
REQUEST_KEYS = {'phase','generation','action_generation','context','messages','tools',
                'interface_instruction','inference_authorized','provider_payload_validated'}


def payload(request):
    if (type(request) is not dict or set(request) != REQUEST_KEYS
            or request['inference_authorized'] is not False
            or request['provider_payload_validated'] is not False
            or type(request['generation']) is not int or type(request['action_generation']) is not int
            or type(request['context']) is not dict or set(request['context']) != {'instruction','data'}
            or type(request['context']['data']) is not dict
            or type(request['context']['instruction']) is not str
            or type(request['interface_instruction']) is not str
            or type(request['messages']) is not list):
        raise ValueError('Exact offline history request required')
    context = request['context']
    if request['phase'] == 'definition':
        if (request['generation'] != 1 or request['action_generation'] != 0
                or request['messages'] or 'prior_agent_definition' in context['data']):
            raise ValueError('Definition must be the first isolated request')
        expected_tools = [definition_tool('record_definition')]
    elif request['phase'] == 'action':
        n = request['action_generation']
        diagnostic = 'prior_agent_definition' in context['data']
        if not 1 <= n <= 4 or request['generation'] != n+int(diagnostic):
            raise ValueError('Unsupported action generation')
        names = {'answer','abstain'} if n == 4 else {'read_cache','run_sql','answer','abstain'}
        expected_tools = [t for t in tool_definitions() if t['function']['name'] in names]
        if diagnostic and n < 4:expected_tools.append(definition_tool('revise_definition'))
        messages = request['messages']
        if len(messages) != 2*(n-1):
            raise ValueError('Every earlier action needs exactly one result')
        seen = set()
        for assistant, result in zip(messages[::2], messages[1::2]):
            parsed = parse(json.dumps({'choices':[{'index':0,'finish_reason':'tool_calls','message':assistant}]}))
            allowed = {'read_cache','run_sql'} | ({'revise_definition'} if diagnostic else set())
            if (parsed['action']['kind'] not in allowed or parsed['call_id'] in seen
                    or canonical(assistant) != canonical(parsed['message'])
                    or type(result) is not dict or set(result) != {'role','tool_call_id','content'}
                    or result['role'] != 'tool' or result['tool_call_id'] != parsed['call_id']
                    or type(result['content']) is not str or not result['content']
                    or len(result['content'].encode()) > 1048576):
                raise ValueError('Nonterminal original assistant/result pairs required')
            seen.add(parsed['call_id'])
    else:
        raise ValueError('Unknown phase')
    if canonical(request['tools']) != canonical(expected_tools):
        raise ValueError('Native schema set differs from phase/horizon')
    wire = dict(model=MODEL, messages=[
        {'role':'system','content':context['instruction']+'\n\n'+request['interface_instruction']},
        {'role':'user','content':canonical(context['data'])}] + deepcopy(request['messages']),
        thinking={'type':'disabled'},temperature=0,max_tokens=MAX_OUTPUT,stream=False,
        tools=deepcopy(expected_tools),tool_choice='required')
    # Disk freeze/reload cannot change nested schema ordering used in counting.
    return json.loads(canonical(wire))


def validate_wire(wire):
    """Restricted reversible structural validation for an isolated HTTP worker.

    Not source authentication: the owned-history adapter enforces provenance.
    Strict canonical round-trip rejects parameter/role/schema smuggling.
    """
    fields={'model','messages','thinking','temperature','max_tokens','stream','tools','tool_choice'}
    if type(wire) is not dict or set(wire)!=fields:
        raise ValueError('Exact wire fields required')
    messages=wire['messages']
    if (type(messages) is not list or len(messages)<2 or type(wire['tools']) is not list
            or any(type(m) is not dict or set(m)!={'role','content'} or type(m['content']) is not str
                   for m in messages[:2])
            or [m['role'] for m in messages[:2]]!=['system','user']):
        raise ValueError('Canonical initial system/user pair required')
    suffix='\n\n'+INTERFACE_INSTRUCTION
    if not messages[0]['content'].endswith(suffix):
        raise ValueError('Interface instruction differs')
    data=_load(messages[1]['content'])
    if type(data) is not dict:raise ValueError('Public packet must be an object')
    is_definition=canonical(wire['tools'])==canonical([definition_tool('record_definition')])
    n=0 if is_definition else 1+(len(messages)-2)//2
    request=dict(phase='definition' if is_definition else 'action',
        generation=1 if is_definition else n+int('prior_agent_definition' in data),
        action_generation=n,context=dict(instruction=messages[0]['content'][:-len(suffix)],data=data),
        messages=deepcopy(messages[2:]),tools=deepcopy(wire['tools']),
        interface_instruction=INTERFACE_INSTRUCTION,inference_authorized=False,provider_payload_validated=False)
    if canonical(payload(request))!=canonical(wire):
        raise ValueError('Noncanonical or unsupported wire profile')
    return request
