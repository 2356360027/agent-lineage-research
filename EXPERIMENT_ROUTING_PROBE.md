# Recipient-routing pilot v1 — fixed before inference

2026-10-01. Exploratory engineering/mechanism study, not confirmatory DICE efficacy.
Prior timing pilot (separate data) did not support assuming gate benefit; gate
remains a comparator, not the chosen winning method. No previous data are changed.

## Mandatory CPU audit and method collapse

Across100 CPU-only fixture worlds, two recipients, four scenarios and two update
stages (1,600 checks), trusted-ID recipient ledger routing equals a strong
recipient-specific observation-ID deduplication baseline exactly. These are
therefore ONE GPU policy, not competing algorithms. Pure routing counterexamples
show global dedup can withhold another recipient's unseen observation; source-level
dedup can remove a genuinely new same-source observation. Those weak rules are
CPU controls only. No efficacy/novelty claim from beating them.

## Correlated-source generator and exact reference

20 fresh worlds, seed59417321. Y is a fair binary state. Each of two sources has
latent Z_s equal to Y with probability r_s chosen from(.65,.8,.9). Source states
are independent givenY. Each source yields two observations, conditionally
independent givenZ_s, matching Z_s with probability t chosen from(.7,.85,.95).
Thus same-source new observations can add information without being independent
givenY. Copies sharing an observationID add no new measurements.

The prompt specifies this generative model and observed reliabilities, never Y,
Z_s, oracle probabilities or arm names. Scoring marginalizes each Z_s exactly,
and removes repeated IDs only for the reference calculation. Unit tests compare
the reference with independent exhaustive enumeration and check copy invariance.
This controlled two-source model is not a general solution for unknown dependence.

## Exposure scenarios

Each world has two recipients: A initially knowsO0, B initially knowsO2. O0,O1
share sourceR0; O2,O3 shareR1. All scenarios reset to the same initial state.

| Scenario | First update | Second update |
|---|---|---|
| relay | both receiveO0 | both receive two more copies ofO0 |
| same_source | both receiveO0 | both receive newO1 fromR0 |
| recipient | A receivesO0; B receives nothing | both receiveO0 |
| mixed | both receiveO0 | one bundle contains oldO0 and newO1 |

Policies: raw cumulative observations; recipient unique-ID dedup; same dedup with
peer verdicts hidden at update1 and released at update2. All final unique evidence
is identical across policies within recipient/scenario and verified by scorer.
For raw, repeated entries remain. All use the same JSON schema and explicit
P(Y=1) instructions. Dedup changes entry count/token length; do not claim exactly
equal token budgets. No separate ledger policy after equivalence audit.

At both updates, raw and dedup see two assigned peer answers v=(world_index+
recipient_index) mod2. Gate sees them only at update2. Assignments are balanced
across the two recipients and independent of gold, not naturally generated agents.
No verdict-sensitivity contrast from this design: there is only one assigned
verdict per recipient, not both interventions for that recipient.

Each path uses its own preceding assessment; matched seeds across policies and
scenarios by world/recipient/stage. Path order deterministically randomized; each
path preserves update1->update2 dependency. Calls are not independent samples.
Same-source and mixed dedup paths have identical prompts and seeds; their outputs
may still vary under GPU nondeterminism. This is a software-equivalence diagnostic,
not a new method comparison; do not inflate N from such repeated conditions.

40 shared initial calls +20worlds*4scenarios*3policies*2recipients*2updates=1000
physical requests. One trajectory per path, no stochastic repetition expansion.

## Fixed primary metrics

1. Final dedup-minus-raw squared error against the evidence-conditional oracle,
   averaged equally across four scenarios and two recipients within each world.
2. Final same_source-minus-relay realized Brier under dedup, averaged across
   recipients, against each world's same Y. This tests useful new evidence, not
   merely invariance. Negative is beneficial for each metric.

Each uses a97.5% approximate world-bootstrap percentile interval,20,000 resamples.
Independent N=20, not40 recipients or960 update calls. Small exploratory study,
not a powered confirmation. Report both primary endpoints and their intervals.
Secondary95% unadjusted: gate-minus-dedup oracle error; mixed-minus-same_source
oracle error under raw/dedup. Also report all per-scenario arm means, initial
reference error (capability check), token cost and actual durations. No deletion
or inversion of inconsistent probabilities, no imputation, no pooling prior batches.

## Limits and decision boundary

Trusted stable atomic IDs are supplied by the generator. No semantic-ID discovery,
malicious metadata robustness, partial/unknown ancestry, verified derivations,
dependence-weighted final aggregation, or natural multi-agent dialogue is claimed.
If ordinary dedup works, that is baseline evidence, not novel DICE efficacy.
If it fails, retain results and investigate task capability and prompt dependence.
Future innovation needs a demonstrably different capability and fresh evaluation.

No outcomes inspected until full1000-call coverage. No silent retries, seed changes
or effect-driven sample increases. Preserve failed/pending requests and stop.
Frozen source/request reconstruction and archive+local rescoring checks required.

## Operations

Same existing model/server, temperature.7, top_p.9, max_tokens256. Anticipated inference
15–25minutes;35minute process ceiling allows hashing/runtime variability. At the
previously displayed1.98credits/hour the ceiling corresponds to1.155compute credits
if unchanged; actual platform billing governs. No new instance, no external paidAPI.
Timeout does not stop the cloud instance. User declined background checks earlier;
do not create a recurring check without renewed authorization.

```bash
python -m unittest discover -s tests -v
python -m runtime.routing_probe --cpu-audit
python -m runtime.routing_probe
timeout 35m python -u -m runtime.routing_probe --native-config /gemini/code/agent-lineage-research/runtime/runs/native-smoke-config.json --output /gemini/code/agent-lineage-research/runtime/runs/routing-pilot-001 --execute
python -m runtime.routing_score --run /gemini/code/agent-lineage-research/runtime/runs/routing-pilot-001
```
