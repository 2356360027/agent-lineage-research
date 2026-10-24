#!/usr/bin/env bash
# Exactly one separately frozen batch; no retries or automated next batch.
set -u -o pipefail
if [ "$#" -ne 6 ]; then
  printf '%s\n' 'Usage: run_citation_interface_v2.sh NATIVE CANDIDATES PLAN PLAN_SHA RUN SERVER_LOG'
  exit 2
fi
native_config=$1
candidates=$2
plan=$3
expected_plan=$4
run_dir=$5
server_log=$6
if [ -e "$run_dir" ]; then
  printf '%s\n' 'Existing run; no overwrite or retry.'
  exit 3
fi
printf 'START_UTC='; date -u +%FT%TZ
printf 'FROZEN_WORKDIR=%s\n' "$PWD"
printf 'EXPECTED_PLAN_SHA256=%s\n' "$expected_plan"
python --version
nvidia-smi
ps -eo pid,lstart,args | grep '[v]llm serve'
sha256sum "$native_config" "$candidates" "$plan"
timeout --signal=TERM --kill-after=30s 15m python -u -m runtime.citation_interface_v2 \
  --native-config "$native_config" --candidates "$candidates" --plan "$plan" \
  --server-log "$server_log" --expected-plan-sha256 "$expected_plan" \
  --output "$run_dir" --execute
experiment_exit=$?
printf 'EXPERIMENT_EXIT_CODE=%s\n' "$experiment_exit"
printf 'END_UTC='; date -u +%FT%TZ
printf '%s\n' 'Timeout does not stop the cloud instance or rental billing.'
exit "$experiment_exit"
