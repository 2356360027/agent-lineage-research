#!/usr/bin/env bash
# Run only the frozen 672-call batch; no retries or conditional extra samples.
set -u
export LC_ALL=C.UTF-8
cd /gemini/code/id-wording-v1 || exit 1
run=/gemini/code/agent-lineage-research/runtime/runs/id-wording-001
test ! -e "$run" || exit 2
mkdir -p runtime/runs
date -Is
timeout --signal=TERM --kill-after=30s 15m python -u -m runtime.id_wording_probe --native-config /gemini/code/agent-lineage-research/runtime/runs/native-smoke-config.json --output "$run" --execute
experiment_status=$?
echo "EXPERIMENT_EXIT_CODE=$experiment_status"
if [ "$experiment_status" -eq 0 ]; then
  python -m runtime.id_wording_probe --output "$run" --score > runtime/runs/id-wording-001.score.log 2>&1
  score_status=$?
  echo "SCORE_EXIT_CODE=$score_status"
else
  score_status=99
fi
date -Is
if [ "$experiment_status" -ne 0 ]; then exit "$experiment_status"; fi
exit "$score_status"
