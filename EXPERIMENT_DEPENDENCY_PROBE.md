# Dependency probe v1: useful computation versus unsupported verdict influence

Status: experimental infrastructure, not a completed empirical finding. Run v1
only with real local-model inference. Software-test fixtures are never paper data.
This protocol follows the first E1 pilot but uses fresh world seeds and a changed
final-assessment system prompt; do not pool its outcomes with that pilot.

## Question and scope

When all observations are already available to an agent, a peer answer contains
no additional task observation, but could help the agent compute a better answer.
Alternatively, an unsupported answer may pull its judgment away from the exact
posterior. We separate these possibilities instead of equating agreement with
collapse or equating every answer change with harm.

This is a synthetic binary-source task with specified reliabilities and exact
Bayesian reference probabilities. It tests one model and one task distribution.
It does not yet establish cognitive independence, real-world generalization,
source-lineage routing efficacy, or a new research field.

## Frozen design

`configs/dependency_probe_v1.json` is the source of truth. All arms use the same
three observations, focal agent's initial answer/probability/citations, and final
system prompt. Focal agent rotates by item index. Each initial assessment is
sampled once from one observation and reused across all conditions and repetitions.
The final prompt explains that peers have no observations beyond the visible list.

| Arm | Peer answers visible | Purpose |
|---|---|---|
| hidden (H) | None | Evidence-complete reference |
| natural (N) | Two frozen, genuinely sampled peer answers | Net natural advice effect |
| injected_zero (V0) | Two experimenter-assigned zeros | Controlled verdict intervention |
| injected_one (V1) | Two experimenter-assigned ones | Controlled verdict intervention |
| hidden_repeat (Hr) | None; distinct sampling seed | Same-prompt sampling variation |

V0 and V1 are interventions, not fabricated natural agents. Assignment is
independent of gold labels and oracle probabilities. No oracle is supplied to
the model. Arm labels do not enter prompts. Peer confidences and rationales are
not supplied; the focal agent's own prior confidence IS supplied in all arms.
H/N/V0/V1 share a sampling seed within each repetition; Hr has another seed and
a different request key, ensuring a genuine call rather than a cache hit.
Same seeds do not imply exact deterministic GPU execution or identical random
draws across different prompts. This is not an exact-seed nondeterminism test.
All repetition/arm update calls are shuffled within each world.

Exploration: **40 new worlds x (3 initial calls + 3 repetitions x 5 arms) = 720 calls**.
Potential unchanged-protocol replication: **160 new worlds, 2,880 calls**. This
second size is a provisional feasible replication, not a demonstrated power
calculation. It is NOT launched automatically. Do not tune on its outputs.
If exploration motivates a changed communication method, define a new protocol,
endpoints, precision/power target, sample size and unused seed before testing it.
Do not call a redesigned method the prespecified v1 replication.

## Outcomes and interpretation

With prior 0.5 and independent source observations o_i in {0,1}, reliability r_i:

    logit(q) = sum_i (2 o_i - 1) log(r_i / (1-r_i))

For reported probability p, conditional expected Brier is
`q(1-q) + (p-q)^2`. Thus `(p-q)^2` measures excess conditional Brier due to an
imperfect posterior calculation. Realized-label Brier `(p-Y)^2` remains separately
reported. A lucky correct hard answer need not be a good probability estimate.

Two primary endpoints, averaged over repetitions within world first:

1. Natural advice: `(pN-q)^2 - (pH-q)^2`. Negative is an improvement.
2. Assigned-verdict response: `pV1 - pV0`. Positive indicates directional
   sensitivity, not by itself harm.

Prespecified secondary descriptive measures:

- Assigned-risk contrast: `[(pV0-q)^2 + (pV1-q)^2]/2 - (pH-q)^2`.
- Sampling variation `abs(pHr-pH)` and natural change `abs(pN-pH)`; do NOT
  subtract them to claim a noise-corrected causal effect.
