"""Render sealed blank review packets as UTF-8 text; never assign judgments."""
import argparse
import json
from pathlib import Path

from runtime import bird_readonly_audit as audit
from scripts.prepare_staged_contract_review import load_archive


def render(packet, reviewer):
    if (packet['status'] != 'BLANK_PRIMARY_ENDPOINT_REVIEW_NOT_RESULTS'
            or len(packet['items']) != 12
            or any(v is not None for v in packet['reviewer'].values())
            or any(v is not None for i in packet['items'] for v in i['review'].values())):
        raise ValueError('Unmodified blank twelve-item packet required')
    lines = [f'开发实验契约复核表：复核者 {reviewer}',
        '这是两道既有开发题的12份模型输出，不是12道独立新题。',
        '请两人分别填写，不互看答案；遇到不确定可以保留未决。',
        '先独立写出你对需求的理解，再阅读模型输出。不要查后续SQL、最终答案或条件映射。',
        '判断整份契约的业务含义。信息放错字段不等于遗漏；不要只按字段名判错。',
        '一致(match)：整体完整表达核心要求，无实质关键遗漏或语义冲突。',
        '冲突(contradiction)：存在与需求矛盾的明确表达。',
        '未决(unresolved)：无法确定、材料不足或仅有遗漏而不能确定冲突。',
        '关键遗漏与仅字段错位分别填写。不要把仅字段错位判为实质冲突。',
        '标签与后续结果已隐藏，但原文可能透露条件线索，不能称为完全盲法。',
        '不要求作出特定结论；不确定和分歧均保留。不得把模型代填当作独立人工复核。',
        '', '复核者自选代号：', '日期：', '是否独立完成（是/否）：',
        '此前接触过哪些题目、输出、研究讨论或答案：',
        '本次是否借助他人/模型；如有，请说明范围：',
        '', '第一部分：在看模型输出前，写下需求理解。']
    seen = set()
    for item in packet['items']:
        if item['current_request'] in seen:
            continue
        seen.add(item['current_request'])
        lines += ['', '需求原文：', item['current_request'], '数据集补充说明：', item['evidence'],
                  '数据库模式：', json.dumps(item['schema'], ensure_ascii=False, indent=2),
                  '你的需求理解（实体、过滤条件、聚合/去重、输出形式；不适用项可注明）：',
                  '歧义或保留意见：']
    lines += ['', '第二部分：逐份复核。请保留原文和编号，只填写空白答案。']
    for item in packet['items']:
        lines += ['', '='*60, '编号：'+item['item_id'], '需求原文：', item['current_request'],
                  '数据集补充说明：', item['evidence'], '模型契约原文开始：',
                  item['raw_contract'] if item['raw_contract'] is not None else '[无可用文本，保持未决]',
                  '模型契约原文结束。', '整体判断（match / contradiction / unresolved）：',
                  '是否有实质关键遗漏（是/否/未决）：',
                  '问题是否仅为字段摆放错位（是/否/未决；无问题填否）：',
                  '支持判断的模型原文片段（精确引用，不改写）：',
                  '理由（结合需求解释，不能仅凭字段名或格式）：']
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('packet', 'manifest-sha', 'output'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    root, output = Path(args.packet).resolve(), Path(args.output).resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError('Preserve sealed source packet')
    _, manifest = load_archive(root, args.manifest_sha)
    texts = {}
    for reviewer in (1, 2):
        name = f'reviewer-{reviewer}.json'
        if audit.hash_file(root/name) != manifest[name]:
            raise ValueError('Changed review source')
        texts[f'REVIEWER_{reviewer}.txt'] = render(json.loads((root/name).read_text('utf-8')), reviewer)
    output.mkdir(parents=True, exist_ok=False)
    for name, text in texts.items():
        with (output/name).open('x', encoding='utf-8-sig', newline='\n') as stream:
            stream.write(text)
    receipt = {'status':'BLANK_TEXT_FOR_HUMAN_REVIEW',
               'source_packet_manifest_sha256':args.manifest_sha,
               'text_file_sha256':{name:audit.hash_file(output/name) for name in texts},
               'semantic_labels_generated':False, 'private':True,
               'new_model_calls':0, 'automatic_text_answer_import':False}
    audit.save_new(output/'receipt.json', receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
