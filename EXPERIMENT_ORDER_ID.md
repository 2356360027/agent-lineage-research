# Order-by-ID diagnostic v1

Motivated AFTER readiness-pipeline-001 failed the double-evidence gate. All ten
answer-direction errors occurred in the two conflict cells with the stronger
observation first. This observation suggests but does not establish position or
ID-binding sensitivity. Preserve the failed gate and leave communication unrun.

This NEW exploratory protocol uses ALL six conflict cells (parent cells 1,3,5,7,9,11),
not only the failed cells. Cross two complementary value assignments, two input
orders, two ID assignments, three matched-seed draws: 6*2*2*2*3 = 144 calls.
New seed 36712908; temperature .7, top_p .9, max_tokens 256. The same physical
evidence values/reliabilities are preserved under each order/ID transform; only
list order and names O0/O1 change. System prompt is exactly the double-stage
system. No peers, probability repairs, removed items or changed gate thresholds.

Primary descriptive outcomes: mean absolute probability change under list-order
reversal, and answer-direction correctness when the stronger evidence is last
minus when it is first. Secondary: absolute probability change under ID swap,
validity, MAE, probability direction and following-the-last-item rate. Report
valid/scheduled paired denominators and all rows. These are six reused cells,
not independent validation or population-level evidence. Matched sampling seeds
do not guarantee identical GPU randomness. No significance/pass thresholds.

Invalid format is retained with null assessment and no repair; service failure
stops. No retries, no effect-based extension. Freeze before launching. Inspect
coverage/errors only until all calls complete. Ten-minute process ceiling on
existing server; no new machine/top-up. Archive raw data/config/code/logs and
verify local/remote hashes and scoring. No raw data pushed publicly. This is not
a successful rerun of the failed competence gate, and cannot establish DICE.

```bash
python -m runtime.order_probe
timeout --signal=TERM --kill-after=30s 10m python -u -m runtime.order_probe --native-config /gemini/code/agent-lineage-research/runtime/runs/native-smoke-config.json --output /gemini/code/agent-lineage-research/runtime/runs/order-diagnostic-001 --execute
python -m runtime.order_probe --output /gemini/code/agent-lineage-research/runtime/runs/order-diagnostic-001 --score
```
