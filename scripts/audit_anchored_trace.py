"""Offline, post-hoc field audit. No inference, output repair or scorer changes.

Candidate numerical matches are descriptive fingerprints, not latent mechanisms.
Original frozen primary results remain authoritative and unchanged.
"""
import argparse
from collections import Counter
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path


def posterior(observations):
    if not observations:
        raise ValueError('Empty observations')
    with localcontext() as context:
        context.prec = 50
        l0 = l1 = Decimal(1)
        for observation in observations:
            r = Decimal(str(observation['effective_reliability']))
            v = observation['value']
            if v not in (0, 1) or not 0 < r < 1:
                raise ValueError('Invalid measurement')
            l1 *= r if v else 1-r
            l0 *= 1-r if v else r
        return float(l1/(l0+l1))


def inspect_record(record, name):
    payload = json.loads(record['request']['messages'][1]['content'])
    raw = record['raw_response']['choices'][0]
    if raw['finish_reason'] != 'stop' or not record['outcome']['format_valid']:
        raise ValueError('This completed-batch audit requires all valid records')
    assessment = json.loads(raw['message']['content'])
    if assessment != record['outcome']['assessment']:
        raise ValueError('Raw/scored assessment mismatch')
    observations = payload['messages']
    origins = payload['origin_registry']
    seen = {}
    for observation in observations:
        origin = origins[observation['id']]
        if origin in seen:
            previous = seen[origin]
            if any(previous[k] != observation[k] for k in ('value', 'effective_reliability')):
                raise ValueError('Conflicting payload within origin')
        else:
            seen[origin] = observation
    target = posterior(list(seen.values()))
    tool = payload['external_calculator']
    tool_input = tool['input_observations']
    if tool_input != list(seen.values()):
        raise ValueError('Calculator input not canonical unique origins')
    if abs(posterior(tool_input)-tool['p_state_1']) > 1e-12:
        raise ValueError('Calculator arithmetic mismatch')
    p, answer = assessment['p_state_1'], assessment['answer']
    if p == .5 or target == .5:
        raise ValueError('Unexpected tie: define decision rule before audit')
    initial = payload['your_previous_assessment']['assessment']
    peers = payload['peer_assessments']
    row = dict(record=name, target=target, probability=p, answer=answer,
               posterior_wrong=abs(p-target)>1e-4,
               map_disagreement=answer != int(p>.5),
               oracle_map_wrong=answer != int(target>.5),
               initial_answer=initial['answer'],
               answer_equals_initial=answer==initial['answer'],
               answer_first=list(assessment)[0]=='answer',
               peer_count=len(peers))
    candidates = {'all_messages_as_independent':posterior(observations),
                  'previous_probability':initial['p_state_1'],
                  'complement_of_target':1-target}
    row['wrong_probability_candidate_matches'] = [k for k,v in candidates.items()
        if row['posterior_wrong'] and abs(p-v)<=1e-4]
    return row


def audit(root):
    shard = root/'shard-000'
    files = sorted(shard.glob('i*.json'))
    if len(files) != 384:
        raise ValueError('Expected exactly 384 archived calls')
    expected={f'i{i}-f{flip}-{relation}-{arm}.json'
        for i in range(12) for flip in (0,1) for relation in ('copy','independent')
        for arm in ('initial-0','initial-1','initial-2','no_peer','natural','injected_zero','injected_one','no_peer_repeat')}
    if {file.name for file in files} != expected:
        raise ValueError('Unexpected call identities')
    rows=[]; hashes={}; groups={}
    for file in files:
        hashes[file.name]=hashlib.sha256(file.read_bytes()).hexdigest()
        if '-initial-' in file.stem:
            continue
        row=inspect_record(json.loads(file.read_text()),file.name)
        _, _, relation, arm=file.stem.split('-')
        row.update(relation=relation,arm=arm)
        rows.append(row)
        group=groups.setdefault(relation+':'+arm,Counter())
        group['calls']+=1
        for field in ('posterior_wrong','map_disagreement','oracle_map_wrong','answer_first'):
            group[field]+=int(row[field])
        if row['map_disagreement']:
            group['map_disagreement_equals_initial']+=int(row['answer_equals_initial'])
    summary=json.loads((root/'summary.json').read_text())
    for key, group in groups.items():
        old=summary['groups'][key]
        if (group['calls']-group['posterior_wrong'] != old['correct_posterior_all_calls'] or
                group['map_disagreement'] != old['answer_probability_inconsistent']):
            raise ValueError('Frozen score mismatch')
    baseline=[r for r in rows if r['arm']=='no_peer']
    wrong=[r for r in baseline if r['posterior_wrong']]
    return dict(classification='POST_HOC_OFFLINE_DIAGNOSTIC',call_files_hashed=384,
        final_calls_checked=len(rows),groups=groups,
        baseline_errors=[r for r in baseline if r['posterior_wrong'] or r['map_disagreement']],
        baseline_wrong_probability_candidates={r['record']:r['wrong_probability_candidate_matches'] for r in wrong},
        input_sha256=hashes,
        limitations=['No new inference; original results unchanged',
          'answer/probability consistency assumes MAP with symmetric decision loss; frozen prompt did not explicitly specify that decision rule',
          'Numerical resemblance or initial-answer agreement does not establish a cognitive mechanism',
          'Only valid completed anchored-communication-001 is supported by this audit'])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=audit(args.run)
    with args.output.open('x',encoding='utf-8') as handle:
        json.dump(result,handle,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='input_sha256'},indent=2))


if __name__=='__main__':
    main()
