# Output-format diagnostic v1

Frozen before inference, following comprehension-holdout-001. That holdout failed
its predefined gate due to probability ties. This is a new exploratory diagnostic,
not a replacement, repaired version, or confirmation of the previous holdout.

## Fixed design

216 calls: 12 new single-channel reliabilities x two observed values x three
output schemas x three repetitions. Reliabilities are 0.526, 0.548, 0.574, 0.602,
0.638, 0.674, 0.716, 0.758, 0.802, 0.844, 0.886, 0.934. These cover weak through
strong evidence and do not equal previous grid effective reliabilities.

A/joint: exact selected effective/explicit prompt, output answer, p_state_1, citations.
B/probability_only: same task and definitions, request only p_state_1.
C/answer_only: same task and definitions, request only answer.
Only the schema-request sentence changes; explanations of all fields remain in
all conditions, even when a field is not requested. Payload is identical across
arms. JSON-object mode, token cap 256, temperature 0.7, top_p 0.9 are shared.
Seed 93517062; match sampling seed across arm/value within cell/repetition.
Randomize order within each cell. No peers, history, hidden label or oracle field.
Posterior is a for observed 1 and 1-a for observed 0 under prior 0.5. This is an
easy one-observation task, not an evaluation of correlated multi-agent evidence.

## Predefined descriptive analysis

Two main contrasts: B-A posterior MAE and B-A exact-0.5 frequency, averaged over
matched cell/value/repetition outputs. Report all 12 cells and both value strata.
Secondary: C-A answer-direction correctness; A/B MSE, probability direction,
complement symmetry; A answer/probability inconsistency; usage/latency.
Do not impute probability for C or infer a reported answer for B. Exact 0.5 is a
tie, failing strict probability direction but not a strict contradiction.

No significance test or post-hoc pass threshold. Twelve deliberately selected
settings and repeats are not 216 independent worlds. This can implicate the
schema intervention, not uniquely identify rounding, reasoning, field-order,
token-length or citation effects. Any selected new schema needs a separate fresh
validation. Earlier gate failure and all unfavorable method findings stand.

## Integrity and resource limits

No silent retry, repair, output overwrite or exclusion. Schema/model/provenance
errors fail closed and retain pending/failed calls; incomplete runs receive only
coverage audits. Inspect coverage/errors during execution, effects after full
completion only. All requests reconstructed in scoring; local source, summary,
rows, contrasts and input hashes must match remote after backup.

User authorized using the existing server with remaining balance 20.59. One batch
only, no new machine, automatic expansion or recurring monitor. Ten-minute process
ceiling, maximum 216 calls and 55,296 generated tokens by configured cap. Expected
few minutes inference, not a guaranteed rental cost. Process timeout/SSH logout
does not stop instance billing. Back up and remind user to stop via provider UI.

```bash
python -m unittest discover -s tests -v
python -m runtime.format_probe
timeout --signal=TERM --kill-after=30s 10m python -u -m runtime.format_probe --native-config /gemini/code/agent-lineage-research/runtime/runs/native-smoke-config.json --output /gemini/code/agent-lineage-research/runtime/runs/format-diagnostic-001 --execute
python -m runtime.format_probe --output /gemini/code/agent-lineage-research/runtime/runs/format-diagnostic-001 --score
```
