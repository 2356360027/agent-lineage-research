#!/usr/bin/env bash
set -u
export LC_ALL=C.UTF-8
cd /gemini/code/stage-diagnostic-v1 || exit 1
run=/gemini/code/agent-lineage-research/runtime/runs/stage-diagnostic-001
test ! -e "$run" || exit 2
mkdir -p runtime/runs
date -Is
timeout --signal=TERM --kill-after=30s 10m python -u -m runtime.stage_diagnostic --native-config /gemini/code/agent-lineage-research/runtime/runs/native-smoke-config.json --output "$run" --execute
experiment_status=$?
echo "EXPERIMENT_EXIT_CODE=$experiment_status"
score_status=99
if [ "$experiment_status" -eq 0 ]; then
  python -m runtime.stage_diagnostic --output "$run" --score > runtime/runs/stage-diagnostic-001.score.log 2>&1
  score_status=$?
  echo "SCORE_EXIT_CODE=$score_status"
fi
date -Is
if [ "$experiment_status" -ne 0 ]; then exit "$experiment_status"; fi
exit "$score_status"
