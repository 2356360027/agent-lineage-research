# Single-observation competence holdout v1

Protocol fixed before new inference on 2026-10-01. This follows the completed
comprehension diagnostic and evaluates its selected effective/explicit prompt
on fresh reliability parameters. Prior findings and unfavorable method results
remain unchanged. This is a finite-grid operational screen, not a new efficacy
claim, population validation, or external preregistration.

## Frozen 144-call design

Source reliability r in {0.61, 0.74, 0.86, 0.93}; observation reliability t in
{0.68, 0.82, 0.97}. All twelve pairs are new relative to the previous diagnostic.
Cross twelve pairs with two observation values, two encodings, and three repeated
draws. New seed 81327046, temperature 0.7, top_p 0.9, maximum 256 output tokens.
The r/t parameters are used only to derive a single-channel likelihood
a = r*t + (1-r)*(1-t); the model receives a, not the two-layer problem.

Y encoding uses the exact selected effective/explicit system prompt. Z encoding
uses Z=1-Y with the observed measurement also complemented; the system prompt
only replaces the state name Y with Z. The p_state_1 field always denotes the
probability of state 1 in the displayed encoding. This is a bundled variable-name
and label-complement check, not a general language/paraphrase robustness test.
No explanations of transformations, oracle posterior fields, or hidden labels
are sent. Prior is 0.5, so posterior equals a or 1-a: this is intentionally easy.

Order is shuffled within each parameter cell. Sampling seeds match within each
cell/repetition across values and encodings. This does not guarantee GPU
determinism. No peers, previous assessment, adaptive wording, or prompt selection.
Raw responses remain unchanged. The predetermined analytical mapping 1-p_Z is
used only for comparison with p_Y, never to repair an incorrect model output.

## Predeclared operational gate

All conditions must pass, after full coverage and request/source audits:

- Overall posterior MAE <= 0.05.
- Each of four displayed-value/encoding strata: MAE <= 0.075, answer-direction
  correctness >= 0.95, probability-direction correctness >= 0.95, inconsistency
  rate <= 0.02. Each stratum has 36 outputs; the last rule requires zero
  inconsistencies, and each direction rule permits at most one wrong output.
- Each encoding's mean complement error abs(p_v0 + p_v1 - 1) <= 0.05.
- Mean cross-encoding discrepancy abs(p_Y - (1-p_Z)) <= 0.05.

These are engineering tolerances chosen before this batch, not statistical
significance thresholds. Ties p=0.5 fail strict probability-direction correctness
but are not counted as answer/probability contradictions. Report ties separately.
Publish all strata, MSE/MAE, per-cell records, symmetry, usage, and every gate
component regardless of success. Do not treat 144 outputs as 144 independent
worlds; no population confidence intervals or significance tests are planned.

Failures, malformed responses, missing calls, or request/provenance mismatches
block gate evaluation. Preserve them; do not silently retry, change seeds, omit
cases, or replace this batch. During inference inspect coverage/errors only.

## Decision after the batch

Passing permits designing a separate controlled communication study; it does not
launch it automatically or establish competence on correlated multi-observation
tasks. Never flatten correlated evidence into independent effective channels.
Any computation support in future method comparisons must be shared by all arms.
Failure means the current screen is not passed, not that the research hypothesis
is disproved. Further task changes require a new exploratory protocol and new
validation; do not relabel this batch as successful after changing thresholds.

## Operation and integrity

Use the existing fingerprinted native model and server only after confirming
availability/budget. No new rental, background monitor, or automatic next batch.
Maximum 144 calls without retries and 36,864 generated tokens by configured cap.
Estimate 2-5 minutes inference, subject to runtime variation; allow a 10-minute
process ceiling including model-file hashing. The ceiling does not stop cloud
billing. Preserve code/config/server metadata and launch/scoring logs. Download
all raw data, verify archive SHA256, and rescore with the identical frozen source.
Public repository contains code/tests/protocol only, not raw responses.

```bash
python -m unittest discover -s tests -v
python -m runtime.comprehension_holdout
timeout 10m python -u -m runtime.comprehension_holdout --native-config /gemini/code/agent-lineage-research/runtime/runs/native-smoke-config.json --output /gemini/code/agent-lineage-research/runtime/runs/comprehension-holdout-001 --execute
python -m runtime.comprehension_holdout_score --run /gemini/code/agent-lineage-research/runtime/runs/comprehension-holdout-001
```
