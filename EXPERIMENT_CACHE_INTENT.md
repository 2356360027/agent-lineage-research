# Cache convenience and request fidelity: dataset adaptation

Status (2026-10-21): **two separately archived contract-only development batches
completed; the 12-call interface comparison now has a single-reviewer,
post-hoc interpretation of core business semantics**. The earlier batch has
24 calls; the later comparison has 12 newly generated calls, not reused controls.
They are not pooled. The later comparison does not manipulate cache cost or
availability, and neither batch contains downstream program execution.
Task adjudication and output review have progressed, but independent historical
task/reuse-relation admission and a frozen, authorized tool-enabled protocol
remain separate prerequisites. No new batch is authorized by this document.

Chronology: earlier dated sections below preserve the evidence and decisions at
their own checkpoints. Their statements about no model calls, unresolved remote
access, or inference eligibility are historical, not a claim that the completed
pilot is still waiting to start. Frozen records retain their original review
status; later adjudications do not retroactively make those runs preregistered.
See the final dated update and its public aggregate receipts for current status.

Historical pre-execution status, retained for provenance:

> Status (2026-10-13): **development compatibility evidence only**. No model run
> has tested whether cache availability/cost changes the interpretation of an
> explicit request. Historical experiments do not establish this new hypothesis.

## Current question

Holding the current request, trusted historical task definitions and available
information fixed, does making a historical result materialized or cheaper to
read alter the agent's expressed task definition? Incorrect cache selection,
SQL implementation errors, ambiguous requests and wrong reference labels are
separate explanations. A changed final answer alone is not evidence of changed
request interpretation.

Public annotations reduce original task-writing work; they do not automatically
validate researcher-constructed cache histories. No project independent
task/history semantic approval has been completed. General-reader usability
feedback has since been received; see the 2026-10-13 scope check below. It does
not approve the current development task pairs. Model-generated judgments must
not be counted as independent human reviews.

## Pinned candidate

Use the **full Arcwise-Plat** question/SQL revision, not its SQL-only variant:

- Repository: https://github.com/uiuc-kang-lab/text_to_sql_benchmarks
- Commit: `fe766045c55b6875a43b30e9ac7683df5582f8cf`
- Source: `data/arcwise_plat_full_with_diff.json`
- Downloaded bytes: 332246; SHA-256:
  `baefe2ca4fbab86c000df72aad9eaa563f7d422e425af2cbff071f656ea4eea8`
- Parsed local snapshot: 498 records, 11 database IDs.
- Repository license: CC BY-SA 4.0; retain attribution and check applicable
  source/database terms before redistributing derived data.
- We downloaded the JSON, README and LICENSE only (357422 total body bytes).
  We did **not** download or verify the matching Arcwise database/schema bundle.

The acquisition command is a plan unless `--execute` is supplied:

```bash
python -m scripts.fetch_arcwise_fixed --execute
```

An explicit credential-free `--proxy` can be provided when needed. It is not
persisted in receipts. TLS validation stays enabled; the three text artifacts
are bounded to 2 MiB each / 4 MiB total. Existing directories, including failed
partial attempts, are never overwritten or automatically resumed.

## Completed local compatibility check

The existing fixed BIRD Mini-Dev snapshot (HF revision
`f65faf4ae3b638c1fa6df1d3370c8d92c8366301`, legacy official SQLite archive)
contains 500 question records and four locally acquired databases. Arcwise IDs
119 and 120 are absent relative to that question file; no reason for absence
is inferred. There are no newly added IDs in the downloaded Arcwise JSON.

We kept the **old 12 development-exposed IDs** and executed both versions, at
most 24 bounded, isolated, read-only queries:

```bash
python -m runtime.arcwise_compatibility \
  --arcwise-dir runtime/runs/datasets/arcwise-plat-fixed-20261012-001 \
  --bird-dir runtime/runs/datasets/bird-mini-fixed-20261005-002 \
  --output runtime/runs/arcwise-compatibility-20261012-001 \
  --execute
```

Use a new output directory for a deliberately authorized rerun; never erase an
existing audit. Without `--execute`, the adapter writes metadata diagnostics
only. The old BIRD acquisition and selection must already exist and verify.