- Realized-label Brier, explicit-answer accuracy, probability-threshold accuracy,
  probability ties, and explicit-answer/probability inconsistency. Keep inconsistent
  outputs, rather than silently changing answers or resampling.
- Per-world oracle/focal probabilities, natural peer answers, per-arm mean
  probabilities; unanimous-peer attraction is descriptive and not a randomized
  subgroup effect.

Independent resampling units are worlds (40 in exploration), not 120 repetitions.
Use 20,000 item bootstrap samples and two-sided 97.5% percentile intervals for
each of the two primary endpoints (approximate Bonferroni coverage). Secondary
intervals are 95%, exploratory and not multiplicity-controlled. Small-sample
bootstrap intervals are approximate. No detected effect is not equivalence.
Keep every world and report outlier sensitivity without selectively deleting it.

## Execution on the existing server

Prerequisites: the complete current code is deployed, vLLM is already serving the
fingerprinted Qwen model on loopback port 8000, and the preserved native config
is `runtime/runs/native-smoke-config.json`. Do not download weights again or
change the installed GPU environment merely to run this standard-library client.

```sh
cd /gemini/code/agent-lineage-research
python -m unittest discover -s tests -v
python -m runtime.dependency_probe
```

The second command is a plan, with no inference. After both succeed:

```sh
python -u -m runtime.dependency_probe --execute --output runtime/runs/dependency-explore-001
python -m runtime.probe_score --run runtime/runs/dependency-explore-001
```

Run in a persistent platform terminal or tmux. Stop if any command fails; do not
continue to scoring or silently start a replacement sample. A read-only coverage
check (no effect estimates, safe for validation) is:

```sh
python -m runtime.probe_score --run runtime/runs/dependency-explore-001 --audit-only
```

For unchanged-protocol replication, only after checking and freezing the plan:

```sh
python -m runtime.dependency_probe --stage validate --output runtime/runs/dependency-validate-001
python -u -m runtime.dependency_probe --stage validate --confirm-frozen-validation --execute --output runtime/runs/dependency-validate-001
```

Do not inspect validation outcome files until all planned items have completed.
These instructions do not launch this optional phase or authorize extra machines.

## Integrity and restart rules

- Save every requested prompt, sampling seed, raw reply, token usage and latency.
  No retries, probability imputation or selective exclusion.
- Manifest freezes design, config, all runtime Python sources and their digest;
  model files and package versions are rechecked before inference. Live endpoint
  model name/path/context length are checked on each invocation. Keep vLLM startup
  logs independently: file hashes and endpoint metadata are NOT proof of loaded
  GPU memory or independent execution attestation.
- The scorer reconstructs all prompts and verifies recorded results. It refuses
  incomplete datasets, incompatible shards or changed runtime source. Analyze
  using the preserved source version, not a silently edited scorer.
- Successful cached calls are reusable only with the original config/source.
  Failed/pending calls require diagnosis and an explicit recorded decision; do not
  overwrite them. Remove a stale lock only after confirming its process stopped.
- Keep original pilot data untouched. Archive all run shards, native config,
  source version, startup logs and summaries off the rental platform. Do not push
  private raw logs, credentials or model weights to the public repository.
- Measure actual tokens and wall time. The earlier pilot's latency is a planning
  reference, not a completed timing result for this protocol. Downloads, model
  hashing, startup and rental idle time are separate costs.

## Decision after exploration

If natural advice improves posterior calculation but assigned verdicts increase
error, test communication that preserves verifiable calculations while restricting
unsupported verdicts. Include hidden/natural controls, an equal-budget generic
recheck, and the proposed method, on fresh worlds. If numerical calculation is
the dominant failure, investigate that mechanism first. If neither contrast is
stable, report insufficient evidence and revise the scientific question without
searching for favorable seeds. DICE efficacy needs additional lineage and
repeated-evidence conditions; it is not established by this probe alone.
