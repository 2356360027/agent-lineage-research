"""Prepare blank independent reviews and optionally fetch one fixed SQLite file.

Reuses verified existing metadata, not a new benchmark split or permission to
run models. All source bytes and historical records remain unchanged.
"""
import argparse
import json
import shutil
from pathlib import Path

from scripts import fetch_bird_fixed as fetch
from scripts.audit_cache_action import snapshot
from runtime.arcwise_compatibility import verify_arcwise

ARCWISE_SHA = 'baefe2ca4fbab86c000df72aad9eaa563f7d422e425af2cbff071f656ea4eea8'
ENTRY = 'minidev/MINIDEV/dev_databases/superhero/superhero.sqlite'


def review_text(records, reviewer):
    lines = [
        f'728/733 原题候选复核：复核者 {reviewer}（空白表）',
        '请两人分别阅读、独立填写，不互看结论；允许不明确、无法判断或建议排除。',
        '请勿要求模型代写判断。若使用了工具或他人帮助，请说明范围。',
        '这是开发候选，不是已通过的样本，不要求支持复用或某个研究假设。',
        '第一部分不提供 SQL、执行结果、模型答案或研究者推荐解释。',
        '先完成第一部分，再看第二部分；阅读顺序靠自报，不能称为严格盲法。',
        '可跳过任何不确定的问题并说明原因，不需要猜答案或编写 SQL。',
        '', '复核者代号：', '日期：', '是否独立完成：',
        '是否借助模型/他人（说明范围）：',
        '此前是否见过这些题目、参考 SQL、答案或相关讨论（说明范围）：',
        '', '第一部分：独立解释原始需求',
    ]
    for qid in (733, 728):
        row = records[qid]
        lines += ['', '=' * 50, f'题号：{qid}', '原题：' + row['question'],
                  '原公开辅助说明：' + (row['evidence'] or '(无)'),
                  '用自己的话描述要求的实体、范围、条件、统计与输出：',
                  '哪些要求明确？哪些存在多种合理解释？',
                  '缺失值、无匹配记录、并列、重复实体是否需要额外约定？',
                  '结论（明确/需澄清/无法判断）及依据：']
    lines += ['', '第一部分完成时间：', '填写第一部分前是否看过第二部分或外部答案：',
              '', '第二部分：研究者配对的可适配性（不是自然发生的用户对话）',
              '设想先执行 728，随后收到 733。只保存历史请求本来要求返回的内容，不额外增加字段。',
              '根据你的原题解释，728 应返回哪些行和列？',
              '728 中 most common 与 each eye color 的关系如何理解？是否唯一确定输出范围？如不确定，不必二选一。',
              '仅有上述历史结果，能否回答 733？请说明条件、缺失信息或反例。',
              '若某颜色未出现在历史结果中，能够推断什么，不能推断什么？',
              '需要查数据库才能确认的事项有哪些？',
              '是否建议进入只读适配（是/否/待澄清）及理由：',
              '是否保留第一部分的原始判断？如修改，请保留原判断并说明原因：',
              '', '来源与使用说明：',
              '公开题目：Jin, Choi, Zhu and Kang, Pervasive Annotation Errors Break Text-to-SQL Benchmarks and Leaderboards (2026), Arcwise-Plat。',
              '仓库 https://github.com/uiuc-kang-lab/text_to_sql_benchmarks',
              '固定版本 fe766045c55b6875a43b30e9ac7683df5582f8cf，data/arcwise_plat_full_with_diff.json。',
              '原题及辅助说明逐字保留；配对、中文复核问题为研究者新增，不是原数据的用户对话。',
              '本复核材料按 CC BY-SA 4.0 提供：https://creativecommons.org/licenses/by-sa/4.0/',
              '请不要在答卷中填写真实姓名、联系方式、密码或密钥。填写内容仅作任务准入复核，不是模型训练标签。']
    return '\n'.join(lines) + '\n'


def prepare(project, output, download=False):
    project, output = Path(project).resolve(), Path(output).resolve()
    fetch.absent(output)
    old = project/'runtime/runs/datasets/bird-mini-fixed-20261005-002'
    metadata = fetch.validate_metadata(old)
    source_dir = project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001'
    _, records = verify_arcwise(source_dir)
    if fetch.file_receipt(source_dir/'arcwise_plat_full_with_diff.json')['sha256'] != ARCWISE_SHA:
        raise ValueError('Unexpected task source')
    if any(records[i]['db_id'] != 'superhero' for i in (728, 733)):
        raise ValueError('Unexpected database')
    listing = fetch.read_json(old/'zip_inventory.json')
    selected = [e for e in listing['entries'] if e['filename'] == ENTRY]
    if len(selected) != 1 or selected[0]['bytes'] != 237568 or selected[0]['compressed_bytes'] != 104094:
        raise ValueError('Unexpected member or size')
    output.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copyfile(__file__, output/'preparation_source.py')
        shutil.copyfile(fetch.__file__, output/'fetch_source.py')
        for name in ['source_manifest.json', *metadata['files'], *metadata['legacy_question_files']]:
            shutil.copyfile(old/name, output/name)
        fetch.validate_metadata(output)
        fetch.write_json_exclusive(output/'selected_db_ids.json', ['superhero'])
        fetch.write_json_exclusive(output/'preparation.json', {
            'status': 'PREPARED_NO_HUMAN_ADMISSION',
            'metadata_origin': 'Verified byte copies from bird-mini-fixed-20261005-002; original acquisition time and transfer receipts retained, not newly downloaded metadata',
            'task_source_sha256': ARCWISE_SHA, 'selected_member': selected[0],
            'license_card_sha256': metadata['files']['README.md']['sha256'],
            'declared_dataset_license': 'CC-BY-SA-4.0 per pinned BIRD dataset card; not legal adjudication of each upstream component',
            'download_requested': download,
            'compatibility': 'Legacy official BIRD SQLite object; not a verified corrected Arcwise database',
            'sql_calls': 0, 'model_calls': 0, 'formal_tasks_admitted': 0,
            'previous_goal_turn': 'Progress: static screen and 74 synthetic relation checks, no model evidence',
        })
        packet = output/'review'
        packet.mkdir()
        for n in (1, 2):
            with (packet/f'REVIEWER_{n}.txt').open('x', encoding='utf-8') as handle:
                handle.write(review_text(records, n))
        fetch.write_json_exclusive(packet/'receipt.json', {
            'status': 'BLANK_REVIEW_ONLY', 'human_approvals': 0, 'reading_order_enforced': False,
            'original_text_ids': [733, 728], 'task_source_sha256': ARCWISE_SHA,
            'contains_reference_sql_or_results': False,
        })
        result = {'status': 'PREPARED_ONLY', 'model_calls': 0, 'sql_calls': 0, 'formal_tasks_admitted': 0}
        if download:
            fetched = fetch.databases_stage(output, timeout=20)
            result.update(status='ONE_LEGACY_DATABASE_DOWNLOADED_NOT_ADMITTED', acquisition=fetched)
        fetch.write_json_exclusive(output/'preparation_summary.json', result)
    except BaseException as exc:
        fetch.write_json_exclusive(output/'preparation_failure.json', {'type': type(exc).__name__, 'message': str(exc), 'automatic_retry': False})
        raise
    finally:
        hashes = snapshot(output)
        fetch.write_json_exclusive(output/'preparation_artifact_hashes.json', hashes)
    actual = snapshot(output)
    actual.pop('preparation_artifact_hashes.json')
    if actual != hashes:
        raise ValueError('Archive changed')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parents[1], args.output, args.download), indent=2))
