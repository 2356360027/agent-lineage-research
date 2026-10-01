# Output-format diagnostic v2

Revised with user approval after v1 stopped on its first response because the
answer-only arm produced an extra probability field. Preserve that aborted run;
do not merge it into this run, overwrite it, or call this independent confirmation.

## Fixed prospective design

216 new calls: the same twelve reliabilities from v1, two observed values, three
output formats and three repetitions. New fixed seed 14602837. Same native model,
temperature 0.7, top_p 0.9, output cap 256. This is an exploratory revised protocol
using the same parameter grid, NOT an unseen holdout. No adaptive sample changes.

A: answer, p_state_1, citations. B: p_state_1 only. C: answer only. Identical task
and observation payload; each arm defines ONLY its requested output fields.
All explicitly prohibit extra fields or text. Thus this changes both requested
schema and arm-relevant explanation, not only field count. No unique cognitive
mechanism can be identified. Requests are shuffled within each cell; sampling
seeds match across arm/value within repetition. No previous output is provided.

## Response handling fixed before launch

A valid API response whose generated content violates the requested format is a
recorded outcome, not a reason to retry or terminate. This includes invalid JSON,
extra/missing/duplicate fields, invalid values, invalid citations, and truncation.
Store raw text, response, usage, violation and format_violation status; assessment
is null. Do not salvage fields, repair probabilities, or synthesize responses.
Continue to the next scheduled call without retries.

Network failures, HTTP errors, malformed API envelopes, wrong served model,
provenance mismatches, and runtime/infrastructure exceptions STOP the batch and
preserve failed/pending records. The whole scheduled response set is required
for comparison. Progress checks inspect coverage/errors only until completion.

## Readout including invalid outputs

Always report all 72 scheduled calls per arm and 36 per value stratum, with format
validity as a first-class result. Primary descriptive comparisons: validity rates,
B-A MAE and tie frequency on matched valid pairs with their denominator, and
all-call B-A MAE bounds. Conditional valid-pair estimates may have selection bias.

For unscorable responses, MAE is bounded in [0, max(q,1-q)] if the missing
probability were in [0,1]; ties are bounded in [0,1]. Aggregate bounds are
partial-identification sensitivity limits, NOT replacement observations or CIs.
Report conditional MSE, probability/answer direction, ties and complementary-label
symmetry with valid and scheduled counts. Invalid cases count as not producing a
valid-correct answer in the explicitly named all-call success rate, never as an
imputed binary answer. Do not invent probability for C or explicit answer for B.

Twelve selected settings and repeated draws are not 216 independent worlds. No
significance or pass/fail threshold is added. Previous holdout remains failed.
No DICE efficacy or communication effect is tested. Any selected improvement
requires a separately designed fresh validation.

## Budget, audit and stop

Only this 216-call batch on the existing server; no automatic next batch, retries,
new rental or recurring monitor. User last reported balance 20.59. Ten-minute
process ceiling including hashing, with TERM then KILL after 30 seconds. Neither
process exit nor SSH logout stops cloud rental billing. Back up raw responses,
config, source snapshot and launch/scoring logs; verify archive SHA256 and exact
local/remote summary, rows, contrasts and input hashes. Remind user to stop the
instance via provider interface after backup. No raw records in public GitHub.

```bash
python -m unittest discover -s tests -v
python -m runtime.format_probe_v2
timeout --signal=TERM --kill-after=30s 10m python -u -m runtime.format_probe_v2 --native-config /gemini/code/agent-lineage-research/runtime/runs/native-smoke-config.json --output /gemini/code/agent-lineage-research/runtime/runs/format-diagnostic-v2-001 --execute
python -m runtime.format_probe_v2 --output /gemini/code/agent-lineage-research/runtime/runs/format-diagnostic-v2-001 --score
```
