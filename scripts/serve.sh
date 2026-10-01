#!/usr/bin/env bash
set -euo pipefail
CONFIG="${1:-configs/e1_pilot.json}"
readarray -t SETTINGS < <(python3 - "$CONFIG" <<'PY'
import json, sys, re
c=json.load(open(sys.argv[1]))
assert re.fullmatch('[0-9a-f]{40}',c['revision']), 'Pin model revision'
assert '@sha256:' in c['server_image'], 'Pin Docker digest'
for k in ['server_image','model','revision','dtype']: print(c[k])
PY
)
if [ "${#SETTINGS[@]}" -ne 4 ]; then exit 1; fi
mkdir -p runtime/runs
nvidia-smi > runtime/runs/server-gpu.txt
docker run --rm --gpus all --shm-size 8g \
  -p 127.0.0.1:8000:8000 \
  -v lineage-hf-cache:/root/.cache/huggingface \
  "${SETTINGS[0]}" --model "${SETTINGS[1]}" --revision "${SETTINGS[2]}" \
  --dtype "${SETTINGS[3]}" --max-model-len 4096 --max-num-seqs 4 \
  --gpu-memory-utilization 0.90 --generation-config vllm --seed 20261001