Observed:

- 12/12 selected IDs and database IDs could be paired; 24/24 SQL executions
  completed; before/after hashes of all four databases matched acquisition.
- Text fields changed in 9/12 rows: question in 5, evidence in 5, SQL in 7.
  These sets overlap; a whitespace-only change also counts as a byte change.
- 5/12 pairs had different typed result bags: 234, 1500, 1481, 639, 847.
- Three rows supplied original-field metadata matching our old values; nine
  did not provide such metadata. Shared IDs alone do not prove version lineage.
- Model calls: 0. New human semantic approvals: 0. Official Arcwise environment
  equivalence: **not verified**.

These are compatibility diagnostics, not accuracy gains, semantic-equivalence
proofs, speed benchmarks or cache-effect estimates. Some question revisions
change dates/entities. Some changed requests yield equal results on this one
database; result equality alone cannot establish request fidelity. Raw outputs
and downloaded data remain under ignored `runtime/runs/`.

The aggregate receipt is in
`configs/run_records/arcwise_compatibility_20261012_001.json`.

## Tests and next decision

```bash
python -m unittest tests.test_bird_readonly_audit tests.test_bird_fetch \
  tests.test_arcwise_fetch tests.test_arcwise_compatibility -v
```

Fixtures/mocked calls are software tests, not research samples. The adapter
preserves incomplete results, duplicate rows, NULLs, types, column positions,
and independent row-order diagnostics. It never certifies task semantics.

Continue to a small development task-pair design, not broad model sampling:

1. Resolve which task and database version is being studied. Do not label a
   legacy-Mini-Dev adaptation as official Arcwise reproduction.
2. Record current task and historical task separately. A cached result must
   be correct for the historical task, with provenance independently bound.
3. Include directly reusable, repairable and nonapplicable histories; do not
   choose tasks based on the model producing a desired error.
4. Manipulate availability and cost separately; compare complete prompts/tools,
   not just nominal condition names. Keep correct recomputation feasible.
5. Distinguish diagnostic explicit-contract elicitation from ordinary agent
   execution. Contract-first and independent verification are strong baselines,
   not presumed novel methods.

AMBROSIA's explicit interpretations and CoSQL's affirmed clarifications remain
useful complementary data candidates; neither has been imported in this run.

## Development cache-history package (2026-10-12)

Constructed six candidate histories for **two already-exposed toxicology
targets**, not six independent research samples. The public current questions
and evidence are unchanged. Historical requests, SQL and candidate contracts
are researcher-constructed; human review is still pending. Adapted task material
is attributed in `configs/cache_intent_pairs_dev_v1.json` and shared under
CC BY-SA 4.0; this statement does not relicense unrelated source code.

```bash
python -m runtime.cache_intent_dev_package \
  --output runtime/runs/cache-intent-dev-package-20261012-001 --execute
```

This completed once locally: six historical SQL executions plus one schema read,
all bounded/read-only; two previously verified target outputs reused, not rerun.
Four declared cache-only operations (two identity, two transformations) matched
their target typed result bags on this snapshot. Two remaining histories omit
needed information; they remain candidate information-insufficiency controls,
not universally useless caches. Additional base-data reads may permit valid
partial reuse. Matching results neither proves semantic equivalence nor supplies
human approval. All four stored database hashes remained unchanged.

`runtime/cache_intent_prompts.py` builds offline previews only. Each history has
two separate paired contrasts: materialized availability false/true at fixed
read cost, and read cost 1/8 at fixed availability. SQL cost 10 and budget 100 are
identical across arms. They are **declared relative units**, not measured cost.
All 12 pairs (24 envelopes) were checked for a single allowed change across the
complete message/tool/response-format envelope. No model was called.

Private wrappers contain case IDs and condition labels; future runners must
send only the selected envelope, never the complete wrapper. Reference outputs,
candidate contracts, applicability labels and query results are not sent.
The explicit-contract instruction is itself a protective intervention. This is
a preview for a pre-result diagnostic, **not ordinary agent execution**, an
implemented cache-access permission system, or evidence of causal effect.

