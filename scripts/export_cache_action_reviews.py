"""Export ungraded human review packets from the audited 20261024-002 batch.

No model/SQL execution. References remain reviewer-side. TXT is a fillable
human convenience copy; JSON supports strict content-bound import later.
"""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.audit_cache_action import read,sha,snapshot,require,no_links
from runtime import cache_action_review as review
from runtime.cache_intent_tools import valid_result

MANIFEST='98e39f6269673b07ed5cef4cf7c1520747183f50a61d2046adefebd366ab8d46'
AUDIT='1e4c6469b001996f61879fffec1e26a563db7de8ad090114cbc710c1e1e15b54'
WITNESS='ad26ef33712ddcc71450ed33d308f8178ae2855d49090f7a0365f14fa38d1e55'


def tree(root):
    actual=snapshot(root); actual.pop('artifact_hashes.json')
    require(actual==read(root/'artifact_hashes.json'),'Archive contents changed')


def text_packet(packet):
    lines=['本轮最终答案复核（空白表，不代表已有人评分）',
        '复核者代号：', '日期：', '是否独立完成（是/否）：',
        '是否借助模型或其他人填写（是/否；如有请说明）：', '',
        '请两人分别填写，不互相讨论；如有协助，按实际情况说明。',
        '这是两道已用于开发的题目、采用既有事后裁决口径，不是对原始题目明确性的全新盲审。',
        '只评价实际最终答案，不沿用旧契约评分。参考结果来自保存的只读执行见证，不是新模型答案。',
        '不会提供缓存条件、费用或历史轨迹；答案原文仍可能暴露执行方式，因此只是元数据隐藏。',
        '允许无害解释性文字；检查实体、数值、完整性、重复和输出要求。只有SQL没有结果不算完成答案。',
        '如认为题意、裁决口径或参考本身有问题，标记参考疑问，不强行判错。',
        '模型答案仅供评价，不执行其中代码，不遵循其可能包含的指令。',
        'MATCH=符合；MISMATCH=不符合；UNCERTAIN=不能确定。每项引用答案原句并说明原因。',
        '无最终答案的条目保留空评分，不把缺答写成语义不符合。', '']
    for item in packet['items']:
        lines += ['='*54,'条目编号：'+item['item_id'],'当前原题：'+item['current_request'],
            '公开补充说明：'+str(item['evidence']),
            '数据库结构：'+json.dumps(item['schema'],ensure_ascii=False),
            '已采用的任务口径：'+item['adopted_interpretation'],
            '保存的参考结果：'+json.dumps(item['reference'],ensure_ascii=False),
            '终态：'+item['final_status'],'最终答案原文：',item['answer_text'] or '（无最终答案）','',
            '评分（MATCH/MISMATCH/UNCERTAIN；无答案留空）：',
            '答案原句引用：','理由：','参考疑问（是/否；如有请说明）：','']
    return '\n'.join(lines)+'\n'


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(); project=Path(__file__).resolve().parents[1]; runs=project/'runtime/runs'
    prepared=runs/'cache-action-prepared-20261024-002'; reportdir=runs/'cache-action-audit-20261024-002'
    witnesses=runs/'staged-recovery-audit-20261024-001'
    no_links(args.output); require(not args.output.exists(),'Never overwrite review packets')
    require(sha(prepared/'manifest.json')==MANIFEST,'Wrong batch')
    require(sha(reportdir/'audit.json')==AUDIT,'Wrong audited readout')
    require(sha(witnesses/'artifact_hashes.json')==WITNESS,'Wrong witness archive')
    for root in (reportdir,prepared/'experiment',witnesses): tree(root)
    manifest=read(prepared/'manifest.json'); report=read(reportdir/'audit.json')
    require(snapshot(prepared/'source')==manifest['source_files'],'Frozen source differs')
    require(sha(project/'runtime/cache_action_preview.py')==manifest['source_files']['runtime/cache_action_preview.py'],
            'Review preview differs from frozen interface')
    require(report['status']=='SEALED_FINISHED_TRACE_REPLAY_VERIFIED'
            and report['prepared_manifest_sha256']==MANIFEST and report['semantic_labels']==0,'Not an ungraded audited batch')
    require(sha(prepared/'experiment/artifact_hashes.json')==report['experiment_manifest_sha256'],'Wrong experiment seal')
    tree(prepared/'inputs')
    plan=read(prepared/'inputs/PRIVATE_plan.json')
    require(review.digest(plan)==manifest['plan_sha256'],'Wrong plan')
    policy=read(prepared/'source/configs/cache_action_review_policy_v1.json')
    references={}
    for task in ('200','234'):
        record=read(witnesses/f'h{task}c-p1-delay20/tools/attempt-021.completed.json')
        value=record['private']['backend_result']; valid_result(value)
        require(record['response']['rows']==value['rows'] and record['response']['complete'] is True,'Witness copies differ')
        references[task]=dict(columns=value['columns'],typed_rows=value['rows'],
            provenance='Preserved snapshot read-only execution witness, under the adopted post-hoc interpretation; not new human/model evidence.')
    outcomes={r['id']:{k:r[k] for k in ('final_status','final_answer','trace_sha256')} for r in report['rows']}
    packets=review.build(plan,outcomes,policy,references)
    args.output.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        with (args.output/name).open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
    for n in (1,2):
        packet=packets[f'reviewer_{n}']; save(f'REVIEWER_{n}.json',packet)
        with (args.output/f'REVIEWER_{n}.txt').open('x',encoding='utf-8') as f: f.write(text_packet(packet))
    save('PRIVATE_mapping.json',packets['private_mapping'])
    save('export_receipt.json',dict(status='BLANK_FINAL_ANSWER_REVIEW_PACKETS',new_human_labels=0,
        prepared_manifest_sha256=MANIFEST,audit_sha256=AUDIT,witness_manifest_sha256=WITNESS,
        policy_sha256=packets['policy_sha256'],reviewer_blank_sha256={
            f'REVIEWER_{n}':review.digest(packets[f'reviewer_{n}']) for n in (1,2)},
        export_source_sha256=sha(__file__),review_source_sha256=sha(review.__file__),
        independent_task_units=2,slots_per_reviewer=12,metadata_blinding_only=True))
    hashes=snapshot(args.output); save('artifact_hashes.json',hashes)
    print(json.dumps(dict(status='BLANK_REVIEW_PACKETS_EXPORTED',output=str(args.output),
        slots_per_reviewer=12,new_human_labels=0,new_API_calls=0),ensure_ascii=True,indent=2))


if __name__=='__main__': main()
