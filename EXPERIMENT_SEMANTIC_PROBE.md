# Probability-semantics diagnostic v1 (exploratory)

Protocol fixed before this batch, 2026-10-01. Motivation: the preceding frozen
validation showed controlled verdict sensitivity, but answer/probability
inconsistency was more common with visible peer verdicts. Those observations
motivate a diagnostic, not a retrospective repair of validation results.

## Design

30 fresh synthetic worlds (seed 19264073), three repetitions per world.
Two final-instruction styles crossed with three peer exposures:

| Instructions | Hidden peer answers | Assigned two zeros | Assigned two ones |
|---|---|---|---|
| Legacy | legacy_hidden | legacy_zero | legacy_one |
| Explicit fixed-event probability definition | explicit_hidden | explicit_zero | explicit_one |

Each world first receives one focal initial assessment from its own observation.
That assessment and the three final observations are held identical across all
six conditions. The clarification only defines p_state_1 as P(Y=1), not confidence
in the chosen answer. It includes symmetric examples; it does not force an
answer/probability consistency constraint, reveal the oracle, or tell the model
to ignore peers. Final-condition order is deterministically randomized. Sampling
seeds match within each repetition; GPU output is not assumed deterministic.

Total: 30 * (1 + 6 * 3) = **570 real requests**, no automatic retries.
This is not the previously discussed 540-call outline: 30 initial calls are
explicitly included. Native model fingerprint and endpoint metadata required.
Use the same Qwen2.5-7B-Instruct deployment, temperature .7, top_p .9, max_tokens 256.

## Analysis fixed before execution

Two primary diagnostic contrasts, world-clustered bootstrap (20,000 resamples,
97.5% approximate percentile interval per contrast):
1. Explicit-prompt P(Y=1) under assigned ones minus assigned zeros.
2. That sensitivity under explicit instructions minus sensitivity under legacy
   instructions (within-world interaction).

Secondary: legacy sensitivity; assigned-verdict oracle error relative to hidden
under explicit instructions; hidden oracle-error change between prompt styles;
arm-specific answer/probability mismatch, accuracy and Brier scores. Secondary
intervals are exploratory 95%; no multiplicity correction. Repetitions are NOT
independent sample units. No significance-based sample extension or early stop.

All valid outputs, including inconsistent answers/probabilities, are retained.
Do not invert probabilities, exclude unfavorable worlds, impute failures, or
merge this batch into prior validation. Any failed/pending request stops the
batch without covert retry. Analysis requires full coverage and reconstructed
requests/source integrity. Inspect progress only until all requests finish.

## Interpretation and next decisions

- Sensitivity persisting with explicit semantics motivates a fresh method test
  (e.g. evidence-only commitment versus unrestricted communication), not a claim
  that this diagnostic proves DICE efficacy.
- Reduced sensitivity with lower mismatch suggests an output-semantics/instruction
  contribution; clarification also changes behavior, so internal causes remain
  underidentified.
- A wide interval or no detectable effect is not equivalence. Do not enlarge
  this batch based on its observed result. Any follow-up needs a new fixed plan.
- There are no natural-peer or heterogeneous-model arms here. This cannot settle
  the earlier natural-peer effect or generalize to all agents/tasks.

## Execution and budget

Estimated inference time from the preceding run is roughly 8 minutes; allow
10-15 minutes for hashing/startup variability. Apply a 20-minute process timeout.
At the previously displayed 1.98 credits/hour, 20 minutes is 0.66 compute credits
if that tariff still applies; actual platform billing governs. A process timeout
does not stop the cloud instance or billing. Do not open additional instances.

```bash
python -m unittest discover -s tests -v
python -m runtime.semantic_probe
timeout 20m python -u -m runtime.semantic_probe --native-config /gemini/code/agent-lineage-research/runtime/runs/native-smoke-config.json --output /gemini/code/agent-lineage-research/runtime/runs/semantic-diagnostic-001 --execute
python -m runtime.semantic_score --run /gemini/code/agent-lineage-research/runtime/runs/semantic-diagnostic-001
```

No model inference occurs without --execute. Keep raw outputs private. Back up
source/config, server evidence, raw records and launch log; compare remote/local
archive SHA-256 and locally recomputed summary/input hashes before stopping the
instance via the platform. Do not shut down a shared host.
