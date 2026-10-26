"""Offline, injected journal/state machine; no API, SQL or spending authority.

Requests are logical specifications, NOT provider-ready payloads. A separately
audited transport, token counter and batch runner are still required. Definition
elicitation and context reconstruction are interventions, not hidden-thought
measurements. The journal establishes order, not semantic correctness.
"""
from copy import deepcopy
from datetime import datetime, timezone

from runtime.native_action_interface import _load, parse_native_response_v2, tool_definitions
from runtime.student_club_diagnostic_design import build, bind_definition, canonical, sha

VERSION = 'student-club-definition-history-offline-v1'
DEFINITION_NAMES = {'record_definition', 'revise_definition'}


def definition_tool(name):
    if name not in DEFINITION_NAMES:
        raise ValueError('Unknown definition operation')
    return {'type': 'function', 'function': {
        'name': name, 'description': 'Record your visible description of the current request, not private reasoning.',
        'strict': True, 'parameters': {'type': 'object',
            'properties': {'text': {'type': 'string'}}, 'required': ['text'],
            'additionalProperties': False}}}


def parse(raw):
    """Strict single-call envelope; preserve originals, never repair responses."""
    if type(raw) is not str or len(raw.encode()) > 1024 * 1024:
        raise ValueError('Bounded raw provider JSON required')
    body = _load(raw)
    if type(body) is not dict or 'error' in body:
        raise ValueError('Provider error or non-object')
    choices = body.get('choices')
    if type(choices) is not list or len(choices) != 1:
        raise ValueError('Exactly one choice required')
    choice = choices[0]
    if (type(choice) is not dict or type(choice.get('index')) is not int
            or choice['index'] != 0 or choice.get('finish_reason') != 'tool_calls'):
        raise ValueError('Complete tool call required')
    message = choice.get('message')
    if (type(message) is not dict or message.get('role') != 'assistant'
            or 'content' not in message or message['content'] not in (None, '')
            or message.get('reasoning_content') not in (None, '')):
        raise ValueError('Extra assistant content')
    calls = message.get('tool_calls')
    if type(calls) is not list or len(calls) != 1:
        raise ValueError('Exactly one function required')
    call = calls[0]
    if (type(call) is not dict or not {'id', 'type', 'function'} <= set(call)
            <= {'id', 'type', 'function', 'index'}
            or call['type'] != 'function' or type(call['id']) is not str
            or not call['id'].strip()):
        raise ValueError('Malformed call')
    if 'index' in call and (type(call['index']) is not int or call['index'] != 0):
        raise ValueError('Invalid optional index')
    function = call['function']
    if (type(function) is not dict or set(function) != {'name', 'arguments'}
            or type(function['name']) is not str or type(function['arguments']) is not str):
        raise ValueError('Malformed function')
    if function['name'] in DEFINITION_NAMES:
        args = _load(function['arguments'])
        if (type(args) is not dict or set(args) != {'text'}
                or type(args['text']) is not str or not args['text'].strip()
                or len(args['text'].encode()) > 8192):
            raise ValueError('Bounded original definition required')
        action = {'kind': function['name'], 'text': args['text']}
    else:
        action = parse_native_response_v2(raw)['action']
    # Native history needs the original argument string, not reserialized text.
    original = {'role': 'assistant', 'content': None,
                'tool_calls': [{k: deepcopy(call[k]) for k in ('id', 'type', 'function')}]}
    return {'call_id': call['id'], 'action': action, 'message': original}


