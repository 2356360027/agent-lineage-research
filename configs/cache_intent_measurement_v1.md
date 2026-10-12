# Measurement specification: cache convenience and request fidelity

2026-10-12. Development specification, **not a preregistered or authorized model
run**. No model outcome has tested this hypothesis. Human review is pending.
The two exposed targets/six constructed histories remain development material.

## 1. What must be observed

Keep three separate artifacts: the current request, an explicitly emitted task
contract (if the workflow elicits one), and the program/action that follows that
contract. Record the final answer separately. Do not reconstruct a missing
contract by translating the model's SQL: that would use the program as both
the evidence of interpretation and the evidence that interpretation was followed.

The eight contract dimensions remain entity, time, filter, aggregation, dedup,
denominator, unit and output. Wording changes are not semantic changes. A
reviewer must identify a discriminating field, its allowed equivalent forms,
and the request/schema/evidence that establishes the intended requirement.

At a single linked checkpoint:

| Indicator | Required observations | Insufficient evidence |
|---|---|---|
| A | Faithful contract AND incompatible cache actually applied | Merely opening a cache, or a history marked insufficient on its own |
| B | Faithful contract AND subsequent program contradicts the request | Different SQL text or one execution mismatch without semantic inspection |
| C | Contract contradicts request AND later program implements that contract | Wrong final answer, SQL copied from history, or a retrospective explanation |
| History-directed C | C AND the changed field follows a conflicting historical requirement | Generic contract error or resemblance on a field shared by both tasks |
| D | Current request remains genuinely ambiguous | A reviewer having not yet completed the review |
| E | Reference/scorer is defective | Model/reference execution outputs merely differ |

A and B can overlap. C is incompatible with A/B at the same checkpoint, since
their faithful/wrong-contract requirements conflict. A separate orthogonal
`incompatible_cache_use` flag can accompany C. Do not force every trace into
exactly one of A/B/C; mixed stages and unresolved cases must remain visible.
An incorrect contract/program can accidentally match the reference result on
one database. Such output agreement does not erase C.

## 2. Timing and trace linkage

Record immutable contract/program artifact IDs, event indices, the contract ID
explicitly linked to the program, and the observation stage:

- `pre_result`: before any result-returning action in that trajectory.
- `post_result`: after an earlier tool result, but before the *new* program
  being judged. This can establish post-exposure C, not pure pre-result drift.
- `not_elicited`: ordinary workflow did not request a contract; structurally
  unobserved, not an observed faithful contract.
- `missing`: a requested artifact was lost/invalid/missing; not the same as
  deliberately not measuring it.

A contract produced at or after the judged program is not admissible for the
forward C chain. A final narrative cannot be joined to an earlier SQL merely
because both concern the same question. A runner must check source events and
hashes; the mechanical rule helper only validates supplied IDs and ordering.
Event indices refer to actual generation/tool turns, not field positions in one
response. Splitting a joint contract/action output into separately numbered
fields cannot establish forward C. Preserve that joint action as a raw artifact;
a contract-only endpoint record leaves the **later** program unobserved unless
another genuine program-generation event follows. It must not label C false.

Execution complete/error/timeout/abstain/missing/not_executed stays separate and
refers to the associated action/program, not an unspecified overall trajectory.
Semantic alignment of a visible program can sometimes be reviewed even if it
timed out; that is not evidence of successful execution or task completion.
Likewise a genuinely later unexecuted program may support semantic C, but cannot
be labeled actual cache application. A final answer that applies a prior result
is an observed answer action, not an unexecuted SQL plan. Opening a cache alone
is not applying it to answer the current task.

## 3. Mechanical helper and reviewer work

`runtime/cache_intent_judgment.py` derives indicators from **already supplied
judgments**. It does not understand natural language, judge SQL equivalence,
authenticate a reviewer, establish data eligibility or approve model execution.
Every real input must be linked to separately preserved blind reviewer forms,
adjudication rationale and raw artifacts. Labels in its tests are invented
software fixtures, never human reviews or empirical samples.

Review current task semantics without cache conditions or model outcomes first.
Then review history correctness and reuse possibilities: direct, cache-only
transformation, augmented reuse with base reads, or unresolved. A missing field
in a cache is not a prohibition against supplementing it from the base tables.
Only subsequently judge blinded model contracts/programs, using the approved
semantics rather than the desired experimental conclusion. Preserve original
reviews and disagreements; two matching forms are not proof of correctness.

The helper accepts tri-valued indicators. Conjunction is false if a required
condition is definitely false, true only if all are true, and unknown otherwise.
Unresolved task/reference labels leave the main indicators unknown. D/E are
also tri-valued. Inconsistent semantic match judgments require adjudication;
the scorer must not choose the label favorable to our hypothesis.

## 4. Contrasts and workflow separation

First development contrast proposed: **read cost 1 versus 8 with cache available
in both arms**. Unlike availability, this leaves the initial action set
identical. It does not automatically preserve later affordability. Hold request,
history SQL, schema, database, correctness priority,
other costs, initial budget and tool definitions fixed. Any preference for low
cost is given equally to both arms, never added after looking at outcomes.

