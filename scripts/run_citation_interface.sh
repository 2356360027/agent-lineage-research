#!/usr/bin/env bash
# One frozen 64-call batch. Never retry, repair, or start another batch.
set -u -o pipefail
if [ "$#" -ne 5 ]; then
  printf '%s\n' 'Usage: bash scripts/run_citation_interface.sh NATIVE_CONFIG CANDIDATES PLAN EXPECTED_PLAN_SHA256 RUN_DIR'
  exit 2
fi
native_config=$1
candidates=$2
plan=$3
expected_plan=$4
run_dir=$5
if [ -e "$run_dir" ]; then
  printf '%s\n' 'Run directory exists; refusing overwrite/retry.'
  exit 3
fi
printf 'START_UTC='; date -u +%FT%TZ
printf 'FROZEN_WORKDIR=%s\n' "$PWD"
printf 'EXPECTED_PLAN_SHA256=%s\n' "$expected_plan"
python --version
nvidia-smi
ps -eo pid,lstart,args | grep '[v]llm serve'
sha256sum "$native_config" "$candidates" "$plan"
timeout --signal=TERM --kill-after=30s 15m python -u -m runtime.citation_interface_v1 \
  --native-config "$native_config" --candidates "$candidates" --plan "$plan" \
  --expected-plan-sha256 "$expected_plan" --output "$run_dir" --execute
experiment_exit=$?
printf 'EXPERIMENT_EXIT_CODE=%s\n' "$experiment_exit"
printf 'END_UTC='; date -u +%FT%TZ
printf '%s\n' 'Process timeout is not cloud-instance shutdown; rental billing continues.'
exit "$experiment_exit"
