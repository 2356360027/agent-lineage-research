"""Unfilled staged review packets from replayed visible events.

In-memory preparation only. Release requires a separate complete archive,
accounting and tool audit. Stage B must remain withheld until Stage A reviews
are locked. Condition masking is partial: exact model wording may reveal it.
"""
from copy import deepcopy
import hashlib

from runtime.student_club_definition_replay import replay
from runtime.student_club_diagnostic_design import build,canonical

GUIDE=(
    '独立阅读原始需求与模型原话，按整体语义判断；不要把字段位置、格式差异或非关键省略判作需求冲突。'
    '只描述可观察输出，不推断隐藏思维。结论可为一致、明确冲突、信息不足；均需引用原句。'
    '不要借助模型代写判断，不要求同意研究假设。空白不是同意或零错误。')


def prepare(common,histories,*,masking_seed):
    jobs=build(common)['jobs']
    if type(masking_seed) is not str or len(masking_seed)<16:raise ValueError('Private masking seed required')
    if type(histories) is not dict or set(histories)!={j['id'] for j in jobs}:
        raise ValueError('All twelve conditions required; never select favorable traces')
    traces={j['id']:replay(histories[j['id']],common,j) for j in jobs}
    def code(text):return hashlib.sha256((masking_seed+'|'+text).encode()).hexdigest()[:16]
    task={k:common[k] for k in ('current_request','current_evidence','schema_projection')}
    stage_a=[];stage_b=[];mapping={};coverage=[]
    for job in jobs:
        trace=traces[job['id']];events=trace['events']
        definitions={e['event_id']:e for e in events if e['kind']=='definition_recorded'}
        initial=next(iter(definitions.values()),None)
        if initial:
            if initial['value']['previous_definition'] is not None or initial['value']['observation']!='pre_result':
                raise ValueError('Initial definition linkage is invalid')
            ident='D-'+code(job['id']+'|'+initial['event_id'])
            stage_a.append(dict(item_id=ident,task=deepcopy(task),definition=initial['value']['text'],
                assessment=None,evidence_quote=None,reason=None))
            mapping[ident]=dict(job_id=job['id'],event_id=initial['event_id'],role='initial_definition')
        programs=[e for e in events if e['kind']=='action_recorded' and e['value']['action']['kind']=='run_sql']
        linked=0
        for program in programs:
            definition=definitions.get(program['value']['definition_event'])
            if definition is None:continue
            if definition['event_id']>=program['event_id']:raise ValueError('Definition must precede program')
            ident='P-'+code(job['id']+'|'+program['event_id']);linked+=1
            stage_b.append(dict(item_id=ident,task=deepcopy(task),
                preceding_definition=definition['value']['text'],
                definition_timing=definition['value']['observation'],
                definition_is_revision=definition['value']['previous_definition'] is not None,
                issued_sql=program['value']['action']['sql'],sql_execution_verified=False,
                definition_vs_request=None,program_vs_definition=None,evidence_quote=None,reason=None))
            mapping[ident]=dict(job_id=job['id'],definition_event=definition['event_id'],
                action_event=program['event_id'],role='later_program')
        coverage.append(dict(job_id=job['id'],state=trace['state'],
            definition_count=len(definitions),issued_programs=len(programs),linked_programs=linked,
            interpretation_status='UNASSESSED',
            missing_link_is_not_negative=True))
    reviewers=[]
    for number in (1,2):
        def ordered(items):return sorted(deepcopy(items),key=lambda x:code(str(number)+'|'+x['item_id']))
        identity=dict(reviewer_code=None,actual_datetime=None,independent_judgment=None,
            model_assistance_scope=None,prior_material_exposure=None)
        reviewers.append(dict(reviewer_number=number,identity=identity,
            stage_a=dict(instructions=GUIDE,items=ordered(stage_a),locked=False),
            stage_b=dict(release_only_after_both_stage_a_locked=True,
                instructions='逐一判断先前定义与需求是否冲突、后续SQL是否符合该定义。二者分开判断；缺失链接不能判零。'+GUIDE,
                items=ordered(stage_b))))
    return dict(status='UNFILLED_REVIEW_DRAFT_NOT_RELEASE_AUTHORIZED',reviewers=reviewers,
        PRIVATE_mapping=mapping,PRIVATE_coverage=coverage,
        partial_masking_only=True,semantic_labels_generated=0,independent_human_reviews_received=0,
        task_pairs=1,dependent_conditions=12,ordinary_contract_inferred_from_sql=False,
        caller_must_authenticate_archive_and_provider_and_tools=True,
        content_sha256=hashlib.sha256(canonical(histories).encode()).hexdigest())
