#!/usr/bin/env bash
set -u
export LC_ALL=C.UTF-8
cd /gemini/code/routing-timing-v2 || exit 1
run=/gemini/code/agent-lineage-research/runtime/runs/routing-timing-002
native=/gemini/code/agent-lineage-research/runtime/runs/migration-20261002/native-new.json
test ! -e "$run" || exit 2
mkdir -p runtime/runs
date -Is
timeout --signal=TERM --kill-after=30s 60m python -u -m runtime.routing_timing_v2 --native-config "$native" --output "$run" --execute
experiment_status=$?
echo "EXPERIMENT_EXIT_CODE=$experiment_status"
score_status=99
if [ "$experiment_status" -eq 0 ]; then
  python -m runtime.routing_timing_v2 --output "$run" --score > runtime/runs/routing-timing-002.score.log 2>&1
  score_status=$?
  echo "SCORE_EXIT_CODE=$score_status"
fi
date -Is
if [ "$experiment_status" -ne 0 ]; then exit "$experiment_status"; fi
exit "$score_status"
