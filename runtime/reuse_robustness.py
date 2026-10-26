"""Preselected coded-label and candidate-blind checklist diagnostics.

These do not replace any main result. Both interventions were specified before
the main matrix; exact mapping/wording is frozen before these separate calls.
"""
import copy
import json
import random

from runtime.reuse_protocol import messages as original_messages, strip_price
from runtime.reuse_scenarios import canonical
from runtime.reuse_separation import parse_response

LABEL_CODES = {'applicable': 'A', 'inapplicable': 'B', 'unknown': 'C'}
ACTION_CODES = {'reuse': 'X', 'recompute': 'Y', 'abstain': 'Z'}
CODE_INSTRUCTION = (
    'For output encoding only, use these exact case-sensitive codes instead of '
    'the semantic words: admissibility A=applicable, B=inapplicable, C=unknown; '
    'action X=reuse, Y=recompute, Z=abstain. The definitions and decision rule '
    'above are unchanged. Do not use semantic words in the coded fields. ')
BLIND_CHECKLIST = (
    'Act as an independent verifier of cache applicability. '
    'Check entity grain, NULL denominators, information discarded by the historical query, '
    'and whether all consistent databases yield the same current answer. '
    'Reject unsupported inferences; use unknown if you cannot decide. ')


def select_cases(cases):
    selected = [c for c in cases if c['instance'] == 0]
    if (len(cases) != 192 or len(selected) != 24
        or len({c['template'] for c in selected}) != 12
        or len({c['base_id'] for c in selected}) != 12
        or any(c['split'] != 'confirmation_candidate' for c in cases)):
        raise ValueError('Requires frozen instance0 of all12 held templates')
    return selected


def schedule(cases):
    selected = select_cases(cases)
    result = []
    bases = sorted({c['base_id'] for c in selected})
    random.Random(2026102703).shuffle(bases)
    for base in bases:
        block = []
        for c in sorted(selected, key=lambda c: c['case_id']):
            if c['base_id'] != base:
                continue
            for method, price in [('M1', 1), ('M1', 20), ('M2', 1), ('M2', 20),
                                  ('M3', None), ('BLIND_CHECKLIST', None)]:
                arm = 'candidate_blind' if method == 'BLIND_CHECKLIST' else 'coded'
                block.append({'job_id': f'{c["case_id"]}-{arm}-{method}-p{price}',
                              'case_id': c['case_id'], 'base_id': base,
                              'method': method, 'price': price, 'arm': arm})
        random.Random('robustness-v1-' + base).shuffle(block)
        result.extend(block)
    assert len(result) == 144 and len({j['job_id'] for j in result}) == 144
    return result


def messages(case, job):
    if job['arm'] == 'candidate_blind':
        if job['method'] != 'BLIND_CHECKLIST' or job['price'] is not None:
            raise ValueError('Blind checklist must not receive prices or proposals')
        msgs = original_messages(case, {'method': 'M3', 'price': None})
        before, fields = msgs[0]['content'].split('JSON fields: ')
        msgs[0]['content'] = before + BLIND_CHECKLIST + 'JSON fields: ' + fields
        return msgs
    if job['arm'] != 'coded' or job['method'] not in ('M1', 'M2', 'M3'):
        raise ValueError('Unknown diagnostic arm')
    msgs = original_messages(case, job)
    before, field_text = msgs[0]['content'].split('JSON fields: ')
    fields = json.loads(field_text)
    for name, codes in (('admissibility', LABEL_CODES), ('action', ACTION_CODES)):
        if name in fields:
            fields[name] = ' | '.join(codes[x] for x in fields[name].split(' | '))
    msgs[0]['content'] = before + CODE_INSTRUCTION + 'JSON fields: ' + canonical(fields)
    return msgs


def no_duplicates(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise ValueError('Duplicate output key')
        obj[k] = v
    return obj


def parse(raw, job):
    # Existing parser is retained for semantic checks; new coded output is
    # decoded only after validating exactly one value per key. No prose rescue.
    method = job['method'] if job['method'] != 'BLIND_CHECKLIST' else 'M3'
    invalid = parse_response(None, method)
    if raw is None:
        return dict(invalid, failure='missing')
    try:
        choice = raw['choices'][0]
        obj = json.loads(choice['message']['content'], object_pairs_hook=no_duplicates)
        if not isinstance(obj, dict):
            raise ValueError('Output is not an object')
        if job['arm'] == 'coded':
            obj['admissibility'] = {v: k for k, v in LABEL_CODES.items()}[obj['admissibility']]
            if method == 'M1':
                obj['action'] = {v: k for k, v in ACTION_CODES.items()}[obj['action']]
        decoded = copy.deepcopy(raw)
        decoded['choices'][0]['message']['content'] = canonical(obj)
        return parse_response(decoded, method)
    except (KeyError, TypeError, ValueError, IndexError):
        return dict(invalid, failure='invalid_encoding_or_json')


def audit_design(cases):
    jobs = schedule(cases)
    for case in select_cases(cases):
        rows = [j for j in jobs if j['case_id'] == case['case_id']]
        for method in ('M1', 'M2'):
            variants = [strip_price(messages(case, j)) for j in rows if j['method'] == method]
            if variants[0] != variants[1]:
                raise ValueError('Nonprice change within pair')
        m2 = next(j for j in rows if j['method'] == 'M2')
        m3 = next(j for j in rows if j['method'] == 'M3')
        if strip_price(messages(case, m2)) != messages(case, m3):
            raise ValueError('M2/M3 differ beyond prices')
        for job in rows:
            payload = json.loads(messages(case, job)[1]['content'])
            payload.pop('costs', None)
            if payload != case['public']:
                raise ValueError('Extra information in public task')
    return {'calls': 144, 'coded_calls': 120, 'candidate_blind_calls': 24,
            'bases': 12, 'templates': 12, 'contexts': 24,
            'price_only_verified': True, 'M2_M3_price_only_verified': True,
            'private_audit_or_candidate_exposed': False}