class DefinitionHistory:
    """Single-owner state machine with mandatory append-only save callback.

    The caller must seal/verify the journal and authenticate public input/source
    bindings. Injected observations are not authenticated here. No automatic
    A/B/C label, task-equivalence check, fee approval or real execution occurs.
    """
    def __init__(self, common, job, save):
        if type(job) is not dict:
            raise ValueError('Prospective job object required')
        expected = next((j for j in build(common)['jobs'] if j['id'] == job.get('id')), None)
        if expected is None or canonical(expected) != canonical(job) or not callable(save):
            raise ValueError('Exact prospective job and journal required')
        self.job = deepcopy(expected)
        self.save = save
        self.state = 'READY'
        self.phase = 'action' if job['protocol'] == 'ordinary' else 'definition'
        self.sequence = self.generations = self.action_generations = self.observations = 0
        self.ids = set()
        self.definition = None
        self.pending = None
        self.messages = []
        self.context = deepcopy(job['action_stage_template'])
        self._event('start', {'version': VERSION, 'job': self.job,
            'public_input_sha256': sha(common), 'inference_authorized': False,
            'semantic_correctness_assessed': False})

    def _event(self, kind, value):
        self.sequence += 1
        event_id = f'e{self.sequence:04d}'
        record = {'event_id': event_id, 'kind': kind, 'job_id': self.job['id'],
            'utc': datetime.now(timezone.utc).isoformat(), 'value': deepcopy(value)}
        try:
            self.save(event_id, record)
        except BaseException:
            self.state = 'JOURNAL_FAILED'
            raise
        return event_id

    def begin(self):
        if self.state != 'READY':
            raise ValueError('Not ready for generation')
        self.generations += 1
        if self.phase == 'definition':
            context = self.job['definition_stage']
            names = ['record_definition']
        else:
            self.action_generations += 1
            context = self.context
            names = ['answer', 'abstain'] if self.action_generations == 4 else [
                'read_cache', 'run_sql', 'answer', 'abstain']
            if self.action_generations < 4 and self.job['protocol'] != 'ordinary':
                names.append('revise_definition')
        tools = [t for t in tool_definitions() if t['function']['name'] in names]
        tools += [definition_tool(n) for n in names if n in DEFINITION_NAMES]
        request = {'phase': self.phase, 'generation': self.generations,
            'action_generation': self.action_generations, 'context': deepcopy(context),
            'messages': deepcopy(self.messages), 'tools': tools,
            'interface_instruction': ('Return exactly one offered function call, with no separate text. '
                'record_definition and revise_definition only record your visible task description; '
                'they do not execute database queries or other external tools.'),
            'inference_authorized': False, 'provider_payload_validated': False}
        self.allowed = set(names)
        self.request_event = self._event('generation_requested', request)
        self.state = 'AWAIT_RESPONSE'
        return deepcopy(request)

    def accept(self, raw):
        if self.state != 'AWAIT_RESPONSE':
            raise ValueError('No pending model request')
        # Save before parsing, including malformed/duplicate/terminal violations.
        raw_event = self._event('raw_response', {'request_event': self.request_event, 'raw': raw})
        try:
            parsed = parse(raw)
            call_id, action = parsed['call_id'], parsed['action']
            if call_id in self.ids or action['kind'] not in self.allowed:
                raise ValueError('Repeated call ID or unavailable/horizon-violating action')
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            self.state = 'INVALID_RESPONSE'
            self._event('invalid_response', {'raw_event': raw_event, 'error_type': type(error).__name__})
            raise ValueError('Invalid response retained; no retry authorized') from error
        self.ids.add(call_id)
        if action['kind'] in DEFINITION_NAMES:
            previous = self.definition['event_id'] if self.definition else None
            definition_event = self._event('definition_recorded', {
                'text': action['text'], 'source': 'evaluated_agent', 'raw_event': raw_event,
                'request_event': self.request_event, 'call_id': call_id, 'previous_definition': previous,
                'observation': 'post_result' if self.observations else 'pre_result',
                'human_verified': False, 'semantic_correctness_assessed': False})
            self.definition = {'event_id': definition_event, 'text': action['text'],
                               'observation': 'post_result' if self.observations else 'pre_result'}
            if self.phase == 'definition':
                self.context = bind_definition(self.job, own_definition=action['text'], source_event_id=definition_event)
                self.phase = 'action'
                self._event('context_reconstructed', {'definition_event': definition_event,
                    'policy': 'new_action_context_with_raw_own_definition_not_same_conversation'})
            else:
                self.messages += [parsed['message'], {'role': 'tool', 'tool_call_id': call_id,
                    'content': canonical({'status': 'DEFINITION_RECORDED_NOT_VERIFIED', 'event_id': definition_event})}]
            self.state = 'READY'
            return {'kind': 'definition_recorded', 'event_id': definition_event}
        action_event = self._event('action_recorded', {'action': action, 'raw_event': raw_event,
            'call_id': call_id, 'definition_event': self.definition['event_id'] if self.definition else None,
            'definition_observation': self.definition['observation'] if self.definition else 'not_elicited',
            'semantic_correctness_assessed': False})
        self.messages.append(parsed['message'])
        if action['kind'] in ('answer', 'abstain'):
            self.state = 'TERMINAL'
        else:
            self.pending = {'call_id': call_id, 'action_event': action_event}
            self.state = 'AWAIT_OBSERVATION'
        return {'kind': 'action_recorded', 'event_id': action_event, 'action': deepcopy(action),
                'call_id': call_id, 'execution_authorized': False}

    def observe(self, call_id, content):
        if (self.state != 'AWAIT_OBSERVATION' or call_id != self.pending['call_id']
                or type(content) is not str or not content or len(content.encode()) > 1024):
            raise ValueError('Expected bounded observation for exact pending call')
        self._event('tool_observation', self.pending | {'content': content,
            'observation_authenticated_here': False})
        self.messages.append({'role': 'tool', 'tool_call_id': call_id, 'content': content})
        self.observations += 1
        self.pending = None
        self.state = 'READY'

    def fail(self, reason):
        if self.state in ('TERMINAL', 'INVALID_RESPONSE', 'JOURNAL_FAILED', 'FAILED'):
            raise ValueError('Already terminal')
        self.state = 'FAILED'
        self._event('external_failure', {'reason': reason, 'pending': self.pending})
