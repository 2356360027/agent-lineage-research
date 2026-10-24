"""Prepare blank review text from fixed public questions; no SQL or inference."""
import json
from pathlib import Path
from scripts.audit_cache_action import read, sha, require, no_links, snapshot


def prepare(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    no_links(output); require(not output.exists(), 'Never overwrite a review packet')
    config_path = project/'configs/cache_next_task_admission_20261024.json'
    config = read(config_path)
    source = project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001/arcwise_plat_full_with_diff.json'
    no_links(source); require(sha(source) == config['source_sha256'], 'Fixed source differs')
    rows = read(source); require(len(rows) == 498, 'Unexpected source size')
    index = {int(row['question_id']):row for row in rows}
    require(len(index) == len(rows), 'Duplicate source IDs')
    old = read(project/config['source_screen'])
    old_pairs = {(p['historical_question_id'],p['current_question_id']) for p in old['pairs']
                 if p['disposition'] == 'SHORTLIST_FOR_TASK_REVIEW_NOT_EXECUTION'}
    require({(p['history_id'],p['current_id']) for p in config['pairs']} == old_pairs,
            'Do not silently change existing shortlist')
    selected = {}
    for pair in config['pairs']:
        for qid in (pair['history_id'],pair['current_id']):
            require(index[qid]['db_id'] == pair['database'], 'Database differs')
            selected[str(qid)] = index[qid]
    intro = [
        '新任务候选复核：空白表，不是实验结果',
        '仅有两组既有开发候选，不是未见确认集。不要求支持研究假设。',
        '请两人分别填写，不互看判断；可填写不明确、无法判断或建议排除。',
        '先完成第一部分，再看第二部分。顺序仅靠自行遵守，不宣称完全盲法。',
        '第二部分的补充历史是研究者设想，不是原生用户对话或已经执行的缓存。',
        '公开问题及evidence按固定Arcwise-Plat原文提供；不提供答案数值或参考SQL来引导第一部分。',
        '来源：uiuc-kang-lab/text_to_sql_benchmarks，固定提交 fe766045c55b6875a43b30e9ac7683df5582f8cf。',
        '原文本许可CC BY-SA 4.0；本表配对、问题和控制设想为研究者增补。',
        '', '复核者代号：', '日期：', '是否独立完成：', '是否借助模型/他人（说明范围）：',
        '此前是否接触过这些题目、SQL、结果或研究讨论（请说明）：', '', '第一部分 当前需求独立解释']
    lines = list(intro)
    for pair in config['pairs']:
        row = index[pair['current_id']]
        lines += ['', '='*50, '当前题号：'+str(pair['current_id']), '原题：'+row['question'],
                  '公开辅助说明：'+row['evidence'], '数据库：'+row['db_id'],
                  '请独立说明要查什么（实体、范围、过滤、聚合、去重、输出、单位）：',
                  '哪些要求明确，哪些需要补充？不要自行替用户补决定：',
                  '是否存在多种合理解释？请说明：', '缺失值、空集、并列是否影响确定答案？',
                  '结论（明确/需澄清/无法判断）与理由：']
    lines += ['', '第一部分完成时间：', '是否在填写前已看过第二部分或参考SQL：',
              '', '第二部分 历史关系与正负控制审查']
    for pair in config['pairs']:
        row = index[pair['history_id']]
        lines += ['', '='*50, f"历史题号：{pair['history_id']}；当前题号：{pair['current_id']}",
                  '历史原题：'+row['question'], '历史公开辅助说明：'+row['evidence'],
                  '请先独立解释历史请求，再比较它与当前请求：',
                  '仅有历史结果能否回答当前请求？如不能，缺什么？',
                  '研究者拟议正控制（不是既成事实）：'+pair['proposed_constructed_positive_control'],
                  '此控制是否足够且仍忠于当前请求？反例或边界：',
                  '需要核对的具体问题：', *['- '+s for s in pair['open_gates']],
                  '逐项意见（可保留未决，不要求提出SQL）：',
                  '是否建议纳入后续只读适配（是/否/待澄清）及理由：']
    output.mkdir(parents=True,exist_ok=False)
    for n in (1,2):
        with (output/f'REVIEWER_{n}.txt').open('x',encoding='utf-8') as f:
            f.write('\n'.join(lines)+'\n')
    def save(name,value):
        with (output/name).open('x',encoding='utf-8') as f: json.dump(value,f,ensure_ascii=False,indent=2)
    save('PRIVATE_original_records.json',selected)
    save('receipt.json',dict(status='BLANK_NATIVE_CANDIDATE_REVIEW_ONLY',
        source_sha256=sha(source),config_sha256=sha(config_path),
        current_ids=[p['current_id'] for p in config['pairs']],human_approvals=0,
        formal_tasks_admitted=0,new_SQL_calls=0,new_model_calls=0,
        development_exposed=True,reading_order_enforced=False,
        source_file_sha256=sha(__file__)))
    hashes=snapshot(output); save('artifact_hashes.json',hashes)
    actual=snapshot(output); actual.pop('artifact_hashes.json')
    require(actual==read(output/'artifact_hashes.json'),'Written packet verification failed')
    return read(output/'receipt.json')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parents[1],a.output),indent=2))
