# Staged cost diagnostic: development proposal, NOT an authorized run

2026-10-24. Continues the convenience-induced request-drift question; not a
replacement experiment about database optimization. No new algorithm selected.

## Current evidence and decision

Two received historical reviews cover all six histories; both endorse their
historical SQL. They differ in assumptions for the molecule scope in h234a/b.
Original forms and the conditional technical audit remain private. Current-task
semantics were resolved post hoc, not independently preregistered. The two tasks
and six constructed histories are exposed development cases, never a holdout.

Proceed with offline workflow implementation. Do not call models yet. Earlier
contract-only outputs do not establish a forward contract-to-program failure or
a causal cost effect. Classic filtering, projection, candidate supplementation
and recomputation remain strong baselines, not our method contribution.

## Proposed diagnostic (not frozen)

- Six history pairs, two cost arms each, one trajectory per arm: 12 trajectories
  from only TWO tasks in ONE database. No adaptive additions or repeats.
- Both arms have the same available complete history, original SQL, current
  request, evidence, schema, database and tools. Change read_cache cost 1 to 8
  only; SQL charge 10. Common budget 200; maximum 20 attempted tool actions.
  These are declared units, NOT yuan, latency, IO or tokens.
- Select nonbinding-200-v1 explicitly. Bind actual ToolSession.describe() to the
  initial prompt. Every otherwise admitted action remains affordable within
  the horizon. Invalid attempts still consume the horizon; SQL may still fail.
- Stage 1 emits a visible contract, with cost already exposed but no result.
  Stage 2 is a distinct generation that selects an action or answer. Subsequent
  generations can consume results. Never fabricate event ordering from fields
  within one response. Do not force the later program to obey a wrong contract.
- At most 22 model generations per trajectory (contract, up to 20 tool-producing
  generations, terminal generation), hence 264 calls as a ceiling, NOT predicted
  consumption. A terminal-only generation must not execute another tool. Exact
  action schema, output-token ceiling, timeout and model fingerprint are still
  unresolved; this preview is not an executable runner.
- Freeze paired seeds and arm order before calls. Identical seeds do not make
  multiple histories independent. Preserve failed/invalid responses without
  automatic repair or replacement; end that trajectory on invalid protocol.

## Scope and endpoints

For h234a/b do not infer molecule membership from identifier spelling alone.
Before freezing a run, specify a shared same-snapshot scope certificate or keep
scope verification as an available, charged operation. Certificate construction
and maintenance cost must be reported separately with an explicit amortization
policy. Do not expose the reference answer through that certificate. All arms
receive identical scope evidence. Existing snapshot agreement is not generic
DDL equivalence; a reviewer's opinion is not an automatically enforced invariant.

Primary development endpoint: pre-result contract contradiction against the
resolved request, assessed across the whole contract (not field location alone).
Secondary: genuinely later program consistency, history-directed C, incompatible
cache application, final correctness, abstention, invalid output, tokens, latency
and full tool/model cost. Do not call a wrong final answer C. Contract elicitation
is an intervention; this is not an ordinary uninstrumented agent experiment.

Report fixed-pair coverage and cost-8 minus cost-1 differences descriptively;
missing semantic judgments stay unknown. No population significance claims from
two targets. Blinded output review remains necessary; a syntactic test cannot
generate semantic ground truth. Keep errors in reference/adjudication separate.

## Strong-baseline sequence and stop criteria

This development batch, if later authorized, checks measurement feasibility, not
method superiority. Before claiming an effect, implement a separate ordinary
workflow (C unobserved), no-cache recomputation, contract-first, independent
verification and structured/classical reuse with on-demand base reads. Include
all extra calls and checks. Do not give one method a human gold contract.

Do not pool workflow changes into the clean price contrast. If differences are
only legal cost-aware plans, ambiguous requirements, faulty scoring or unequal
information, repair the study rather than label them drift. Do not repeat the
two tasks until a desired sign appears. New confirmation requires frozen unused
tasks and a justified sample size, not more seeds on these examples.

## Remaining execution gates

1. Frozen tool-loop runner with actual request/response IDs, raw response saving,
   hash-bound source and data, attempt limits, failure preservation and costs.
2. Scope-evidence policy applied identically; inspect actual generated prompts.
3. Fixed action schema, tokens, seeds, arm order, generation/time ceilings and
   preflight against the existing read-only protections.
4. Current server availability, balance and price; user-approved exact batch,
   estimated charge and maximum runtime. Old balances are not current authority.
5. Verified private backup path. No raw reviews/results in public GitHub.

Implemented so far: runtime/cache_intent_staged_preview.py and six synthetic
tests. They validate prompt/state binding and preserve raw contract text. They
do NOT execute tools, verify human independence, judge meaning, certify event
chronology, approve inference or establish scientific results.

### Later local implementation update (same date)

The staged loop and exclusive disk journal are now implemented separately in
`runtime/cache_intent_staged_loop.py` and `runtime/cache_intent_journal.py`.
After fixing a missing SQL-hash binding in a NEW synthetic fixture, the staged
suite passes 20 tests, including five real temporary-SQLite/disk integration
tests. The prior 14 budget-profile tests also pass with approved local temporary
directory access. Scripted responses are not model calls. Model transport,
frozen generation limits, external timeout, authorization and full run packaging
remain outstanding. The proposal is still NOT an approved/frozen model run.