The earlier software fixture (100 units, 20 attempts) is not an admissible pure
convenience protocol: twelve reads leave 88 versus 4 units, so a 10-unit SQL
becomes unaffordable only in the expensive arm. Keep that frozen fixture; do
not relabel its wiring result as a scientific contrast. Before inference, freeze
a nonbinding budget for the bounded action horizon. With these exact two tools,
maximum charge 10 and at most 20 attempts, an initial 200 units suffices: before
attempt k (1..20), remaining budget is at least 200-10*(k-1), hence at least 10.
Both arms can afford every admitted action; invalid attempts cost zero but still
consume the common horizon. The current correct solution is one base-table SQL.
This does not guarantee success or permission for actions after the common cap.
If tools, charges, horizon, or required solution lengths change, recheck this
bound. Log total charged cost and use the same predeclared soft efficiency goal;
do not describe the effect as forced scarcity. The new configuration and its
boundary tests are still required before any model run; none ran here.

Preserve the existing preview direction: first=1, second=8, delta=8 minus 1.
The conjecture that cheap reuse induces drift predicts a negative delta for a
drift indicator; do not flip signs after observing outputs. Availability remains
a separate secondary contrast (false then true), not pooled with price effects.

Workflows are different measurement/mitigation settings, not interchangeable
arms of a clean causal contrast:

1. Ordinary joint execution: no forced contract. Measure final request fidelity,
   actions and costs. C is structurally unobserved, not zero.
2. Instrumented joint execution: emit a short contract with intended action in
   the same response; save both before executing the action. This permits a
   pre-result contract endpoint, but elicitation itself is an intervention.
   The joint action is not a later generation event and cannot establish forward
   C. A genuine subsequent program event is needed for that separate endpoint.
3. Contract-first: establish contract from current request/context before
   exposing cache convenience, then plan. Its pre-exposure contract cannot
   respond to an as-yet-hidden treatment by construction; assess final fidelity
   and all extra generation/verification costs, not claim a discovery from
   this built-in invariance.
4. Independent verifier: inspect original request and candidate program in a
   fresh context, without privileged correct human contract. Include verifier
   calls, repairs and re-execution in total cost.

The present protective `cache_intent_prompts.py` is left frozen. It forbids
planning/tools and is not silently relabeled as ordinary joint execution.
Strong structured intent matching, classical reuse plus on-demand base reads,
and metamorphic/counterexample verification remain required later baselines.
No novel algorithm is selected until these leave a measurable gap.

Pre-result contract contradiction, forward C, and final fidelity are separate
endpoints. C is a compound event; it does not prove causal mediation through
the contract. After tools run, actions may change which rows become visible.
A total effect on final performance cannot isolate convenience from this
information-exposure pathway. A future equal-exposure replay would be a distinct
intervention with its own assumptions, not a silent reanalysis of these runs.

## 5. Missing records and dependence

Freeze pair IDs before generating any outputs. The helper `paired_bounds`
requires that manifest, rejects unexpected/duplicate records, and retains
entirely missing pairs as two unknown arms. It cannot verify that the supplied
manifest really is the previously frozen one; that binding belongs to the runner.

For a binary indicator, known false/true has interval [0,0]/[1,1], while unknown
has [0,1]. For second minus first, the individual bound is
`[L_second - U_first, U_second - L_first]`. Average over the fixed manifest.
These are missing-label accounting bounds, **not confidence intervals**, causal
estimates under missingness assumptions, p-values or independent-sample counts.
Any complete-case mean is labeled descriptive and shown alongside coverage.

Do not treat a newly discovered ambiguous/defective task as an ordinary failed
model label with a well-defined endpoint. Preserve it in the original accounting,
document when the defect was found and resolve the eligibility protocol before
claiming an estimand on a changed subset. Never silently replace it or drop only
one arm. Structurally unobserved and accidentally missing contracts are reported
separately; no-contract ordinary runs cannot yield a C comparison from this helper.

Histories, repeated generations, dialogue turns and reversed pairs may share
one target or database. Pair-level accounting is not statistical independence.
The current two exposed targets cannot support a general population claim.
Any later confirmation must freeze task-family/database splits, sample size
justification and clustered uncertainty before inspecting held-out effects.

## 6. Next admissible small step and stop rules

After two-person task/history review and current resource authorization, use
the existing tiny package only to debug workflow coverage and adjudication.
Do not treat it as powered confirmation. Freeze exact model, prompts, pair IDs,
generation/tool ceilings, analysis and failure handling before execution.
The tool budget in existing software is a declared relative-unit budget;
LLM tokens, measured latency, failures, and actual provider/server cost must
also be logged. No fresh paid run is authorized by this document.

Revise/stop if observed differences reduce to ambiguity, bad references, changed
information, unavailable correct solutions or legitimate cheaper planning.
If ordinary strong contract-first/verification procedures remove the issue
without a worthwhile remaining cost-quality gap, do not manufacture a novel
method name. A negative/unknown development result is not permission to add
traps, change prices or keep sampling until a positive result appears.
