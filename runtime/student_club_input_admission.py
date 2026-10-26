"""Read-only admission of pinned development inputs, NOT inference approval.

Hashes bind local evidence, not independent human identity or universal SQL
semantics. Private scoring data is deliberately separate from public prompts.
No database query, model call, network request or downloaded code execution.
"""
from copy import deepcopy
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime.arcwise_compatibility import verify_arcwise
from runtime.cache_intent_tools import valid_result
from runtime.student_club_diagnostic_design import build, canonical
from scripts.audit_cache_action import no_links, snapshot
from scripts.audit_student_club_structure import DB_SHA, TASK_SHA
from scripts.check_student_club_reference import scalar, sealed
from scripts.fetch_bird_fixed import read_json

VERSION = 'student-club-pinned-input-admission-v1'
PINS = {
    'data': ('datasets/student-club-fixed-20261026-002', 'preparation_artifact_hashes.json',
             'be4a94f661392566dacda976dd3bdc2f478448297b5d9b04e7cf3a0149f66be4'),
    'structure': ('student-club-structure-20261026-001', 'artifact_hashes.json',
                  'b244e5279fbb17a51cd08319f4705d59a3dfe86312534079c2a48b336f1db601'),
    'reference': ('student-club-reference-20261026-001', 'artifact_hashes.json',
                  '87f7a03c676893c157a1bd87fb88da086c3559107b039373904e88c694000f84'),
    'draft': ('student-club-diagnostic-draft-20261026-001', 'draft_artifact_hashes.json',
              '5b4d0631300bad7bc85cd979a3744da5db80e89560f3c21ddb4dc6bfb2fc3d34')}


def verify_pinned(directory, manifest, expected):
    directory = Path(directory)
    no_links(directory)
    no_links(directory/manifest)
    if audit.hash_file(directory/manifest) != expected:
        raise ValueError('Pinned manifest differs')
    sealed(directory, manifest)


def validate_contents(common, tasks, reference_plan, summary, responses, process, supplement, original_sha):
    """Pure consistency checks after file identities were authenticated."""
    build(common)  # Exact public-field boundary excludes oracle/review fields.
    h, c = tasks[1371], tasks[1317]
    expected = dict(current_request=c['question'], current_evidence=c['evidence'],
        history_request=h['question'], history_evidence=h['evidence'], history_sql=h['SQL'],
        database_sha256=DB_SHA)
    if (any(common[k] != v for k,v in expected.items())
            or any(t['db_id'] != 'student_club' for t in (h,c))
            or reference_plan['database_sha256'] != DB_SHA or reference_plan['task_sha256'] != TASK_SHA
            or reference_plan['queries']['original_H'] != h['SQL']
            or reference_plan['queries']['original_C'] != c['SQL']):
        raise ValueError('Task, query or snapshot binding differs')
    if (process['original_sha256'] != original_sha or supplement['original_sha256'] != original_sha
            or process['independent_before_model_judgment_confirmed_by_user'] is not True
            or supplement['reviewer_2_readonly_negative_control_agreement'] is not True
            or supplement['students_means_relevant_members_no_extra_identity_filter'] is not True):
        raise ValueError('Human process or admission supplement missing')
    if set(responses) != {'original_H','original_C','entity_H','entity_C'}:
        raise ValueError('All four fixed reference results required')
    values = {}
    for name, response in responses.items():
        valid_result(response)
        values[name] = scalar(response)
    if (summary['status'] != 'REFERENCE_CHECK_COMPLETE'
            or summary['original_and_entity_counts_agree'] is not True
            or summary['database_unchanged'] is not True or summary['counts'] != values
            or any(values['original_'+x] != values['entity_'+x] for x in ('H','C'))):
        raise ValueError('Reference evidence inconsistent')
    cache = deepcopy(responses['original_H'])
    cache['sql_sha256'] = audit.digest(h['SQL'])
    return cache


def load(project):
    """Returns PRIVATE operator packet; never pass this whole object to a model."""
    project = Path(project).absolute()
    no_links(project)
    runs = project/'runtime/runs'
    paths = {}
    for label, (rel, manifest, pin) in PINS.items():
        paths[label] = runs/rel
        verify_pinned(paths[label], manifest, pin)
    db = paths['data']/'databases/student_club/student_club.sqlite'
    if audit.hash_file(db) != DB_SHA:
        raise ValueError('Database changed')
    taskdir = runs/'datasets/arcwise-plat-fixed-20261012-001'
    _, tasks = verify_arcwise(taskdir)
    if audit.hash_file(taskdir/'arcwise_plat_full_with_diff.json') != TASK_SHA:
        raise ValueError('Task source changed')
    reference, draft = paths['reference'], paths['draft']
    plan = read_json(reference/'plan.json')
    if plan['structure_seal_sha256'] != PINS['structure'][2]:
        raise ValueError('Structure audit binding differs')
    if snapshot(reference/'private_review') != plan['input_review_hashes']:
        raise ValueError('Archived reviews differ from reference plan')
    live_review = runs/'student-club-review-intake-20261026-001'
    if snapshot(live_review) != plan['input_review_hashes']:
        raise ValueError('Current review receipts changed; new admission required')
    common = read_json(draft/'PUBLIC_INPUTS_PRIVATE_DISTRIBUTION.json')
    design = build(common)
    if canonical(design) != canonical(read_json(draft/'PRIVATE_draft_plan.json')):
        raise ValueError('Prospective design changed; do not reuse old frozen draft')
    summary = read_json(reference/'summary.json')
    binding = read_json(draft/'PRIVATE_reference_binding.json')
    if binding != dict(not_for_model_input=True, source_seal_sha256=PINS['reference'][2], summary=summary):
        raise ValueError('Draft oracle binding differs')
    cache = validate_contents(common, tasks, plan, summary,
        {name:read_json(reference/(name+'.response.json')) for name in ('original_H','original_C','entity_H','entity_C')},
        read_json(live_review/'process_confirmation.json'), read_json(live_review/'admission_supplement.json'),
        audit.hash_file(live_review/'submitted_reviews_original.txt'))
    receipt = dict(version=VERSION, status='PINNED_DEVELOPMENT_INPUTS_VERIFIED_ONLY',
        seals={k:v[2] for k,v in PINS.items()}, database_sha256=DB_SHA, task_sha256=TASK_SHA,
        public_input_sha256=audit.digest(common), cache_sequence_sha256=cache['sequence_sha256'],
        review_hashes=deepcopy(plan['input_review_hashes']), research_pairs=1,
        paid_approval=False, source_runtime_frozen=False, model_calls=0, database_queries=0,
        human_process_evidence='User confirmation and archived forms; not independent identity verification',
        generalization='Researcher-paired development case on legacy snapshot, not corrected benchmark certification')
    return dict(common=common, design=design, cached_result=cache, database_path=str(db),
        private_scoring_reference=summary, receipt=receipt)
