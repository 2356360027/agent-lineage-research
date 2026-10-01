# Real-inference pilot: first deployment

This repository contains experimental infrastructure, not validated findings.
Legacy manuscript, plots and rule-simulation outputs are deliberately excluded.
Python 3.10+; client and tests require only the standard library. Linux/NVIDIA
Docker is needed only for the inference server. No training is required.

## Layout

- `runtime/e1.py`: deterministic synthetic worlds; frozen S0; real chat transport;
  four evidence/verdict arms; per-call raw responses, usage, checkpoints and locks.
- `runtime/score.py`: separate gold reconstruction and descriptive scoring.
- `configs/e1_pilot.json`: exploratory configuration, revision/image locks required.
- `scripts/serve.sh`: loopback-only GPU model server.
- `tests/test_runtime.py`: software tests with explicitly mocked transport.

## Local validation (no model calls)

```sh
python -m unittest discover -s tests -v
python -m runtime.e1 --config configs/e1_pilot.json
```

Default pilot: 20 worlds x 1 repetition x (3 initial + 4 updates) = 140 calls.
No inference happens without `--execute`. This is not the complete E2/E3 suite.

## Server selection and budget

Start with ONE dedicated 24GB NVIDIA GPU (RTX 4090/3090 class), 8 vCPU,
32GB RAM (64GB preferred), 100GB persistent SSD, Ubuntu 22.04/24.04 and
provider-supported NVIDIA Container Toolkit + Docker. Check actual free VRAM,
driver/image compatibility and access to Hugging Face before paying for many hours.
Qwen2.5-7B-Instruct BF16 is an engineering pilot, not a claim of frontier coverage.
Its 7.61B parameters alone take roughly 15.22 GB decimal at two bytes/parameter;
runtime and KV cache need additional memory. Context is capped at 4096.
24GB suitability is an estimate to validate at startup, not a measured benchmark.

Suggested allocation of a CNY 500 ceiling: <=50 environment/smoke test,
<=100 exploratory pilot, reserve 350 for revised experiments and replication.
These are spending caps, NOT provider quotes. Rental billing continues while idle;
stop the instance after backing up results. Paid GPU time excludes any assumption
of an API token fee: this configuration serves downloaded weights locally.

## GitHub and deployment

Use a public code-only repository. Do not commit `.env`, tokens, SSH keys,
private paper files, model weights or raw experiment outputs. No software license
has yet been chosen by the authors.

```sh
git clone --branch experiment/e1-pilot https://github.com/OWNER/REPO.git
cd REPO
python3 -m unittest discover -s tests -v
nvidia-smi
docker info
```

Before execution choose a stable vLLM image compatible with the server driver.
Pull its explicit version, inspect its repository digest, and set `server_image`
to `vllm/vllm-openai@sha256:...` in the config. Resolve the model's exact commit
from the official Hugging Face repository and set `revision` to its 40-character
SHA. Do not use a moving `main` revision or `latest` image for experiments.
Commit this locked config on the experiment branch and deploy the SAME commit
to every worker. The placeholders intentionally block unpinned execution.

Start in a persistent terminal (provider terminal/tmux). First terminal:

```sh
bash scripts/serve.sh configs/e1_pilot.json
```

Second terminal, after model startup succeeds:

```sh
curl --fail http://127.0.0.1:8000/v1/models
python3 -m runtime.e1 --execute --output runtime/runs/pilot-001
python3 -m runtime.score --run runtime/runs/pilot-001
```

Do not expose port 8000 to the public Internet. If necessary use an SSH tunnel.
Save the Docker digest, startup log, GPU/driver information and locked git commit
alongside the raw outputs. The client records declared revision; deployment logs
must independently establish that those weights were actually loaded.

## Multiple machines AFTER the first pilot passes

Use identical hardware/software when pooling a model's results. One model copy
can play all three agents in separate contexts; three agents do NOT need three GPUs.
Use the same branch/commit/config on all machines, split by item, not by condition.

```sh
# server 0
python3 -m runtime.e1 --execute --shards 2 --shard 0 --output runtime/runs/pilot-002
# server 1
python3 -m runtime.e1 --execute --shards 2 --shard 1 --output runtime/runs/pilot-002
```

Download both `shard-000` and `shard-001` directories under one local run folder,
then score that folder. Never mix model revisions or change shard count mid-run.
Re-running the exact command reuses successful calls. Failed/pending calls STOP
the run; preserve and diagnose them rather than silently resampling. A stale
`.running.lock` must only be removed after confirming its process has stopped.
An interrupted request may have consumed tokens even if usage was not received.

## Scientific interpretation and next gates

E1 changes peer verdict visibility, not source-level independence. Paired E1C1 vs
E1C0 share evidence, frozen focal assessment, peer assessments and sampling seed.
E0C1 can reveal information about unseen observations; do not conflate its effect
with evidence-fixed social influence. Prompts disclose the generative model.
Focal agent rotates by item; arm order is randomized independently of treatment.
Same seed does not guarantee deterministic GPU execution or perfect coupling.

Inspect parse failure rate, probability/answer consistency, latency, output-token
distribution, ceiling/floor effects and actual verdict diversity first. No effect
is a valid outcome. Do not pick settings to guarantee the hypothesis. Do not count
repetitions as independent items. Preregister confirmatory sample size/endpoints
after pilot; later add item-clustered confidence intervals, E2/E3 controls, a second
model family and an external task. This pilot alone cannot support a full paper.

Time estimate must come from the pilot: for N worlds and R repetitions there are
7NR successful calls, excluding failures. Estimate worker time using observed mean
call time; record loading/download separately. No real inference has been measured
at initial scaffolding time.

## Official references

- https://huggingface.co/Qwen/Qwen2.5-7B-Instruct
- https://docs.vllm.ai/en/latest/serving/openai_compatible_server/
- https://docs.vllm.ai/en/latest/deployment/docker/
