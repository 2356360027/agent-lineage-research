# Local batch preparation and execution boundaries

2026-10-24. The server is stopped; the current authorization is local preparation
only. The fixed development allocation and scripted orchestration are ready for
local inspection. Live execution remains a separate, unapproved step.

## Fixed allocation

There are 12 trajectories, from two current tasks and six constructed historical
caches. Each history appears under read prices 1 and 8, with identical nonprice
inputs and a shared seed. Seeds 1700..1705 follow the listed history order
h200a, h200b, h200c, h234a, h234b, h234c. Arm order alternates low/high then
high/low. This is deterministic counterbalancing, not randomization.

Recovery-v2 permits at most 23 generations and 21 tool attempts per trajectory.
The full allocation therefore has ceilings of 276 generations and 252 tool
attempts. These are limits, not predicted usage or independent sample counts.
Raw output is capped at 512 tokens; the proposed context window is 32768.
The budget remains 200 exploration units plus 10 reserved recovery units, not yuan.

Plan SHA256:
`3666fbbcbe8e16c22bc76eca6b827fd2e111905ebd35c21ca41de0999dfccdab`.
The plan contains canonical model-visible inputs and cache fingerprints, not
candidate reference contracts or target SQL. The separate private cache file
contains complete historical results. Hashes bind the bytes actually parsed.

## Local orchestration evidence

The scripted batch joins the actual recovery loop, capacity check, adapter,
observation policy, tool session and exclusive journals. Its backend must be
injected and is labelled SCRIPTED_NOT_MODEL; it has no live command-line mode
or default network transport.

Seven batch tests and five source-package tests pass. One synthetic SQLite test
walks all 12 trajectories through 20 cache reads, one recovery SQL and a final
answer: 276 scripted dispatches and 12 synthetic SQL queries. It does not execute
the BIRD research database. Network access is forbidden in these tests.
Other tests cover serialized-plan round trips, changed allocation/cache rejection,
provider mismatch, late response retention, and invalid contracts without retry.

Model protocol failures remain in their original slots. An invalid contract
ends that trajectory without repair; subsequent planned trajectories may proceed.
An infrastructure exception, invalid environment or finalization failure stops
the batch. Unstarted slots remain NOT_STARTED in the fixed twelve-slot report.
No failed slot is replaced with a new seed or a successful rerun.

The 1800-second deadline is cooperative: checks occur before dispatch and before
and after tools. A late tool completion is retained by its tool journal. This
is not a hard process timeout and never stops instance billing. A live launcher
still needs an external process supervisor, current model/serving checks and
explicit resource approval.

## Source and remaining work

The revised component bundle contains 20 allow-listed source files, including the
batch module. Its manifest is
`5a834c1bc4b2f87831d4449b257286be256ae7786a72e8849102565c30b7e37d`.
It remains a component snapshot, not a full deployment with model weights and
private data. Earlier snapshots are preserved.

Before a live batch: bind the full deployment and model identity, establish
provider/tokenizer agreement and actual serving capacity, enforce an external
timeout, verify the private output destination, and obtain approval for the
current server, budget and fixed run. Do not infer approval from this plan.

The next scientific observation is still whether price changes the pre-result
contract and whether a later program follows a contradictory interpretation.
These engineering tests do not estimate that effect, resolve semantic labels,
or establish an advantage over strong reuse-and-recompute baselines.
