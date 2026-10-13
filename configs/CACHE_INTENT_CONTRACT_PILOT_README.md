# Unreviewed contract-only pilot: execution boundary

## Current status (2026-10-13, after collection and local audit)

`server-pilot-20261013-001` completed **24/24 real model calls**, all structurally
valid, with no transport failures or unattempted calls. It was exploratory
inference, not weight training or SQL execution. The result archive was downloaded,
its remote/local SHA-256 matched, and preserved-source reconstruction matched the
requests, assessments and structural summary. Two development targets and six
constructed histories are not 24 independent research samples.

The completed run supersedes the preparation-time remote-access blocker below;
it does not imply that a model service is currently running. Human task/history
review remains pending, and neither the main cache-intent hypothesis nor method
efficacy has been established. Raw records stay private. Public receipts:

- `configs/run_records/cache_intent_contract_pilot_20261013_001.json`.
- `configs/run_records/cache_intent_interface_audit_20261013_001.json`.

The subsequent offline audit found each of the eight complete field definitions
in all 24 guided decoding schemas but absent from all 24 logged text prompts.
The definition-visible v2 is an **undeployed candidate**, not a demonstrated fix.
The original batch, prompts and runner remain unchanged. This document does not
authorize another batch or a rerun of the completed one.

## Historical preparation note (before the completed 2026-10-13 run)

The following paragraph records the earlier checkpoint, not current run status:

2026-10-13. The user selected exploratory inference, **not weight training**.
This entry point prepares a bounded first real-model diagnostic from the existing
development material. No model call has been made with it yet. Remote SSH
authentication remains unresolved; the presence of this document is not a launch
authorization, a verified live server, or a completed experiment.

## Scope and frozen inputs

- Two already-exposed targets (200, 234), six constructed histories, two paired
  contrasts per history, and two arms per contrast: 24 scheduled calls. There
  are only **two independent targets**, not 24 research samples.
- Reuse the original 24 protective, pre-result contract envelopes byte-faithfully.
  The source and preview SHA-256 values are fixed in the runner. Only model and
  decoding fields are added. The complete paired envelopes are revalidated.
- Availability false/true and declared cache price 1/8 are separate contrasts.
  Within each history, availability-high and price-low have identical envelopes;
  their separate technical calls are not independent controls or replications.
- Temperature 0, top-p 1, at most 768 completion tokens, one call per scheduled
  envelope, deterministic hash ordering and fixed paired seeds. Backend seed
  acceptance does not itself guarantee hardware-level determinism.
- No tools, SQL execution, training labels, participant responses, gold answers,
  candidate contracts or cache-applicability labels enter model requests.

The protective instructions forbid planning and tool use. The declared budget
100, SQL cost 10 and cache cost 1/8 remain **prompt text**, not actual execution
costs. Do not relabel this experiment as ordinary agent execution, the later
200-unit tool-enabled protocol, a test of real resource pressure, or a method
comparison. No model-weight update is performed.

## What can be reported

The runner reports planned/attempted/unattempted calls, HTTP/model-identity
failures, strict eight-field format validity, available server-reported tokens
and client time. Invalid/truncated outputs remain in the scheduled denominator.
They are neither repaired nor silently replaced. Unknown usage is not imputed.
The raw bytes remain available for later authorized, separately documented review.

It does **not** compute task accuracy, semantic correctness, an intent-drift
rate, type-C labels, significance, or causal effect sizes. Different strings are
not automatically different meanings. A/B/C require additional observations and
independent adjudication as specified in `cache_intent_measurement_v1.md`.
The existing task/history approval fields remain pending; this run cannot
approve itself or move the project past the substantive data-admission gate.

## Historical offline preparation (no model, network or database execution)

Run from the repository root:

```bash
python -B -m runtime.cache_intent_contract_pilot
python -B -m unittest tests.test_cache_intent_contract_pilot -v
```

The first command checks the frozen local package and prints a plan hash.
Tests use invented responses and mocked transports: they are software checks,
not additional human reviews, model outcomes or research samples.

Verified locally on 2026-10-13 with Python 3.13.9: 33 runner tests plus the
12 existing prompt tests passed together (45 total). Network, subprocess and
SQLite guards prevent unintended real work in the fixture suite; intended
transport/native execution paths are mocked. The private frozen-package hash
check ran read-only, not skipped. The plan-only command produced SHA-256
`73fc13d1256a76281e42c3f0aca15a92ff875322e59b1bcc9ff03348cdeea5e3`.
At that preparation checkpoint, actual remote JSON-schema compatibility remained
untested. The later completed pilot established structural compatibility for its
24 requests, not semantic accuracy or field-definition visibility in text.

