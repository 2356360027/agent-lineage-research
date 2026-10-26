"""Private, append-only staged review handoff. Never supplies human judgments.

Only reviewer-specific forms may be distributed. PRIVATE material is for the
organizer; filesystem permissions are not an access-control boundary here.
Identity/independence statements remain self-reports, not verified facts.
"""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import uuid

from runtime import bird_readonly_audit as audit
from runtime.cache_intent_journal import Journal,verify
from runtime.native_symmetric_batch_audit import journal,read
from runtime.native_trajectory_replay import equal
from runtime.student_club_batch_audit import inspect
from runtime.student_club_definition_replay import event_records
from runtime.student_club_review_packets import prepare
from scripts.fetch_bird_fixed import no_link_ancestors

RATINGS={'一致','明确冲突','信息不足'}
ASSISTANCE={'未使用模型','仅整理格式，未生成或修改判断'}


def export(run,plan,common,cache,*,output,masking_seed,**audit_arguments):
    """Re-audit before producing any forms; stopped batches cannot be released."""
    receipt=inspect(run,plan,common,cache,**audit_arguments)
    if receipt['status']!='SEALED_STUDENT_CLUB_BATCH_REPLAY_VERIFIED':
        raise ValueError('Complete cross-record audit required before review export')
    histories={j['id']:event_records(journal(Path(run)/j['id']/'loop')) for j in plan['design']['jobs']}
    draft=prepare(common,histories,masking_seed=masking_seed)
    output=Path(output);no_link_ancestors(output);output.mkdir(parents=True,exist_ok=False)
    private=Journal(output/'PRIVATE')
    private('audit',receipt);private('draft',draft)
    private('handoff',dict(mode=audit_arguments['mode'],archive_sha256=audit_arguments['expected_archive_sha'],
        partial_masking_only=True,distribute_only_reviewer_specific_forms=True,
        stage_b_withheld_until_two_stage_a_locks=True,human_identity_independence_verified=False))
    private.seal()
    forms=Journal(output/'stage_a')
    for reviewer in draft['reviewers']:
        forms(f"reviewer-{reviewer['reviewer_number']}",dict(reviewer_number=reviewer['reviewer_number'],
            identity=reviewer['identity'],stage_a=reviewer['stage_a'],
            allowed_assessments=sorted(RATINGS),allowed_model_assistance=sorted(ASSISTANCE),
            instructions='请另存副本填写；不要修改题目、模型原话、编号或题序。实际时间须含时区。未能判断可填信息不足并解释。仅发送本人表格，不互看答案。'))
    forms.seal()
    result=dict(status='STAGE_A_UNFILLED_EXPORTED',private_seal=audit.hash_file(output/'PRIVATE/artifact_hashes.json'),
        stage_a_seal=audit.hash_file(output/'stage_a/artifact_hashes.json'),
        independent_human_reviews_received=0,stage_b_released=False)
    audit.save_new(output/'handoff.json',result)
    return result


def check_package(root,expected_handoff_sha):
    root=Path(root);no_link_ancestors(root)
    equal(audit.hash_file(root/'handoff.json'),expected_handoff_sha,'External handoff seal')
    h=read(root/'handoff.json')
    for folder,key in [('PRIVATE','private_seal'),('stage_a','stage_a_seal')]:
        equal(audit.hash_file(root/folder/'artifact_hashes.json'),h[key],'Review folder seal')
        verify(root/folder)
    return root


def validate_form(template,filled,*,now):
    if type(filled) is not dict:raise ValueError('Review object required')
    normalized=deepcopy(filled)
    identity=filled['identity']
    equal(sorted(identity),sorted(template['identity']),'Identity fields')
    for key in ('reviewer_code','actual_datetime','model_assistance_scope','prior_material_exposure'):
        if type(identity[key]) is not str or not identity[key].strip():raise ValueError('Incomplete review identity/exposure')
    if identity['independent_judgment'] is not True or identity['model_assistance_scope'] not in ASSISTANCE:
        raise ValueError('Independent-judgment process needs clarification; do not silently certify')
    stamp=datetime.fromisoformat(identity['actual_datetime'])
    if stamp.utcoffset() is None or stamp>now:raise ValueError('Actual review time requires timezone and cannot be future')
    normalized['identity']=deepcopy(template['identity'])
    items=filled['stage_a']['items'];original=template['stage_a']['items']
    if len(items)!=len(original):raise ValueError('All issued items required')
    for index,(item,blank) in enumerate(zip(items,original)):
        if item['assessment'] not in RATINGS:raise ValueError('Missing/unsupported assessment')
        for field in ('evidence_quote','reason'):
            if type(item[field]) is not str or not item[field].strip():raise ValueError('Quote and reason required, including uncertainty')
        for field in ('assessment','evidence_quote','reason'):normalized['stage_a']['items'][index][field]=None
    equal(normalized,template,'Only answer and identity fields may change')
    return identity


