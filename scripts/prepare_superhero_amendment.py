"""Prepare a separately versioned, unapproved researcher clarification.

No database execution, model calls, automatic human labels, or altered sources.
Do not use this packet as independent confirmation of the original benchmark.
"""
import argparse
import shutil
from pathlib import Path

from runtime.arcwise_compatibility import verify_arcwise, REVISION
from runtime.bird_readonly_audit import hash_file
from scripts.fetch_bird_fixed import absent, write_json_exclusive
from scripts.audit_cache_action import snapshot
from scripts.prepare_superhero_assets import ARCWISE_SHA

VERSION = '728-complete-groups-researcher-clarification-v1'
CLARIFICATION = '''For this adapted task, return all observed eye-colour groups, not a
top-k subset. Count superhero records by their non-null id, not distinct names.
Include only superhero records whose publisher_id matches a publisher id with
publisher_name exactly 'Marvel Comics', and whose eye_colour_id matches a colour id.
Group by the colour name using case-sensitive binary text equality. Distinct colour
ids with the same name belong to the same group. Retain matched colour records
even when their name is null or an unknown/placeholder label; null names form one
group. Do not add zero-count groups for dictionary entries with no matching hero.
Return the colour name, count and frequency rank. A group's rank is one plus the
number of groups with strictly larger counts: ties share rank and leave gaps.
The order of returned rows is not part of the required answer. Return every group
in full; no row limit, frequency threshold, pagination omission or display truncation.'''


def review_text(records, reviewer):
    lines = [f'728 修订版与 733 配对复核：复核者 {reviewer}（空白表）',
             '版本：' + VERSION,
             '这是研究者新增限制的开发任务，不是对原题唯一正确解释的声明。',
             '原题 728 的待澄清结论保留；本表不能用来倒改原判断或冒充自然历史对话。',
             '仅核查修订内容及其影响，不必重填旧表。允许不明确、不适合或建议排除。',
             '两人分别填写，不互看。请勿由模型代写判断；工具只排版也请注明。',
             '你可能已读过原题讨论，本次不称为新的盲审或独立确认集。',
             '先填第一部分再看第二部分；阅读顺序仅自报。请勿查执行答案。',
             '不要填写姓名、联系方式或密钥。', '',
             '复核者自选代号：', '实际填写日期和时间：', '是否独立判断：',
             '是否借助模型或他人及具体范围：',
             '已接触原题、研究讨论、参考程序或答案的范围：', '',
             '第一部分：修改是否清楚且仍适合作为任务？', '',
             '728 原题（逐字保留，仅作比较）：' + records[728]['question'],
             '728 原公开辅助说明：' + (records[728]['evidence'] or '(无)'), '',
             '728 新增研究者限定（英文是拟进入实验的确切文本）：', CLARIFICATION, '',
             '修改摘要（仅帮助定位，不作为另一套题干）：',
             '明确全量分组、按记录计数、名称分组及比较规则、缺失/占位值、并列排名和输出范围；不要求输出行次序。',
             '请用自己的话概括新增限定后的历史任务：',
             '新增限定中是否仍有矛盾、含糊、不可实施或不自然的地方？请指出：',
             '这些限定是否改变了你对原题的理解？哪些是额外约定而不是原题必然要求？',
             '这版任务是否适合受控开发实验（适合/需修改/不适合/无法判断）及依据：',
             '第一部分完成时间：', '完成前是否已读第二部分或外部材料：', '',
             '第二部分：配对边界（当前题未改写）',
             '733 原题：' + records[733]['question'],
             '733 原公开辅助说明：' + (records[733]['evidence'] or '(无)'),
             '研究者设想：同一固定快照，先执行修订历史任务，再收到 733。',
             '仅保存该修订历史任务本身要求返回的结果，不为 733 增加额外缓存字段。',
             '只根据这份历史结果，能否回答当前题？请给出条件、反例或无法判断的原因：',
             '某个颜色未出现在结果时，你能推断什么？需要哪些前提？',
             '若结果被截断、过滤或来自不同快照，判断是否改变？',
             '733 是否还存在会影响配对的统计粒度或其他未决问题？',
             '是否同意该修订配对进入只读适配（是/否/待澄清）及依据：',
             '你是否认为必须改动当前题才能成立？如是，请说明，不自行替换原题：',
             '是否保留原题 728 的旧判断？若修改旧判断，请另记理由，勿覆盖旧表：', '',
             '来源：Arcwise-Plat, Jin, Choi, Zhu and Kang (2026).',
             'https://github.com/uiuc-kang-lab/text_to_sql_benchmarks',
             '固定版本：' + REVISION,
             '原题及辅助说明来自 data/arcwise_plat_full_with_diff.json；新增英文限定、配对和复核问题为研究者改编。',
             'CC BY-SA 4.0：https://creativecommons.org/licenses/by-sa/4.0/']
    return '\n'.join(lines) + '\n'


def prepare(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    absent(output)
    source = project/'runtime/runs/datasets/arcwise-plat-fixed-20261012-001'
    _, records = verify_arcwise(source)
    if hash_file(source/'arcwise_plat_full_with_diff.json') != ARCWISE_SHA:
        raise ValueError('Unexpected source hash')
    if any(records[i]['db_id'] != 'superhero' for i in (728, 733)):
        raise ValueError('Unexpected database')
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, output/'preparation_source.py')
    for n in (1, 2):
        with (output/f'REVIEWER_{n}.txt').open('x', encoding='utf-8') as handle:
            handle.write(review_text(records, n))
    write_json_exclusive(output/'adaptation.json', {
        'version': VERSION, 'status': 'DRAFT_NOT_HUMAN_APPROVED',
        'original_728': {k: records[728][k] for k in ('question', 'evidence')},
        'additional_researcher_instruction': CLARIFICATION,
        'unchanged_733': {k: records[733][k] for k in ('question', 'evidence')},
        'task_source_sha256': ARCWISE_SHA, 'source_revision': REVISION,
        'modification_basis': 'Address pending review on coverage, rank, missing values and record grain; not selected using model failures',
        'independent_confirmation_set': False, 'natural_dialogue': False,
        'reference_SQL_or_results_in_forms': False,
        'original_task_pending_unchanged': True, 'human_approvals': 0,
        'model_calls': 0, 'database_queries': 0, 'inference_authorized': False,
        'gate': 'Revised semantic review does not certify original task, paired SQL equivalence, empirical effect or methodological novelty.',
    })
    hashes = snapshot(output)
    write_json_exclusive(output/'artifact_hashes.json', hashes)
    actual = snapshot(output); actual.pop('artifact_hashes.json')
    if actual != hashes: raise ValueError('Packet changed')
    return {'status': 'BLANK_AMENDMENT_PACKET_CREATED', 'version': VERSION,
            'sealed_files': len(hashes), 'manifest_sha256': hash_file(output/'artifact_hashes.json')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(prepare(Path(__file__).resolve().parents[1], args.output))
