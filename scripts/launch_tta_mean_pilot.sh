#!/usr/bin/env bash
# Run only this fixed development diagnostic, never a paid model API.
set -euo pipefail
export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2
export OPENBLAS_NUM_THREADS=2
export PYTHONUNBUFFERED=1
cd /gemini/code/tta-mean-pilot-20261004-001
test ! -e run
test ! -e launch.log
test ! -e tests.log
python -m unittest discover -s tests -p test_tta_mean_probe.py -v 2>&1 | tee tests.log
date -Is
nvidia-smi --query-gpu=name,memory.free,memory.used,utilization.gpu --format=csv
sha256sum runtime/tta_mean_probe.py tests/test_tta_mean_probe.py scripts/launch_tta_mean_pilot.sh
set +e
# SIGINT permits a failure record; SIGKILL after 10s bounds only this process group.
# This does not stop any other service or the billed cloud instance.
timeout --signal=INT --kill-after=10s 1790s python -m runtime.tta_mean_probe \
  --checkpoint /gemini/code/tta-mean-assets-20261004/natural.pt \
  --checkpoint-sha256 6ed1c75dad63e8ebdbef365020dd1a7eee5e79cf8a1438bd3bdc3b9c7aab3d44 \
  --cifar-root /gemini/code/tta-mean-assets-20261004/extracted \
  --trust-verified-official-cifar --output run --device cuda:0 \
  --max-seconds 1780 --batch-size 8 2>&1 | tee launch.log
experiment_code=${PIPESTATUS[0]}
printf 'EXPERIMENT_EXIT_CODE=%s\n' "$experiment_code" | tee -a launch.log
date -Is | tee -a launch.log
exit "$experiment_code"