The immutable package includes blind current-task review templates; editable
copies are kept outside the archive in ignored `reviews/cache_intent_dev_v1/`.
Two reviewers should complete these independently before seeing stage-two
historical SQL/relationship suggestions. Leave original archive templates
unchanged; blank forms and assistant judgments are not reviews.

Inference remains ineligible. Before any model run, require task/history review,
a distinct sampling/analysis plan, actual tool/cost enforcement where applicable,
and current resource authorization. This tiny exposed package cannot establish
generality, publication readiness or a gap over strong reuse/recompute baselines.

```bash
python -m unittest tests.test_cache_intent_dev_package \
  tests.test_cache_intent_prompts tests.test_bird_readonly_audit \
  tests.test_bird_fetch tests.test_arcwise_fetch tests.test_arcwise_compatibility -q
```

106 software tests passed; fixture queries are mocked or isolated as documented,
and do not count as research samples. The public aggregate receipt is
`configs/run_records/cache_intent_dev_package_20261012_001.json`.

## Schema-description provenance and scoring witnesses (2026-10-12)

The upstream `data/schemas/toxicology/database_description/` resource at the
same pinned revision contains four CSV field descriptions. We acquired only
these four files (1,451 response-body bytes), preserving the exact source bytes:

```bash
python -m scripts.audit_arcwise_schema \
  --output runtime/runs/arcwise-schema-description-20261012-001 --execute
```

All four Git blob IDs and byte sizes matched the inspected upstream tree. The
11 column names and declared types matched our previously captured legacy DDL.
This **does not bind the database contents or binary version** to Arcwise full.
No SQLite database was opened by this audit. The restricted DDL parser is for
these already captured four table statements, not general SQL validation.

One literal metadata observation requires review: in `molecule.csv`, the `+/-`
value explanation is attached to the `molecule_id` record, while the `label`
record's value-description cell is blank. We preserved this placement and did
not correct the CSV or add it to prompts. This observation alone neither
invalidates a target question nor establishes an error in its reference SQL.

Keep three provenance claims separate: pinned question/SQL annotations, pinned
field-description metadata, and the as-yet-unresolved official database binary
binding. The pinned upstream README links `bird.zip` in SAR-Agent setup and
`dev.zip` in the corrected Dev subset/re-evaluation context; neither supplies a
unique full-498-to-binary hash binding. Paper v3 section 4.1 reports that its
Mini-Dev and Dev database contents differ. That paper version predates the later
Arcwise release and cannot alone certify our later JSON/binary pairing.

Separately, eight in-memory software tests now include two paired-world
counterexamples: the same ID-only cache can require different label-filtered
answers, and the same molecule-level bond count can correspond to different
atom-incidence counts. A richer cache or additional base-data reads can support
valid repair/partial reuse. These are synthetic information-sufficiency and
scoring checks, **not model outcomes or independent research samples**. Passing
finite fixtures does not prove general query equivalence or chemical realism.
Formal availability/cost contrasts must still keep the database identical
across arms; changing worlds here is only a software counterexample device.

```bash
python -m unittest tests.test_arcwise_schema tests.test_cache_intent_witnesses \
  tests.test_cache_intent_dev_package tests.test_cache_intent_prompts \
  tests.test_bird_readonly_audit tests.test_bird_fetch tests.test_arcwise_fetch \
  tests.test_arcwise_compatibility -q
```

130 software tests passed. No new model calls or human approvals occurred.
Source CSVs and diagnostic artifacts remain ignored; only the aggregate receipt
`configs/run_records/arcwise_schema_description_20261012_001.json` is public.
Stage-two recommendation: continue task-pair review and resolve/explicitly
document the data adaptation, not broad model sampling. Strong database reuse
rules plus selective base reads remain a baseline, not an established gap.

Sources:

