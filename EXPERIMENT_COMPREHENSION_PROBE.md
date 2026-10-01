# Single-observation comprehension diagnostic v1

Fixed before inference,2026-10-01. Motivated by descriptive inspection of prior
routing outputs:19/20 initial observation-zero answers disagreed with the Bayes
direction, versus0/20 observation-one;6/40 initial answer/probability mismatches.
Those counts motivated this new diagnostic and are not a population-level test.
Prior experimental results remain unchanged, including all unfavorable results.

## Fixed216 requests

9 reliability combinations: source r in(.65,.8,.9), observation t in(.7,.85,.95).
Cross with observation value0/1, two representations, two field explanations,
and three stochastic repetitions.9*2*2*2*3=216 calls, no peers or prior assessment.
All observations use identical ID O0 to remove ID-position variation.

- Layered/current: exact prior routing system prompt and clarification, single
  observation payload. Earlier dataset used other IDs; this is not exact replay.
- Layered/explicit: same generative explanation, replace probability-field text
  with a distinction between observation, hidden state and requested probability.
- Effective/current: equivalent single-observation channel with reliability
  a=r*t+(1-r)*(1-t), and current probability explanation.
- Effective/explicit: equivalent channel with replacement explanation.

Single-channel reduction is correct for ONE observation only; never flatten
multiple correlated observations into independent effective channels. It provides
precomputed likelihood information, so representation effects conflate reduced
reasoning burden and wording/format changes. Replacement clarification also
removes numeric examples; it is a bundled diagnostic, not attribution to one word.

Prior P(Y=1)=.5; exact posterior equals a for observed1 and1-a for observed0.
No hidden labels, oracle posterior field, or treatment labels enter the prompt.
Because likelihood and posterior coincide under this prior, simplifying the
channel makes the answer easier; do not call it an equally difficult task.

Order randomized within reliability cell; seeds matched across value/prompt
conditions within each repetition. GPU nondeterminism is possible. No adaptive
prompt revisions, no retries, probability inversion or selective exclusions.
Temperature.7, top_p.9, max_tokens256; same native fingerprinted model.

## Predefined descriptive readout

Report all four conditions, separately for observation0 and1: answer direction
accuracy, probability direction accuracy, MSE/MAE to exact posterior, and
answer/probability inconsistencies. Also report mean abs(p0+p1-1), per-cell
results, and descriptive MSE contrasts for simplification, clarification and
their interaction. Values at.5 are recorded and fail strict probability-direction
correctness; answer/probability inconsistency does not count ties.

This is a hand-selected grid of9 parameter cells with repeated sampling, not216
independent tasks. No population confidence intervals or significance claims.
No Bayes action accuracy is described as realized hidden-label accuracy. All
requests/results reconstructed and audited before summary, full216 coverage
required. Only progress/errors inspected while running.

If simplification helps, investigate calculation burden on fresh prompts; if
direction bias persists, test independent wording/label swaps or another model
under a new protocol. No automatically selected winning prompt or reuse of this
grid as independent confirmation. This diagnostic does not establish DICE or
communication-method efficacy.

## Operation

No new server or paidAPI. Expected3–6minutes inference,10minute process ceiling
for hashing/runtime variability. Rental idle time continues after process exit.
No recurring monitor: user previously declined background checks. Preserve and
download raw responses/config/source/logs, verify archiveSHA256 and local frozen
rescoring. PublicGitHub includes code/protocol/tests only, never raw logs.

```bash
python -m unittest discover -s tests -v
python -m runtime.comprehension_probe
timeout 10m python -u -m runtime.comprehension_probe --native-config /gemini/code/agent-lineage-research/runtime/runs/native-smoke-config.json --output /gemini/code/agent-lineage-research/runtime/runs/comprehension-diagnostic-001 --execute
python -m runtime.comprehension_score --run /gemini/code/agent-lineage-research/runtime/runs/comprehension-diagnostic-001
```
