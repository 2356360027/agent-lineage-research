"""Frozen-input candidate only, no execution path or authorization."""
from copy import deepcopy
import json

from runtime import bird_readonly_audit as audit
from runtime.cache_workflow_diagnostic import payload

HISTORIES = tuple(f'h{t}{s}' for t in (200, 234) for s in 'abc')
SEED = 'cache-documentation-calibration-v1-20261024'
DOCUMENTATION = '''Tool semantics for this experiment:
history.sql is the historical query associated with cache v000 on the fixed snapshot identified by context.database_sha256.
The experiment operator has verified that the stored result is the complete execution result of that historical query on this snapshot. This does not certify that the historical query answers the current request.
When materialized_available is true, read_cache with cache_id v000 retrieves that stored result's columns and rows without running SQL again. When false, that result cannot be retrieved through read_cache.
run_sql executes the supplied read-only query on the same snapshot. Use the declared tool costs; results are subject to the shared complete-or-explicit-oversize observation policy.
Decide applicability from the original current request, evidence, schema and historical query. Do not replace the current request with the historical one. No task-specific applicability label is supplied.'''


def build(old_plan):
    """Caller MUST verify historical execution provenance before using the text."""
    jobs = []
    for hid in HISTORIES:
        selected = [j for j in old_plan['jobs'] if j['history_id'] == hid and j['read_price'] == 1]
        if len(selected) != 1:
            raise ValueError('Expected each fixed historical input exactly once')
        original = selected[0]['envelope']['messages']
        system = original[0]['content']
        if system.count('Stage 1:') != 1:
            raise ValueError('Unexpected staged source')
        packet = json.loads(original[1]['content'])
        if packet['context']['cache'] != dict(cache_id='v000', materialized_available=True, read_cost=1):
            raise ValueError('Wrong availability or read price')
        if packet['context']['sql_call_cost'] != 10:
            raise ValueError('Wrong SQL cost')
        base = payload(dict(workflow='action_first', packet=packet,
                            action_system=system.split('Stage 1:')[0]+'Do not invent unspecified requirements.\n'))
        for condition in ('original', 'documented'):
            request = deepcopy(base)
            if condition == 'documented':
                request['messages'][0]['content'] += '\n\n'+DOCUMENTATION
            jobs.append(dict(id=hid+'-'+condition, history_id=hid, condition=condition, payload=request))
    jobs.sort(key=lambda j: audit.digest(SEED+j['id']))
    return dict(version=SEED, jobs=jobs, independent_development_tasks=2,
                max_calls=12, max_seconds=600, max_output_tokens=512,
                semantic_success_labels=0, tools_to_execute=0, authorization=False)
