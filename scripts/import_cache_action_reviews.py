"""Account supplied final-answer reviews after explicit human-source clarification.

Does not judge answers, call models, execute SQL, or publish raw submissions.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from runtime.cache_action_review import account, digest
from runtime.cache_action_review_text import transcribe
from scripts.audit_cache_action import read, sha, snapshot, require, no_links
from scripts.export_cache_action_reviews import tree, AUDIT


def run(source, source_sha, packet, auditdir, output, *, human_authorship_confirmed=False):
    require(human_authorship_confirmed, 'Explicit human authorship clarification required')
    source, packet, auditdir, output = map(lambda p: Path(p).resolve(), (source, packet, auditdir, output))
    for p in (source, packet, auditdir, output): no_links(p)
    require(not output.exists(), 'Never overwrite an accounting run')
    require(not any(output == p or output.is_relative_to(p) for p in (packet,auditdir,source.parent)),
            'Keep accounting separate from original evidence')
    require(sha(source) == source_sha, 'Submission changed')
    tree(packet); tree(auditdir)
    require(sha(auditdir/'audit.json') == AUDIT, 'Wrong audited model batch')
    receipt = read(packet/'export_receipt.json')
    require(receipt['audit_sha256'] == AUDIT, 'Review packet belongs to another audit')
    blank = read(packet/'REVIEWER_1.json')
    require(blank == read(packet/'REVIEWER_2.json'), 'Unexpected differences between blank packets')
    require(digest(blank) == receipt['reviewer_blank_sha256']['REVIEWER_2'], 'Second blank identity changed')
    mapping = read(packet/'PRIVATE_mapping.json')
    audited = {r['id']:r for r in read(auditdir/'audit.json')['rows']}
    require(set(mapping) == {i['item_id'] for i in blank['items']}, 'Mapping coverage differs')
    require({r['job_id'] for r in mapping.values()} == set(audited), 'Run coverage differs')
    for item in blank['items']:
        link = mapping[item['item_id']]; original = audited[link['job_id']]
        outcome = {k: original[k] for k in ('final_status','final_answer','trace_sha256')}
        require(digest(outcome) == link['outcome_sha256'] and
                original['trace_sha256'] == link['trace_sha256'] and
                original['final_answer'] == item['answer_text'] and
                original['final_status'] == item['final_status'], 'Review evidence differs from audit')
    before = (snapshot(packet),snapshot(auditdir),sha(source))
    parsed = transcribe(source.read_text('utf-8-sig'), blank,
                        expected_blank_sha256=receipt['reviewer_blank_sha256']['REVIEWER_1'])
    result = account(blank, parsed['submissions'])
    conditions = {}
    for row in result['rows']:
        job = mapping[row['item_id']]['job_id']
        condition = 'available' if audited[job]['available'] else 'unavailable'
        conditions.setdefault(condition, Counter())[row['review_status']] += 1
    summary = dict(status='USER_ATTESTED_FINAL_ANSWER_REVIEWS_ACCOUNTED',
        source_sha256=source_sha, export_receipt_sha256=sha(packet/'export_receipt.json'),
        audit_sha256=AUDIT, task_units=2, reviewed_answer_slots=12,
        reviewer_count=2, submitted_ratings=24,
        review_status_counts=dict(Counter(r['review_status'] for r in result['rows'])),
        conditions={k:dict(v) for k,v in conditions.items()},
        quote_delimiter_adjustments=len(parsed['quote_delimiter_adjustments']),
        user_confirmation='两位真人独立评分后合并，未用模型代写判断',
        user_attested_separate_human_authorship=True, identity_independently_verified=False,
        reviewer_independence_independently_verified=False, automatic_adjudication=False,
        C_drift_assessed=False, new_model_calls=0, new_SQL_executions=0,
        semantic_labels_created_by_importer=False, raw_reviews_public=False,
        limits=['Two reused development tasks under adopted post-hoc interpretations.',
                'Answer review is not a pre-action task-contract review or evidence of internal interpretation.',
                'No population-wide absence, statistical equivalence, or novel method advantage established.'])
    require(before == (snapshot(packet),snapshot(auditdir),sha(source)), 'Inputs changed during import')
    output.mkdir(parents=True,exist_ok=False)
    with (output/'original-submission.txt').open('xb') as f: f.write(source.read_bytes())
    for name, value in (('transcription.json',parsed),('accounting.json',result),('summary.json',summary)):
        with (output/name).open('x',encoding='utf-8') as f: json.dump(value,f,ensure_ascii=False,indent=2)
    for name,path in [('importer.py',Path(__file__)),('parser.py',Path(__file__).parents[1]/'runtime/cache_action_review_text.py'),
                      ('accounting_source.py',Path(__file__).parents[1]/'runtime/cache_action_review.py')]:
        with (output/name).open('xb') as f: f.write(path.read_bytes())
    hashes = snapshot(output)
    with (output/'artifact_hashes.json').open('x',encoding='utf-8') as f: json.dump(hashes,f,indent=2)
    tree(output)
    return summary


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','source-sha','packet','auditdir','output'): p.add_argument('--'+name,required=True)
    p.add_argument('--separate-human-authorship-confirmed',action='store_true')
    a=p.parse_args()
    print(json.dumps(run(a.source,a.source_sha,a.packet,a.auditdir,a.output,
                        human_authorship_confirmed=a.separate_human_authorship_confirmed),ensure_ascii=True,indent=2))