## Real execution prerequisites

These are retained execution safeguards, not instructions to rerun the completed
batch. They must be rechecked for any separately authorized future experiment;
the historical access issue has already been resolved for the recorded first run.

1. Resolve SSH authentication and inspect the actual existing instance and model
   service. Do not retry a rejected credential, accept an unknown host key, or
   assume an old process/instance is still active.
2. Confirm the current instance price/budget and the scope of the authorized
   batch. Do not silently install/download a model, rent a machine or use a paid
   API as a substitute for the intended existing backend.
3. On that same execution host, supply a native model manifest containing the
   exact local model path, file hashes, package versions and served model name.
   Retain its actual startup log. The runner rehashes the weights and checks
   package versions and the served `/v1/models` ID/root before inference.
4. Use a new output directory. Copy/verify the fixed package and current source
   first if they are absent remotely. Never overwrite a prior run or resubmit a
   failed/uncertain call under the same experiment identity.

After these prerequisites, the explicit execution interface is:

```bash
python -B -m runtime.cache_intent_contract_pilot \
  --native-config /absolute/path/to/verified-native-config.json \
  --startup-log /absolute/path/to/actual-server-startup.log \
  --output runtime/runs/NEW_UNIQUE_CONTRACT_PILOT \
  --execute --ack-unreviewed-contract-only
```

These placeholder paths are not existing/verified server artifacts. Execution
must occur next to the pinned local weights, not through an unverified remote
proxy. Only explicit `http://127.0.0.1:PORT/v1` endpoints are accepted. Proxy
environment settings and redirects are disabled; no API credentials are read.
This client performs no automatic install, network model download, SQL execution,
SSH reconnection, or resumption.

## Failure, time and provenance handling

Requests are written before dispatch. Each HTTP worker runs from the source
snapshot archived inside the output and flushes incoming response bytes to disk.
Each request has at most 180 seconds; the inference client loop has at most
1,800 seconds in total. **That loop limit excludes native-weight hashing and
preflight**, and client termination does not prove server-side generation was
cancelled. It never stops cloud instance billing. Check the server after a
timeout; do not immediately send the same request again.

Transport errors, model-ID mismatches, unsupported JSON-schema requests and
malformed API responses stop the remaining schedule without retry or fallback.
A completed API response containing an invalid/truncated contract is retained,
marked format-invalid and followed by the next fixed call. No early stopping
depends on semantic outcomes, and there is no sample replacement.

The output includes native manifest, startup log, source snapshot, frozen plan,
requests, raw response bytes, transport receipts, per-call structural assessments,
coverage summary, and artifact hashes. It stays under ignored `runtime/runs/`.
Disk hashes plus server metadata are provenance evidence, not attestation of the
exact bytes loaded by a running process. Review/backup logs before any public
release. Do not upload raw participant data or authentication material.

After a successful collection, verify archive hashes and reconstruct structural
counts from the raw responses before discussing research results. A subsequent
tool-enabled experiment and strong-baseline comparison remain separate work;
this entry point does not complete the long-term research goal.

## Completed collection and limits (2026-10-13 update)

The recorded model was Qwen/Qwen2.5-7B-Instruct. Server-reported usage was 19,320
prompt tokens and 2,237 completion tokens; the client loop recorded 95.08 seconds,
and the supervisor recorded 186.49 seconds including checks, startup and cleanup.
These are recorded timings, not an independently measured bill. The downloaded
archive was 159,058 bytes, SHA-256
`148311bece032c05d4d87b58cf918338f1a31dcca76041e7dc0efc566565c45a`.
Raw requests/responses, frozen source, manifests and logs were preserved and
locally verified; they were not uploaded to the public repository.

The supervisor reported no cleanup errors; a recorded follow-up GPU query found
no compute processes. No cloud-instance shutdown was performed. That post-run
observation is not a live-status check or confirmation that billing stopped.

The post-outcome interface audit found 18 unique envelopes and 11 exact parsed
contract objects across 24 calls. Seven of twelve output pairs were exactly equal
and five had string differences. Those differences mix possible wording/field
placement changes and are **not semantic labels or an intent-drift rate**. No
later program, actual cache application or final answer was observed; downstream
indicators remain unobserved rather than observed zero.

The separate `runtime/cache_intent_prompts_v2.py` candidate exposes the existing
field definitions by appending them to the system message only. It has not been
deployed or substituted into this frozen runner. No claim is made that the
omission explains every field error or that v2 fixes them. Keep the pending
independent task/history review and any future interface comparison separate
from this completed exploration. Do not overwrite/retry the old batch or start
a new one without its own frozen scope and current authorization.
