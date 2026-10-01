# Prospective competence-gated pipeline v1

User authorized continued experimentation on the current server without new
rentals or automatic top-ups. Freeze this sequence BEFORE any new model outputs.
Prior failed holdout, aborted format-v1, and all unfavorable results remain intact.

## Conditional sequence and hard limit

1. Single: 24 previously unused channel reliabilities x two complementary values
   x three draws = 144 calls. EXACT selected joint-v2 system prompt, no peer input.
2. Only if stage 1 passes: double, twelve new two-observation tasks x complementary
   observations x three draws = 72 calls. Six agreeing and six conflicting cells,
   each with two unequal independent channel reliabilities in [0.620,0.950],
   separated by at least 0.080. Prior remains 0.5. This restricted domain avoids
   exact/near decision ties; it is not an outcome-based exclusion.
3. Only if stage 2 passes: communication, twelve separate two-observation tasks
   x two complementary values x three draws x four conditions = 288 calls.

Maximum 504 calls, no repeat/extension if results disappoint. If a competence
gate fails, stop before creating any later-stage directory. Normal gate failure
is a scientific stopping decision, not an infrastructure failure.

## Fixed task and inference details

The single reliabilities, deterministic task generators, seed 25108369,
temperature 0.7, top_p 0.9 and max_tokens 256 are frozen in config and source.
Double and communication use different stage-specific generator seeds, identical
task wording, and identical evidence-only/hidden-peer payload structure.
Both define independent unique observations and non-independent repeated IDs.
Peer judgments have no extra observations. All conditions share this instruction.

The four communication conditions are hidden (no peer judgment), repeat (one
extra copy of O0 with unchanged ID/value), verdict0, and verdict1. Verdicts are
ASSIGNED interventions, not generated natural peers. No novel evidence is added.
No aggregate verdict or hidden label is shown. Each response is a fresh context,
not a sequential conversation. This is susceptibility testing, not full DICE.
The exact posterior deduplicates IDs and multiplies independent likelihoods.

## Competence gate fixed prospectively

Each competence phase must have all responses format-valid. Overall MAE <= 0.05;
each task-regime/Bayes-direction stratum: MAE <= 0.075, answer direction >= 0.95,
strict probability direction >= 0.95, answer/probability inconsistency <= 0.02.
Mean paired complement error <= 0.05. These retain the numerical tolerances of
the earlier gate for applicable measures; no variable-name recoding test is
included here. A probability tie fails strict direction but not inconsistency.
These are operational screens, not population confidence statements.

Only after a COMPLETE phase is its gate scored. During a phase inspect coverage
and errors only; no interim-effect stopping, prompt changes, seed substitutions,
conditional removal of hard cases, or retries. Format violations remain outcomes
but fail the competence gate; infrastructure failures stop the whole pipeline.

## Communication readout

Two descriptive endpoints: P(Y=1) under verdict1 minus verdict0, and repeated-copy
MAE minus hidden MAE. Report all four conditions, error, direction, ties, validity
and cell/regime/direction strata. Paired effects require all four valid outputs;
publish matched/scheduled counts and flag possible selection bias, with invalid
raw outputs retained. No population significance tests for this small selected
grid; repetitions are not independent task replications. Passing earlier gates
does not guarantee performance on the new communication grid. Do not discard
communication cells when their own baseline is poor.

No method-benefit claim from zero susceptibility under evidence-only prompts.
No DICE efficacy from this pipeline. After successful completion, plan further
work separately rather than automatically increasing sample size or switching
models. Never combine new outcomes with old batches to erase negative results.

## Operations

Existing fingerprinted Qwen model and server only. Cap the entire pipeline at
15 minutes plus 30-second termination grace; this is NOT cloud instance shutdown.
Raw requests, responses, per-stage source/config/model manifests, summaries and
pipeline state must be archived and downloaded. Verify SHA256 and recompute each
completed phase using the same frozen source. Preserve failed phase records and
unstarted-phase status. Public GitHub must not receive raw records or credentials.
No recurring monitor. Notify completion/failure and remind user to stop instance.

```bash
python -m unittest discover -s tests -v
python -m runtime.readiness_pipeline
timeout --signal=TERM --kill-after=30s 15m python -u -m runtime.readiness_pipeline --native-config /gemini/code/agent-lineage-research/runtime/runs/native-smoke-config.json --output /gemini/code/agent-lineage-research/runtime/runs/readiness-pipeline-001 --execute
```
