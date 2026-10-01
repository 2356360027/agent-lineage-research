# Communication-timing component pilot v1

Frozen before execution, 2026-10-01. This is a small exploratory method-component
pilot motivated by earlier verdict sensitivity that persisted after probability
clarification. It is NOT a full DICE evaluation or a confirmatory validation.

## Factorial intervention

40 fresh synthetic worlds, seed 31620487. One stochastic trajectory per condition
per world. All stages use the same explicit fixed-event P(Y=1) instructions.
Initial S0: focal agent sees one observation. Two matched evidence regimes:

- repeat: S1 and S2 contain three copies of the same observation ID.
- new: S1 and S2 contain that observation plus two genuinely new independent
  sources' observations. IDs and reliabilities remain visible.

Three policies, each with initial, early, and final assessment stages:

| Policy | Early S1 | Final S2 |
|---|---|---|
| open | evidence plus assigned peer verdicts | same evidence and verdicts, own S1 |
| gate | evidence only, independent assessment | same evidence plus assigned verdicts, own S1 |
| evidence | evidence only | evidence only, own S1 |

For open and gate, independently intervene on two peers' verdicts: both0 or both1,
without changing task evidence. These are artificial treatment messages, not
claims about naturally generated agents. No oracle-based choice of treatments.
Peer verdicts are opinions with no observations beyond the visible list.

Gate is the commit-then-discuss/timing baseline itself, not a renamed novel DICE.
Recipient-specific lineage routing, verified derivations and dependence-weighted
aggregation are NOT implemented in this pilot. Do not claim otherwise.

Open/gate comparisons have matched final evidence, verdicts, output schema and
logical depth. The treatment changes early verdict visibility and hence the
intermediate assessment. Each final prompt contains the immediately preceding
assessment; this is a structured-state pipeline, not an unrestricted chat history.

Identical evidence-only early states are shared across gate0, gate1 and evidence.
Evidence-only has only one path, not two fake independent verdict arms. Shared
prefixes are recorded once. Per world: 1 initial + 2 regimes * (3 early + 5 final)
=17 physical requests; total680. Logical paths have the same three assessment
stages, but actual total tokens are measured, not claimed to be exactly matched.
No additional independent-repeat/noise-control arm in this budget-limited pilot.

Regime and within-stage order randomized by frozen seeds. All early outputs of
a regime are generated before its final outputs. Paired seeds by world/stage;
no assumption of deterministic GPU inference. Temperature.7, top_p.9, max_tokens256.

## Predeclared endpoints

Two primary pilot contrasts, each 97.5% approximate world-bootstrap percentile
interval (20,000 resamples). N=40 worlds; reuse and conditions are not independent N.

1. Gate minus open squared error against the evidence-conditional oracle,
   averaged equally across both verdicts and both evidence regimes. Negative is
   better for gate. For repeat the oracle uses one unique observation; for new
   it uses all three. Do not count copies as independent evidence.
2. Evidence-only new minus repeat realized Brier score, evaluated against the
   SAME hidden label of each world. Negative indicates benefit from receiving
   new evidence; no oracle-error comparison across unequal information targets.

Secondary exploratory contrasts include per-regime verdict sensitivity, gate-open
sensitivity difference, evidence-only vs open oracle error, and new-repeat Brier
for open/gate. Arm means include accuracy, Brier, probability and inconsistency.
Secondary intervals95%, no multiplicity correction. Report all outcomes; never
claim efficacy because an isolated secondary interval excludes zero.

## Interpretation and stopping

If timing reduces error while new evidence improves predictions, the component
merits a fresh larger validation and comparison to full lineage mechanisms.
If final verdict release restores influence, delayed exposure alone may be
insufficient; report this instead of hiding it. If evidence-only fails to exploit
new evidence, investigate task/prompt capability before scaling the method.
Do not claim equivalence from a nonsignificant small pilot. No effect-driven
extension, prompt edits, resampling, probability inversion or output exclusions.
Zero verdict sensitivity of evidence-only would be structural, not a discovery.

Fixed680 calls, no retries; failures preserved and surfaced. No intermediate
effect analysis. Full-coverage scoring reconstructs every request and graph edge
from raw responses, checks frozen source and output hashes. Prior datasets remain
unchanged; all batches analyzed separately. Raw data stays out of public GitHub.

## Operation

Same existing Qwen2.5-7B-Instruct/RTX4090D service, no new rental. Expected inference
roughly10–15minutes based on measured prior latency, allow25minute process limit.
At the previously displayed1.98credits/hour,25minutes corresponds to0.825credits
if that tariff applies; actual platform billing controls. Timeout does not stop
the instance. Back up and verify before asking user to stop it in the platform.

```bash
python -m unittest discover -s tests -v
python -m runtime.timing_probe
timeout 25m python -u -m runtime.timing_probe --native-config /gemini/code/agent-lineage-research/runtime/runs/native-smoke-config.json --output /gemini/code/agent-lineage-research/runtime/runs/timing-pilot-001 --execute
python -m runtime.timing_score --run /gemini/code/agent-lineage-research/runtime/runs/timing-pilot-001
```
