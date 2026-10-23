# Recovery feasibility: local findings and proposed protocol revision

2026-10-24. Development engineering evidence, not a model experiment, frozen
batch, method claim, or authorization to restart a server. Preserve v1.

## Findings from actual local execution

The offline script combines the existing trajectory engine, real ToolSession,
private journals, archived tokenizer, and scripted (not sampled) generations.
The scope-informed input version is used. Analyst-supplied candidate contracts
and target SQL are engineering fixtures, NOT privileged input for a method.
Task 234's audit SQL explicitly joins bond membership to the TR009 restriction.

There are 36 scripted paths: six histories, two prices, and three fixed routes.
These are two already exposed tasks, not 36 independent research observations.

| Route | Paths | SQL calls actually executed | Engineering outcome |
|---|---:|---:|---|
| Immediate recomputation | 12 | 12 | Complete result and scripted answer; largest prompt 1316 tokens |
| Nineteen cache reads, then recomputation | 12 | 12 | Complete SQL results; four terminal requests exceed 4096 with a hypothetical 512-token output reservation |
| Twenty cache reads, then attempt recomputation | 12 | 0 | All SQL attempts are prohibited by the terminal-only instruction and loop horizon; budget remains 180 or 40 |

The largest prompt is 3741 tokens (h234b, price 1, nineteen reads then SQL).
3741 + 512 = 4253. These lengths use complete real tool observations but
scripted contract/action text, not model behavior or worst-case lengths.
The script measures overflows without enforcing a serving limit; its ANSWERED
status is therefore not evidence that the 4096-token model could finish.
All 24 executed SQL results have the same bag fingerprint within each target.
That checks reproducibility across routes, not general semantic equivalence.
The database hash is unchanged. No model, SSH, or paid service was used.

After twenty cache reads, some directly compatible caches already suffice for
an answer. The evidence is that recomputation is unavailable, NOT that every
correct solution is unavailable in every history. In particular, an insufficient
cache may still require base data. Budget nonbindingness alone is insufficient.

## Consequence for the scientific design

The old configuration cannot support the objective's strong feasibility premise.
Do not call a late resource failure an intent shift or exclude failed pairs.
Simply raising the window enough to fit this one observed scripted path does
not solve arbitrary SQL-output growth or the exhausted tool horizon.

Proposed next development version, before any model run:

1. Keep at most twenty exploration actions, then offer one explicitly reserved
   recovery stage: answer, abstain, or one agent-written read-only SQL query.
   If SQL is selected, allow a subsequent answer/abstention generation. No
   reference SQL or correct contract is supplied to the model. This adds an
   environmental option uniformly, not an algorithmic contribution.
2. Reserve ten declared units for recovery, outside a common 200-unit exploration
   allowance. Price 1/8 changes neither recovery access nor its reserved balance.
   Every attempted SQL, including failure, is logged and charged. The recovery
   allowance is explicit in both arms from the beginning.
3. Provisionally assess a 32768-token window, which is recorded in the archived
   model config, NOT confirmed on a live server. Keep the candidate 512-token
   generation cap and examine whether valid contracts, SQL, and answers fit it.
   Confirm hardware/serving/template behavior separately before authorization.
4. Assess a common 512-token bound for each complete model-visible tool
   observation. If a result exceeds that bound, store it in full privately and
   return an explicit incomplete/oversize status, with no selected partial rows.
   This changes the observation protocol and must be versioned, disclosed, and
   applied to every method. Do not silently retrofit historical measurements.
   Verify complete correct-route outputs fit this bound on every admitted task.
5. Check full serialized prompts, with completion reservation, at EVERY model
   request. Derive and test the maximum permitted history envelope, including
   JSON escaping, role framing, errors, all 21 possible tool results, and all
   prior generations. An indicative sum of content caps is not a tokenizer
   proof: 22 previous generations * 512 + 21 observations * 512 = 22016 tokens
   BEFORE initial context, protocol messages, framing and final reservation.
6. Reject a configuration/task before inference if this envelope or its known
   correct route does not fit. Configuration revisions must use engineering
   criteria and new versions, never intermediate model outcomes. Within an
   admitted run, retain unexpected overflow as failure; no hidden truncation.

This is a design proposal, not implemented recovery support. The existing v1
loop remains unchanged, including its terminal-only behavior. The previous
full-result policy remains the historical v1 policy; the proposed bounded-result
interface must not be presented as already agreed/frozen or naturally occurring.

## Next useful work and limits

Implement and test the proposed common recovery reserve and explicit result
admission on local fixtures, then verify actual correct-route result sizes and
the context envelope. Only after this gate should a fixed paid development batch
be proposed. No need to regenerate twenty more variants of the same toy paths.
Still missing: a controlled model effect, strong-baseline residual gap, and
independent tasks. Engineering success cannot substitute for any of those.
