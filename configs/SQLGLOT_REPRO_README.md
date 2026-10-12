# SQLGlot component audit (2026-10-12)

This is **not model training, a new reuse algorithm, or a BIRD accuracy result**.
The fixed BIRD programs are treated as SQL programs, not approved natural-language
intent labels. All authored counterexamples are disclosed in the runner. Preserve
timeouts, unsupported cases, column labels, NULLs, types and duplicate rows.

Public code includes the audit runner, the read-only execution dependency,
bounded data downloaders, tests and compact run receipts. Raw results, databases,
model weights and private logs remain outside Git. Python 3.12+ is required for
SQLite defensive configuration; the recorded environment was Python 3.13.9 and
SQLite 3.51.0. Different SQLite versions can produce different plans and timings.

## Component tests

```sh
python -m unittest tests.test_bird_fetch tests.test_bird_readonly_audit tests.test_sqlglot_repro_audit -v
```

The recorded final run passed 52 tests. Initial test-fixture cleanup and diagnostic
UTF-8 read errors were fixed before their respective production batch started.

## Reacquire pinned artifacts

Use new, unused directories. The BIRD downloader fetches only selected SQLite
members through verified HTTP ranges, not the whole archive. It refuses a changed
remote entity; do not silently replace it with a new release. Dataset licensing
is separate from software licensing: consult the downloaded official BIRD README
(CC BY-SA 4.0). SQLGlot is MIT and its license is saved with the wheel.

```sh
python scripts/fetch_bird_fixed.py metadata --directory runtime/runs/datasets/bird-reproduction
python -m runtime.bird_readonly_audit prepare --data-dir runtime/runs/datasets/bird-reproduction
python scripts/fetch_bird_fixed.py databases --directory runtime/runs/datasets/bird-reproduction
python scripts/fetch_sqlglot_repro.py --output runtime/runs/dependencies/sqlglot-reproduction
python -m runtime.sqlglot_repro_audit prepare --data runtime/runs/datasets/bird-reproduction --wheel runtime/runs/dependencies/sqlglot-reproduction/sqlglot-30.22.0-py3-none-any.whl --output runtime/runs/sqlglot-reproduction
python -m runtime.sqlglot_repro_audit run --output runtime/runs/sqlglot-reproduction
```

The first three steps are unnecessary when using the already verified local
dataset. The new audit does not mutate source databases. SQLite defenses are not
an OS sandbox: execute only the pinned, reviewed inputs; never point third-party
evaluation cleanup scripts at the frozen source directory.

If the exact selection/source hash differs, stop and record an adaptation instead
of bypassing the identity check. The historical source snapshot and acquisition
receipts remain the reference. No production batch may overwrite an existing one.

## Recorded outcomes

- Fixed audit: 132 SQL executions; 131 complete and 1 soft timeout. Among complete
  transformed programs, typed result values matched their original SQL. Some
  output column names changed, so arbitrary downstream interface compatibility
  is not established.
- Six explicitly authored negative pairs agree on their base data and disagree
  on their counterstate. This is a software counterexample test, not a population
  error-rate estimate or a novel scientific discovery.
- Separate post-hoc diagnostic: 3 query plans plus 3 result executions; original
  and omit-unnest candidate completed, default optimization timed out again.
  Fixed order and one observation per candidate do not support a speedup claim.
- Zero model/API calls. Original databases and source-batch artifacts unchanged.

See the two `configs/run_records/sqlglot_*_20261012_001.json` receipts. This is an
execution of an upstream component with our adapter, **not** a reproduction of
Calcite, OpenIVM, or a complete paper benchmark.

## Next strong-baseline gate

Before naming a method, compare with Apache Calcite's materialized-view rewriting
capabilities, including residual predicates, aggregate rollup and UNION
compensation. SQLGlot `optimize` does not provide that full baseline.

- https://github.com/tobymao/sqlglot
- https://calcite.apache.org/docs/materialized_views.html
- https://github.com/ila/openivm
- https://www.sqlite.org/eqp.html

New dataset candidate BIRD-Critic-SQLite provides initial erroneous SQL, but its
full solutions/tests require an official request. Do not claim complete scoring
from the public issue JSON alone. Its evaluator writes and cleans databases;
isolated copies and code review are mandatory before execution.

- https://huggingface.co/datasets/birdsql/bird-critic-1.0-sqlite/tree/af2b1c3e2f8b3480e5569212198761c770df06d0
