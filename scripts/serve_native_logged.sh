#!/usr/bin/env bash
# Serve only after a successful exact-content migration check. Log redirection
# is supplied by the operator so the original model startup log is retained.
set -euo pipefail
export LC_ALL=C.UTF-8
check_file="${1:?migration model-check.json required}"
model_path=$(python -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["content_matches"] and d["packages_match"] and d["revision_stable"]; print(d["model_path"])' "$check_file")
exec vllm serve "$model_path" \
  --served-model-name Qwen/Qwen2.5-7B-Instruct \
  --host 127.0.0.1 --port 8000 --dtype bfloat16 \
  --max-model-len 4096 --max-num-seqs 4 \
  --gpu-memory-utilization 0.85 --enforce-eager \
  --generation-config vllm --seed 20261001
