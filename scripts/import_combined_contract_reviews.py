"""Transcribe a combined human review submission; do not create semantic labels.

Authorship is user-attested, not independently verified. Preserve the original
bytes and disclose removal of quotation delimiters for substring matching.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from runtime.cache_intent_journal import Journal
from runtime import bird_readonly_audit as audit
from scripts.prepare_staged_contract_review import load_archive


def field(text, label):
    matches = re.findall(r'^'+re.escape(label)+r'：([^\r\n]+)', text, re.M)
    if len(matches) != 1:
        raise ValueError('Missing or duplicated submitted field: '+label)
    return matches[0].strip()


def transcribe(text, template, source_sha):
    if (len(template['items']) != 12
            or any(v is not None for v in template['reviewer'].values())
            or any(v is not None for i in template['items'] for v in i['review'].values())):
        raise ValueError('Unchanged blank template required')
    headings = list(re.finditer(r'^# 开发实验契约复核表：复核者 ([12])\s*$',text,re.M))
    if len(headings) != 2 or [m.group(1) for m in headings] != ['1','2']:
        raise ValueError('Expected two explicit submitted reviewer sections')
    reviews, changes = [], []
    expected = {i['item_id'] for i in template['items']}
    for slot, heading in enumerate(headings,1):
        end = headings[slot].start() if slot < 2 else len(text)
        section = text[heading.end():end]
        parts = re.split(r'^编号：', section, flags=re.M)
        if len(parts) != 13:
            raise ValueError('Expected twelve submitted ratings per reviewer')
        preamble = parts[0]
        if '第一部分' not in preamble or '你的需求理解' not in preamble:
            raise ValueError('Missing submitted current-request interpretation section')
        independent = field(preamble,'是否独立完成（是/否）')
        if independent not in ('是','否'):
            raise ValueError('Unrecognized independence disclosure')
        result = deepcopy(template)
        result['reviewer'] = {
            'code':field(preamble,'复核者自选代号'), 'date':field(preamble,'日期'),
            'independently_completed':independent == '是',
            'prior_exposure':field(preamble,'此前接触过哪些题目、输出、研究讨论或答案'),
            'assistance_used':field(preamble,'本次是否借助他人/模型；如有，请说明范围'),
            'current_request_review_reference':f'sha256:{source_sha}#reviewer-{slot}/第一部分; reading order self-reported, not independently verified',
        }
        originals = {i['item_id']:i for i in result['items']}
        seen = set()
        for block in parts[1:]:
            ident = block.splitlines()[0].strip()
            if ident not in expected or ident in seen:
                raise ValueError('Unknown or duplicated rating ID')
            seen.add(ident)
            label = field(block,'整体判断（match / contradiction / unresolved）')
            if label not in ('match','contradiction','unresolved'):
                raise ValueError('Unrecognized submitted label')
            flags = []
            for name in ('是否有实质关键遗漏（是/否/未决）',
                         '问题是否仅为字段摆放错位（是/否/未决；无问题填否）'):
                value = field(block,name)
                if value not in ('是','否','未决'):
                    raise ValueError('Unrecognized submitted flag')
                flags.append({'是':True,'否':False,'未决':None}[value])
            quote_part, reason = block.split('支持判断的模型原文片段（精确引用，不改写）：',1)[1].split(
                '理由（结合需求解释，不能仅凭字段名或格式）：',1)
            quoted = quote_part.strip()
            if not (quoted.startswith('`') and quoted.endswith('`')):
                raise ValueError('Expected a single backtick-delimited evidence quote')
            quote = quoted[1:-1]
            raw = originals[ident]['raw_contract']
            if quote not in raw:
                if quote.startswith('"') and quote.endswith('"') and quote[1:-1] in raw:
                    changes.append({'reviewer_slot':slot,'item_id':ident,
                                    'change':'Removed only two outer quotation delimiters',
                                    'submitted_quote':quote,'imported_quote':quote[1:-1]})
                    quote = quote[1:-1]
                else:
                    raise ValueError('Evidence quote does not match source contract')
            reason = re.split(r'\n(?:={5,}|---)',reason,maxsplit=1)[0].strip()
            if not reason:
                raise ValueError('Missing submitted reason')
            originals[ident]['review'] = dict(whole_contract_vs_request=label,
                material_omission=flags[0],field_misplacement_only=flags[1],
                evidence_quote=quote,reason=reason)
        if seen != expected:
            raise ValueError('Missing ratings')
        reviews.append(result)
    return reviews,changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source','packet','packet-sha','output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--separate-human-authorship-confirmed',action='store_true')
    args = parser.parse_args()
    if not args.separate_human_authorship_confirmed:
        parser.error('Explicit user clarification of separate human authorship required')
    packet, output = Path(args.packet).resolve(),Path(args.output).resolve()
    if output == packet or output.is_relative_to(packet):
        raise ValueError('Keep blank source packet unchanged')
    _, manifest = load_archive(packet,args.packet_sha)
    raw = Path(args.source).read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    template = json.loads((packet/'reviewer-1.json').read_text('utf-8'))
    if audit.hash_file(packet/'reviewer-1.json') != manifest['reviewer-1.json']:
        raise ValueError('Template changed')
    reviews,changes = transcribe(raw.decode('utf-8-sig'),template,source_sha)
    journal = Journal(output)
    with (output/'source_submission.txt').open('xb') as stream:
        stream.write(raw)
    for i,review in enumerate(reviews,1):
        journal(f'reviewer-{i}',review)
    receipt = {'status':'SUBMITTED_HUMAN_REVIEWS_TRANSCRIBED_NOT_ADJUDICATED',
        'source_sha256':source_sha,'packet_manifest_sha256':args.packet_sha,
        'user_authorship_clarification':'俩个人分别填写合并的',
        'user_attested_separate_human_authorship':True,
        'reviewer_independence_verified':False,
        'no_model_assistance_self_reported':all(r['reviewer']['assistance_used']=='否' for r in reviews),
        'opening_dual_perspective_wording_preserved':True,
        'reviewer_slots':2,'ratings_per_reviewer':12,'quote_delimiter_adjustments':changes,
        'semantic_labels_created_by_importer':False,'new_model_calls':0}
    journal('receipt',receipt)
    journal('importer-source',{'source_utf8':Path(__file__).read_text('utf-8')})
    journal.seal()
    print(json.dumps({'status':receipt['status'],'reviewer_slots':2,'ratings':24,
                      'quote_delimiter_adjustments':len(changes),'new_model_calls':0}))


if __name__ == '__main__':
    main()
