# Cache convenience and request fidelity: dataset adaptation

Status (2026-10-12): **development compatibility evidence only**. No model run
has tested whether cache availability/cost changes the interpretation of an
explicit request. Historical experiments do not establish this new hypothesis.

## Current question

Holding the current request, trusted historical task definitions and available
information fixed, does making a historical result materialized or cheaper to
read alter the agent's expressed task definition? Incorrect cache selection,
SQL implementation errors, ambiguous requests and wrong reference labels are
separate explanations. A changed final answer alone is not evidence of changed
request interpretation.

Public annotations reduce original task-writing work; they do not automatically
validate researcher-constructed cache histories. No project human review has
been completed. Model-generated judgments must not be counted as such reviews.

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
