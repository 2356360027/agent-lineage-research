"""Transcribe a narrowly supported combined answer-review text, not a judge.

No IO or automatic accounting. Disclosures are self-reports, never proof of
human authorship. The caller must preserve original bytes and clarify provenance
before passing any parsed submission to the separate accounting function.
"""
from copy import deepcopy
import re

from runtime.cache_action_review import digest, submission_from_filled, RATINGS


LABELS = (
    '评分（MATCH/MISMATCH/UNCERTAIN；无答案留空）',
    '答案原句引用', '理由', '参考疑问（是/否；如有请说明）')


def field(text, name):
    values = re.findall(r'^'+re.escape(name)+r'：([^\r\n]*)$', text, re.M)
    if len(values) != 1:
        raise ValueError('Missing or duplicate field: '+name)
    return values[0].strip()


def transcribe(text, blank, *, expected_blank_sha256):
    if digest(blank) != expected_blank_sha256:
        raise ValueError('Blank packet identity mismatch')
    if (len(blank['items']) != 12 or len({i['item_id'] for i in blank['items']}) != 12
            or any(i['rating'] is not None or i['quoted_answer_span'] or i['reason']
                   or i['reference_concern'] for i in blank['items'])):
        raise ValueError('Untouched twelve-item blank packet required')
    text = text.replace('\r\n', '\n')
    headings = list(re.finditer(r'^# 本轮最终答案复核：复核者 ([12])[ \t]*$', text, re.M))
    if [h.group(1) for h in headings] != ['1','2']:
        raise ValueError('Expected exactly two ordered reviewer sections')
    submissions, adjustments = [], []
    for n, heading in enumerate(headings):
        section = text[heading.end():headings[n+1].start() if n == 0 else len(text)]
        parts = re.split(r'^条目编号：', section, flags=re.M)
        if len(parts) != 13:
            raise ValueError('Retain twelve explicit item blocks per reviewer')
        filled = deepcopy(blank)
        independent = field(parts[0], '是否独立完成（是/否）')
        assisted = field(parts[0], '是否借助模型或其他人填写（是/否；如有请说明）')
        if independent not in ('是','否') or assisted not in ('是','否'):
            raise ValueError('Detailed or ambiguous provenance requires manual preservation and clarification')
        filled.update(reviewer_code=field(parts[0], '复核者代号'), date=field(parts[0], '日期'),
                      independent_completion=independent == '是',
                      model_or_other_person_assistance=assisted == '是')
        if not filled['reviewer_code'] or not filled['date']:
            raise ValueError('Reviewer code and date required')
        originals = {i['item_id']:i for i in filled['items']}
        seen = set()
        for block in parts[1:]:
            ident = block.splitlines()[0].strip()
            if ident not in originals or ident in seen:
                raise ValueError('Unknown or duplicate item ID')
            seen.add(ident)
            for line in block.splitlines()[1:]:
                stripped = line.strip()
                if (stripped and not re.fullmatch(r'-{3,}|={5,}', stripped)
                        and not any(line.startswith(name+'：') for name in LABELS)):
                    raise ValueError('Unsupported continuation or extra content; do not silently drop text')
            rating, quote, reason, concern = (field(block, name) for name in LABELS)
            if rating not in (*RATINGS, '') or concern not in ('是','否'):
                raise ValueError('Unknown rating or concern; do not infer')
            item = originals[ident]
            # Only the submitted outer display delimiters may be removed.
            if quote.startswith('"') and quote.endswith('"') and len(quote) >= 2:
                quote = quote[1:-1]
                adjustments.append(dict(reviewer_slot=n+1,item_id=ident,
                                        kind='removed_two_outer_quote_delimiters'))
            if rating:
                if (item['final_status'] != 'ANSWERED' or not reason or not quote
                        or quote not in item['answer_text']):
                    raise ValueError('Rated answer needs unchanged source quotation and reason')
            item.update(rating=rating or None, quoted_answer_span=quote,
                        reason=reason, reference_concern=concern == '是')
        submissions.append(submission_from_filled(blank, filled,
                           expected_blank_sha256=expected_blank_sha256))
    return dict(status='TEXT_TRANSCRIBED_AUTHORSHIP_NOT_ESTABLISHED',
                submissions=submissions, quote_delimiter_adjustments=adjustments,
                independent_human_reviews_counted=0, semantic_labels_inferred=0,
                C_drift_assessed=False)
