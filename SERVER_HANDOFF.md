# Experiment server handoff

This repository contains research code and exploratory diagnostics, not a
validated DICE method or a completed conference submission. On 2 October 2026,
experiments were paused for server expiry. No additional inference was started
during archiving. Raw responses and private deployment records are deliberately
excluded from this public repository.

## Preserved work

The private retirement archive preserves 21 run directories, including early
smoke tests, incomplete or unsuccessful diagnostics, original responses,
manifests, configurations, frozen source snapshots, launch logs and score logs.
Directory presence does not mean that an experiment passed its competence gate.
Existing local data are not overwritten by the retirement snapshot.

The retirement archive contains 11,680 files, all verified individually against
its SHA-256 manifest. All 10,634 files under the server's main run-data directory
also match their pre-existing local copies. The archive SHA-256 is
`213253f889bd743e8d5cf754cda95bee46c44634d6a856137e69ec7df4d9f159`.

The archive also contains model configuration and tokenizer files, an installed
package inventory, and GPU information. It excludes credentials, shell history,
home directories, Git configuration, and the approximately 15 GB of model
weights. Native run configurations preserve weight-file sizes and SHA-256 hashes.
The downloaded model used a moving ModelScope revision; a name or `master`
reference alone is not sufficient to reconstruct identical weights. Compare
every recorded model-file hash after downloading again. If any hash differs,
treat the deployment as a different model snapshot, not an exact replication.

## Latest frozen experiments

| Experiment | Frozen commit | Calls | Purpose |
|---|---|---:|---|
| lineage utility | `c213d110434760450a16a928e8afdc76afb7bb34` | 480 | Compare supplied-origin routing and model selection |
| origin representation | `74ff4e39df2778b9c8e448d1411693d4009bda76` | 408 | Compare map, inline and grouped source representations |
| prompt ID bridge | `f41244e5ec482b9925e03f914f439f706c41cdc9` | 384 | Cross instruction bundles with ID conventions |

These three batches completed and were individually archived, hash-checked and
replayed locally against their frozen source. Their summary, row metrics, paired
contrasts and input hashes matched the server outputs. Software fixtures are
marked as fixtures and are not empirical observations. The bridge source passed
66 local and 66 server tests before inference.

## Current evidence and limitations

The prompt ID bridge uses 12 fresh synthetic cells, two complemented labels,
copy and independent-origin cases, and separate selector and final-answer calls.
All final calls receive a correct calculator result. In the copy cases:

| Instruction bundle | ID convention | Correct origin selection | Final posterior MAE |
|---|---|---:|---:|
| Legacy adapted | Sequential | 24/24 | 0.055218 |
| Legacy adapted | Opaque | 21/24 | 0.041633 |
| Current | Sequential | 16/24 | 0.003999 |
| Current | Opaque | 15/24 | 0.002126 |

This is a descriptive task-specific difference, not evidence of a universal
mechanism. Selecting origins and producing probabilities respond differently
to the instruction bundles. The calls use separate contexts, so this does not
demonstrate an internal recognition-use dissociation. There were four
answer/probability contradictions among all 192 final calls; they are retained.

The legacy prompt normalizes the map field name to the current JSON schema.
Consequently this is not an exact replay of the earlier experiment. Instruction
and identifier bundles differ in length and several cues. The independent unit
is the task cell, not each API call. Results are from one small model and synthetic
tasks. No DICE efficacy, general multi-agent independence, or significance claim
is established. Programmatic source deduplication is an ordinary engineering
baseline, not a claimed novel algorithm.

## Restore on a replacement server

1. Clone the repository and select the frozen experiment commit or branch.
   Use its archived source for replay: audit functions hash all runtime modules,
   so scoring an old run from a newer checkout can correctly fail source checks.
2. Transfer the private run archive separately. Verify its SHA-256 before
   extracting to a new directory; do not overwrite an existing run.
3. For offline auditing, use the matching frozen source and run its scorer.
   This requires no GPU or model calls. Preserve the original manifest unchanged.
4. For new inference, reconstruct the original environment as closely as possible:
   Python 3.12, PyTorch 2.6.0 with CUDA 12.4, vLLM 0.8.3, RTX 4090 D 24 GB.
   Consult the private package inventory for exact installed versions. Matching
   these versions is not by itself a guarantee of bitwise-identical inference.
5. Download Qwen/Qwen2.5-7B-Instruct and compare the recorded file hashes.
   Keep the original model fingerprint and native config. Record new paths and
   environment information in a new deployment config, not in old run artifacts.
6. Start the model service on loopback only. The previous command was:

```sh
vllm serve /path/to/Qwen2.5-7B-Instruct \
  --served-model-name Qwen/Qwen2.5-7B-Instruct \
  --host 127.0.0.1 --port 8000 --dtype bfloat16 \
  --max-model-len 4096 --max-num-seqs 4 \
  --gpu-memory-utilization 0.85 --enforce-eager \
  --generation-config vllm --seed 20261001
```

Run the software tests and one separate engineering smoke test before paying
for a larger batch. Existing launch scripts contain old absolute deployment
paths; prepare new launch paths explicitly. Never silently resume or overwrite
a failed run. Preserve truncation, malformed outputs and negative results.

## Proposed next experiment

Not yet run: cross the two final instruction bundles with correct calculator
inputs, duplicated-origin inputs, and omitted-independent-origin inputs. Keep
the arithmetic correct for each declared input set, and expose its provenance.
Measure correct-tool damage, wrong-tool adherence, posterior MAE and answer
consistency. This distinguishes input verification from simply trusting a
calculator. Include a deterministic provenance validator as a strong baseline.
Freeze sample size, prompts, seeds, scoring and spending limits before execution.

Stopping an experiment, closing SSH, or archiving files does not stop cloud
billing. Stop or release the instance using the provider's controls only after
private backups are verified. There is no need to shut down a shared host.