- [Pinned upstream README](https://raw.githubusercontent.com/uiuc-kang-lab/text_to_sql_benchmarks/fe766045c55b6875a43b30e9ac7683df5582f8cf/README.md).
- [Pinned molecule description](https://raw.githubusercontent.com/uiuc-kang-lab/text_to_sql_benchmarks/fe766045c55b6875a43b30e9ac7683df5582f8cf/data/schemas/toxicology/database_description/molecule.csv).
- [Original audit paper v3, section 4.1](https://arxiv.org/html/2601.08778v3).
- [Distilled test-suite evaluation](https://aclanthology.org/2020.emnlp-main.29/)
  and [official implementation](https://github.com/taoyds/test-suite-sql-eval)
  motivate multi-database counterexamples. Our two fixtures are not a
  reproduction of their distilled test suite or metric.

## AMBROSIA admission and measurement review (2026-10-12)

Public-code inspection at `saparina/ambrosia` revision
`7de80a41f1ad4f01aed9bb55e734654688b572a8` identifies a Croissant `examples`
record set, including explicit interpretations linked to their ambiguous source
question. This is a code-interface finding, not validation of an acquired data
archive. The official download presently redirects to a password form. No
password was submitted, archive acquired, mirror used, or third-party code run.
Acquisition is pending user confirmation or a user-supplied official archive.
Respect the authors' request not to upload raw data to GitHub/Hugging Face;
that request is distinct from the declared CC BY 4.0 license.

Two adaptations must not be silent:

- The official loader appends a one-table output instruction to attachment
  questions and their family text, and rewrites the database dump. Keep original
  and processed fields separate; review any adopted output constraint explicitly.
- The original prompt uses a database dump containing values. Our tool-reuse
  experiment should expose schema before result access, not quietly provide all
  rows for free. This changes the task interface and must be called an adaptation,
  not a reproduction of the original benchmark setting.

The upstream ordinary SQLite connection, cell-flattening comparison and skipped
error paths are not our required read-only execution or failure-accounting
protocol. We have inspected their code, not recomputed their published scores.

An explicit interpretation from the same ambiguity family is a candidate
historical task, **not automatically a nonreusable cache**. Review complete
result shape, inclusion relations, transformations and necessary extra base
reads separately. Group related directions by both task family and shared
database; do not count reversed pairs as independent samples. Ambiguous source
questions stay in provenance, not in place of the current explicit request.

Measurement decision: preserve `runtime/cache_intent_prompts.py` unchanged as
a protective, pre-result explicit-contract diagnostic. It prohibits planning
and tool calls; its declared costs have no implemented execution consequences.
A null result there does not rule out task drift during actual execution, and
a nonzero result is not evidence of operational resource pressure. Do not
promote this preview to the sole test of the main hypothesis.

Before a later inference run, specify a separate tool-enabled joint execution
workflow and contract-first/independent-verifier controls. Without an observable
intent statement, an ordinary workflow can establish task-fidelity failure but
not distinguish contract drift from implementation error. If an intent statement
is required, disclose that measurement intervention and analyze it separately.
Same-history SQL, same schema, same correctness priority and affordable correct
recomputation must hold in each availability/cost contrast. Any optimization
objective and tool-cost enforcement must be fixed before seeing model outputs.

Current decision: continue data/measurement adaptation, not model sampling.
No AMBROSIA archive, semantic approval, model outcome or method increment has
been established by this review. The protected download and human review gates
remain open; existing frozen artifacts have not been changed.

Primary sources:

- [Official dataset entry and distribution request](https://ambrosia-benchmark.github.io/).
- [Pinned README and license statement](https://raw.githubusercontent.com/saparina/ambrosia/7de80a41f1ad4f01aed9bb55e734654688b572a8/README.md).
- [Pinned loader](https://raw.githubusercontent.com/saparina/ambrosia/7de80a41f1ad4f01aed9bb55e734654688b572a8/src/evaluation/evaluation_utils.py).
- [Pinned evaluation loop](https://raw.githubusercontent.com/saparina/ambrosia/7de80a41f1ad4f01aed9bb55e734654688b572a8/src/evaluation/eval.py).
- [Pinned family/prompt construction](https://raw.githubusercontent.com/saparina/ambrosia/7de80a41f1ad4f01aed9bb55e734654688b572a8/src/evaluation/format_prompts.py).
- [Pinned comparison code](https://raw.githubusercontent.com/saparina/ambrosia/7de80a41f1ad4f01aed9bb55e734654688b572a8/src/evaluation/metrics.py).

## Local tool environment wiring check (2026-10-12)

`runtime/cache_intent_tools.py` now enforces cache availability and declared
relative costs. Its trusted operator interface receives provenance-bound inputs
from `runtime/cache_intent_tool_inputs.py`; it is **not** a model runner,
semantic approval gate or new algorithm. Agent arguments cannot choose cache
rows, database paths or costs. Keep private logs inaccessible to any future
agent runner. Hash checks are not file locks, and SQLite defenses are not an OS
security sandbox.

Only `read_cache(cache_id)` and `run_sql(sql)` are implemented. Initial budget
is 100 relative units, cache read costs 1 or 8, SQL costs 10, and the attempt cap
is 20. Invalid/unavailable/unaffordable requests are free but consume an attempt.
Admitted SQL is charged before execution, including errors/timeouts. There are
no retries, resume or automatic cache refill. Correct recomputation is initially
affordable, not guaranteed after arbitrary spending. Declared units must never
be presented as measured time, API spend or monetary charges.

One local wiring check used the SAME already-exposed `h200a` historical SQL in
three sessions: unavailable/1, available/1, available/8. All six tool attempts
and three read-only SQL calls completed as designed, charging 10/11/18 units.
No additional target-reference query or target-answer scoring was performed;
the historical query may itself be equivalent to the q200 target. There were
zero model calls, human approvals and new independent research samples.

The related software suite passed 180 tests (50 newly added). Review fixed
partial completion logs being treated as resolved and finalization failure
leaving sessions callable. Closing seals the session before writing; failures
and partial files are retained. Consumers must verify full manifest coverage
and hashes and reject failure markers, not rely on a success-looking summary.

Receipt: `configs/run_records/cache_intent_tool_smoke_20261012_001.json`.
Raw logs, inputs and source snapshots remain local under git-ignored
`runtime/runs/cache-intent-tool-smoke-20261012-001`. Execution used the new
uncommitted source bytes on top of `c8045a2`, with individual source hashes
archived; that base commit alone is not the executed version.

The CLI is plan-only without `--execute`:

```bash
python -B -m runtime.cache_intent_tool_smoke --output runtime/runs/NEW_UNIQUE_PATH
```

Explicit execution refuses an existing output directory. Do not rerun this
already-completed check merely to increase counts. Human review, dataset
admission, model/resource authorization and a frozen inference protocol are
still required. This check supplies no evidence of cache-induced intent drift
or improvement over strong baselines; the protective contract preview remains
unchanged.

## CoSQL interface and measurement rules (2026-10-12)

The [official CoSQL page](https://yale-lily.github.io/cosql) and its associated
[UnifiedSKG loader](https://github.com/xlang-ai/UnifiedSKG/blob/main/tasks/cosql.py)
currently point to different Drive file IDs. Different IDs do not establish
different contents, and an upstream code revision does not pin an archive.
The official archive probe timed out; no archive bytes or data hashes were
obtained. No login, password submission, or alternate archive download occurred.

The inspected loader emits cumulative utterances, SQL, and a special final row,
but omits dialogue roles/acts. Do not use that flattened interface as proof of
human clarification. In the [original paper](https://aclanthology.org/D19-1204.pdf),
AFFIRM is a general user affirmation and CONFIRM_SQL is a system request for
confirmation, not a completed human approval. Raw role/clarification/SQL chains
must be examined before selecting tasks. Adding historical gold SQL/caches is
our adaptation, not the original SQL-state-tracking protocol. The inspected
loader URL is mutable main; neither its immutable revision nor data package
was acquired in this review.

`configs/cache_intent_measurement_v1.md` now separates ordinary execution,
instrumented contracts, contract-first and independent verification. It is a
development specification, not a preregistration or model-run authorization.
`runtime/cache_intent_judgment.py` mechanically derives indicators from supplied
adjudications; it cannot supply those judgments or grant human approval.

Key safeguards: wrong final output alone never implies C; a wrong contract with
an aligned later program remains C even if this database masks the error; missing
contracts cannot be inferred from SQL. A/B may overlap but exclude C at a linked
checkpoint. Unknown labels remain unknown. Entire missing pair records stay in
the supplied fixed denominator; the resulting bounds are not confidence intervals.

Independent review identified and resolved three design/implementation risks:

- One response containing contract and action cannot be split into invented
  event indices to manufacture a forward chain. It supports a pre-result
  contract endpoint, not forward C absent a later genuine program event.
- Planned, unexecuted reuse cannot be labeled actual cache application; the
  helper now rejects that contradictory label combination.
- The previous 100-unit/20-attempt software fixture permits price to change
  later affordability. It stays archived as a wiring check, not a clean causal
  protocol. The new specification requires a nonbinding horizon budget before
  inference (200 units suffices for these tools with charge <=10 and 20 attempts).
  This proposed configuration has not been used in a model run or new tool run.

```bash
python -B -m unittest tests.test_cache_intent_judgment -q
```

33 pure in-memory software tests passed. Labels are synthetic fixtures, not
research outcomes or completed human reviews. No new SQL/model calls or paid
compute occurred. Human task/history review and authorized, frozen inference
remain prerequisites; no method increment has been demonstrated.

## Bounded native-question pair screen (2026-10-12)

`configs/cache_intent_native_pair_screen_v1.json` records a static development
screen, not an executable configuration or an approved task set. The fixed
Arcwise full source remains 498 records; 185 belong to the four database IDs
already present locally. Source hash, all five recorded pair ID/database links,
and the four per-database text counts were checked. No new SQL/model calls,
human approvals, downloads or generic screening framework were added.

Only two additional current targets are shortlisted for review: Codebase
537->555 (post count to mean score for the same display-name filter) and Debit
1473->1480 (annual-derived mean to peak month for SME/2013). The individual
questions are public originals, but their pairing and temporal direction are
researcher constructed. They are development-exposed, not held-out samples.
Nullable scores and peak-month ties remain explicit review questions.

Formula_1 931->960 is held: saved old DDL declares fastestLapSpeed TEXT, while
the historical query sorts it and the target uses AVG. This is a static type
semantics risk, not an observed wrong answer. Original queries were not patched.
Alternatives involving the old q200/q234 targets retain nonselection reasons.

This bounded screen has not certified a nontrivial cache-only conversion between
distinct original questions. A dataset made only of insufficient-cache cases
would reward always recomputing and cannot evaluate retained reuse benefit.
Keep same-request repeat controls and separately disclosed, human-reviewed
lossless constructed histories; do not fabricate positive-reuse labels to
balance categories. Existing q200/q234 configs, raw evidence and blank review
forms are unchanged. No new batch is authorized by this screen.

## Nonbinding budget profile implementation (2026-10-12)

`runtime/cache_intent_tools.py` v2 adds an explicit trusted-operator profile
`nonbinding-200-v1`: 200 declared relative units, maximum 20 attempts, SQL
charge 10 and cache-read charge 1 or 8. The default `legacy-100-v1` preserves
the earlier 100-unit behavior. Existing archived v1 source, raw records, and
protective prompt previews are unchanged.

Before attempt k <=20, remaining budget is at least 200-10*(k-1) >=10.
Consequently every otherwise admitted action stays affordable in both price
arms within the shared horizon. This does not guarantee valid SQL, successful
execution, correct task interpretation, or a new attempt after the cap. These
units are not money or measured runtime. Availability remains separate from
price. Invalid requests still consume an attempt without a charge; admitted
SQL failures remain charged and recorded without silent retries.

Profile and initial budget now appear in public session state, the start
receipt and close summary; journal validation and charged-cost totals use the
selected initial budget. Unknown profile names and numeric budget overrides
are not accepted. A future increase in the configured maximum charge/horizon
that breaks the 200-unit bound is rejected when this profile is constructed.

Verification: 14 new budget-profile tests passed with mocked backends and
guards prohibiting SQLite connections/subprocess execution. Another 26
existing related tests passed; those construct temporary SQLite fixtures, but
the real isolated-worker integration test was deliberately not rerun. No
research database queries, model calls, human approvals or paid compute were
added. Test counts are software checks, not research sample counts.

The future runner must explicitly select the nonbinding profile in both price
arms and render matching 200-unit instructions. The old default CLI/100-unit
preview is not a ready scientific protocol. Human task/history review, exact
prompts and pair IDs, model/resource authorization and a frozen analysis plan
remain prerequisites. No cache-induced intent drift or method increment has
yet been established.

## Questionnaire material-scope check (2026-10-13)

General-reader usability feedback now exists, so “no returned materials” is
no longer an accurate description. It is not an independent semantic approval
of a current request/history pair. Private responses and platform metadata are
kept outside the public repository; this section records only material scope.

The ordinary B form uses legacy Mini-Dev IDs 1500, 604, 1481, 576, 1501 and 639.
It does not cover the development targets 200/234 or the shortlisted 555/1480,
and it does not ask reviewers to certify historical SQL/cache relationships.
Do not fill the two-target review forms using those responses or mark any
current candidate history approved on that basis.

A read-only text crosswalk against the two previously pinned local annotation
files found each of the six IDs exactly once, with matching database IDs. The
form retains all six legacy English questions and evidence fields. Compared
with Arcwise full at the revision above:

| Legacy ID | Question changed | Evidence changed | SQL changed |
|---|---|---|---|
|1500|yes|yes|yes|
|604|no|no|no|
|1481|no|no|yes|
|576|no|no|no|
|1501|yes|yes|yes|
|639|yes|yes|yes|

“Changed” means exact source-field inequality, not an adjudication of semantic
correctness. In particular, 1500/1501 change the requested month to August 2012,
and 639 changes the named user. Existing ordinary-reader feedback must not be
silently transferred to a different revision with the same numeric ID. Equal
text fields likewise do not certify SQL, database binding or cache applicability.

This was a static material/coverage check: no SQL execution, model sampling,
paid compute, new independent research tasks or semantic approvals. Keep the
current-request review and later historical-task/cache review separate. Continue
staged data adaptation with the existing two-target review materials; do not
expand the ordinary B survey or start inference as a substitute for that gate.

## Explicit unreviewed exploration request (2026-10-13)

The user subsequently selected a bounded exploratory model run using existing
material, rather than model-weight training or additional ordinary-reader survey
collection. This is a separate, explicitly unreviewed development diagnostic;
it does not retroactively grant human task/history approval or establish an
intent-drift endpoint. Existing frozen records and pending review fields remain
unchanged. Participant answers are not repurposed as training labels.

`runtime/cache_intent_contract_pilot.py` adds a real inference entry point for the
24 existing protective contract envelopes. It defaults to an offline plan and
requires explicit execution acknowledgement, native model-file verification,
a startup log and a new output directory. See
`configs/CACHE_INTENT_CONTRACT_PILOT_README.md` for its limits and prerequisites.
No model execution is claimed by this addition; remote authentication and
live-backend compatibility have not been established. In particular, this
contract-only diagnostic is not the later tool-enabled, nonbinding-budget
experiment and cannot resolve the substantive research hypothesis by itself.

## Completed real pilot and interface audit (2026-10-13, post-collection update)

The preceding preparation note has been superseded by the completed, separately
authorized `server-pilot-20261013-001` collection. The existing
Qwen/Qwen2.5-7B-Instruct backend produced all 24 scheduled responses: 24 passed
the eight-field structural parser, with no transport failures or unattempted
calls. Server-reported usage was 19,320 prompt and 2,237 completion tokens. The
client loop recorded 95.08 seconds; the supervisor recorded a 186.49-second
window including its checks, startup and cleanup. These are recorded timings,
not a platform bill or evidence of semantic correctness.

Only two development targets and six constructed histories were involved.
No weights were trained, no questionnaire answers were used as labels, and no
SQL, cache reads or later program-generation events occurred. Human semantic
approvals remain zero. This protective contract-only run does not establish the
main hypothesis, a type-C event, or a gap over strong checking plus recomputation.

The 159,058-byte result archive matched remotely and locally at SHA-256
`148311bece032c05d4d87b58cf918338f1a31dcca76041e7dc0efc566565c45a`.
Local reconstruction with the preserved source matched requests, assessments
and the structural summary. Raw artifacts remain private; the public receipt is
`configs/run_records/cache_intent_contract_pilot_20261013_001.json`.
The supervisor recorded no cleanup errors, and the recorded post-run GPU check
found no compute processes. The cloud instance was not shut down; this historical
check is not a claim about its current status or whether billing has stopped.

A post-outcome local audit matched all 24 logged prompts and guided schemas to
their saved requests. Each of the eight complete field definitions appeared in
all 24 decoding schemas but in none of the 24 logged text prompts. This is a
measurement-interface omission, not proof that it caused particular outputs or
that the cache hypothesis is true. Five of twelve pairs had string differences;
those are not five semantic-drift events. The aggregate record is
`configs/run_records/cache_intent_interface_audit_20261013_001.json`.

`runtime/cache_intent_prompts_v2.py` only appends the existing eight definitions
to the system message. It preserves the old prompt/data and is
**candidate-only, not deployed, and effectiveness untested**. The original runner
has not been switched to it. Any later interface comparison needs a separately
frozen design and current authorization; do not rerun, overwrite or silently
resume the completed batch. Task/history semantics remain pending, and the
tool-enabled workflow and strong-baseline experiment are still separate work.

## Offline next-step preparation and human-review materials (2026-10-14)

The source-level visibility repair is now paired with an offline 12-request
development plan, described in `configs/CONTRACT_INTERFACE_DIAGNOSTIC_V1.md`.
It fixes the cache context and compares newly sampled legacy/candidate arms,
with same paired seeds, all six histories retained and balanced arm order.
An independent static audit reconstructed its 61,286 bytes and all request/source
hashes. This is **prepared, not authorized, not executed**; the separate bounded
execution adapter is not connected. No old response will be reused as its control.

The first pilot's existing 24 raw responses were separately arranged into two
private blank review worksheets. Exact duplicates are grouped only when their
current task/evidence/schema also match: 11 reading objects, all 24 original
calls mapped once, still only two independent targets. Conditions and history
labels are hidden; verbatim outputs may reveal clues, so perfect blinding is not
claimed. Reviewers must first save independent current-task interpretations.
No semantic judgment or human approval was generated. Filled forms and the
organizer mapping remain outside the public repository.

Independent read-only checks matched both worksheets back to all raw responses.
The current 90-test offline fixture suite initially failed on sandbox temporary
directory permissions (47 reported errors); the approved local test rerun passed
all 90. Both attempts are recorded in
`configs/run_records/contract_output_review_preparation_20261014_001.json`.
These are software checks, not additional model samples. No model/SQL/server
operation occurred in this preparation. The stage-two decision remains **revise
measurement and obtain independent task review**, not scale paid experiments or
declare a method contribution. The strong-baseline gap is still unestablished.

## Interface collection and post-hoc review update (2026-10-21)

The previously prepared 12-request interface diagnostic was subsequently
authorized and completed on 2026-10-14. Its public aggregate receipt is
`configs/run_records/contract_interface_diagnostic_20261014_001.json`.
All 12 scheduled calls passed the structural parser; the archived responses,
requests and source were checked locally. This is not semantic approval by the
parser. There were two development targets, six histories and ten distinct
review objects. These are not twelve independent tasks.

Current-task interpretations were discussed and adjudicated after collection.
One completed output review was subsequently received; user clarification
defines its overall positive label as complete core business semantics while
allowing non-substantive placement/format differences. Under that submitted
post-hoc rubric, all six outputs in each interface arm receive the positive
overall label; none of the six paired overall labels changes. Field-level
issues remain separately recorded. A uniqueness operation expressed in an
aggregation field is not treated as absent from the whole contract merely
because the dedicated uniqueness field does not repeat it.

This is a descriptive, single-reviewer result with prior exposure to adjudication
materials, not a preregistered blinded confirmation or evidence of inter-rater
agreement. It does not establish field-by-field completeness, SQL correctness,
statistical equivalence, or an effect of cache convenience. No downstream SQL
was generated in this batch. The existing 24-call pilot is not silently assigned
these labels. Original responses, submitted reviews and subsequent clarifications
are preserved separately outside the public repository.

Decision: do not scale the interface comparison or claim that cleaner field
placement improved core business fidelity. Before a cost/availability study,
independently assess the constructed history relations, bind genuine subsequent
program events, freeze task-level scoring and resource limits, and obtain current
execution authorization. Two repeatedly inspected development targets cannot
become a held-out confirmation set. A gap over strong checking plus recomputation
remains unestablished. This documentation update ran no model, SQL or cloud job.