def lock_stage_a(root,submission,*,reviewer_number,expected_handoff_sha):
    """Retain the original supplied bytes even if validation needs clarification."""
    root=check_package(root,expected_handoff_sha)
    if type(reviewer_number) is not int or reviewer_number not in (1,2):raise ValueError('Reviewer 1 or 2 required')
    if (root/'stage_b').exists():raise ValueError('Stage B already released; initial ratings cannot be replaced')
    target=root/'locks'/f'reviewer-{reviewer_number}'
    if target.exists():raise FileExistsError('Existing lock retained; amendments require separate provenance')
    submission=Path(submission);no_link_ancestors(submission)
    raw=submission.read_bytes()
    intake=root/'intake'/uuid.uuid4().hex;no_link_ancestors(intake);intake.mkdir(parents=True,exist_ok=False)
    with (intake/'submission.json').open('xb') as f:f.write(raw)
    try:
        template=read(root/'stage_a'/f'reviewer-{reviewer_number}.json');filled=read(intake/'submission.json')
        identity=validate_form(template,filled,now=datetime.now(timezone.utc))
        other=root/'locks'/f'reviewer-{3-reviewer_number}'
        if other.exists():
            verify(other)
            code=read(other/'submission.json')['identity']['reviewer_code']
            if code.strip().casefold()==identity['reviewer_code'].strip().casefold():raise ValueError('Different reviewer codes required')
        saved=Journal(target)
        saved('submission',filled)
        saved('receipt',dict(status='STAGE_A_SELF_REPORTED_REVIEW_LOCKED',reviewer_number=reviewer_number,
            handoff_sha256=expected_handoff_sha,raw_intake_relative=(intake/'submission.json').relative_to(root).as_posix(),
            raw_submission_sha256=audit.hash_file(intake/'submission.json'),
            locked_utc=datetime.now(timezone.utc).isoformat(),human_identity_independence_verified=False,
            model_judgments_generated=False))
        saved.seal()
    except Exception as error:
        audit.save_new(intake/'validation.json',dict(status='NEEDS_CLARIFICATION',error_type=type(error).__name__,
            original_retained=True,locked=False))
        raise
    audit.save_new(intake/'validation.json',dict(status='LOCKED_AS_SELF_REPORTED',original_retained=True))
    return dict(status='STAGE_A_SELF_REPORTED_REVIEW_LOCKED',reviewer_number=reviewer_number,
        lock_sha256=audit.hash_file(target/'artifact_hashes.json'),human_identity_independence_verified=False)


def release_stage_b(root,*,expected_handoff_sha,expected_lock_shas):
    root=check_package(root,expected_handoff_sha)
    if set(expected_lock_shas)!={1,2}:raise ValueError('Both independent lock seals required')
    codes=[]
    for number in (1,2):
        path=root/'locks'/f'reviewer-{number}'
        equal(audit.hash_file(path/'artifact_hashes.json'),expected_lock_shas[number],'External lock seal')
        verify(path);receipt=read(path/'receipt.json')
        equal(receipt['handoff_sha256'],expected_handoff_sha,'Lock belongs to this handoff')
        equal(receipt['reviewer_number'],number,'Reviewer slot')
        raw=root/receipt['raw_intake_relative'];no_link_ancestors(raw)
        if not raw.resolve().is_relative_to((root/'intake').resolve()):raise ValueError('Intake path outside package')
        equal(audit.hash_file(raw),receipt['raw_submission_sha256'],'Original intake bytes')
        filled=read(path/'submission.json');equal(filled,read(raw),'Locked original content')
        identity=validate_form(read(root/'stage_a'/f'reviewer-{number}.json'),filled,now=datetime.now(timezone.utc))
        codes.append(identity['reviewer_code'].strip().casefold())
    if codes[0]==codes[1]:raise ValueError('Different reviewers required')
    draft=read(root/'PRIVATE/draft.json');out=Journal(root/'stage_b')
    for reviewer in draft['reviewers']:
        out(f"reviewer-{reviewer['reviewer_number']}",dict(reviewer_number=reviewer['reviewer_number'],
            identity=reviewer['identity'],stage_b=reviewer['stage_b']))
    out('release',dict(status='STAGE_B_UNFILLED_RELEASED',handoff_sha256=expected_handoff_sha,
        stage_a_lock_shas={str(k):v for k,v in expected_lock_shas.items()},released_utc=datetime.now(timezone.utc).isoformat(),
        self_reported_independent_reviews=2,human_identity_independence_verified=False,semantic_labels_generated=0))
    out.seal()
    return dict(status='STAGE_B_UNFILLED_RELEASED',seal_sha256=audit.hash_file(root/'stage_b/artifact_hashes.json'))
