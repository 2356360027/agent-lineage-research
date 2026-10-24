# Correct-route cap admission and source component snapshot

2026-10-24. Local preparation only; the user says the server is stopped.
No SSH, inference, paid API, model download or server startup was performed.

## Evidence added

The sealed recovery-v2 software audit contains four scripted routes: two
previously adjudicated development targets under each of two declared prices.
This check verifies that archive's hashes and reuses its complete SQL results;
it does not rerun the research database, reinterpret the task or treat the
scripted contract/query/answer as model behavior.

| Development target | Contract tokens | SQL action tokens | Final answer tokens | Largest complete observation count |
|---|---:|---:|---:|---:|
| 200 | 96 | 56 | 33 | 87 |
| 234, scope-informed version | 124 | 78 | 19 | 80 |

Counts use the archived tokenizer and exact serialized messages. The three
scripted reply types are identical across prices and pass both raw-output cap
512 and assistant-frame cap528. Final answers preserve every typed SQL row.
Observation counts include the conservative empty-system/user framing plus
assistant prefix used by the exposure policy. They remain below512 for a
superset of possible result metadata: attempts1..21 and remaining balances
0..200, with SQL charge10. The 8442 total metadata combinations are software
checks, not independent tasks or model trials. No result rows were suppressed.

This establishes an admissible representation of the known correct route for
each of these two development targets. Together with the reserved SQL attempt
and conditional context envelope, it removes identified interface obstacles.
It does NOT guarantee that a model finds that route, that arbitrary answers fit,
that SQL always completes on another machine, or that errors never terminate a
trajectory. In particular, invalid/over-cap outputs remain recorded failures.

Six historical-cache contexts still represent only two current target tasks.
The complete fresh-recompute witness for a target is independent of which
historical cache is shown; no reference witness is added to model prompts.
The separate capacity audit covers all twelve initial history/price previews.

## Packaging

The allow-listed source component snapshot now contains19 files, including
recovery preview, complete-or-withheld observation policy and capacity checker.
Five packaging tests pass, including copied-module imports from an isolated
directory and one synthetic read-only SQLite query in that directory. Four
cap-check unit tests pass. Those tests do not query the research database.

Snapshot manifest SHA256:
`f81ab7372553e44f58e34eb27aea57e1e16e9e9bb5b835085455a4c8c36438b5`.

The snapshot has no weights, datasets, review forms, raw answers or credentials.
It is a component bundle, NOT a complete executable or approved experiment.
The allow-list verifier retains explicit support for the older15/16-file
snapshots; old artifacts are not overwritten.

## Next gate, not a launch instruction

Local wiring still needs a bounded batch entry point joining frozen inputs,
model/tokenizer identity, the capacity checker, adapter, tool session and
journals. It must not supply reference contracts, SQL or answers to the model.
After local dry-run validation, live serving configuration and tokenizer parity
need new resource authorization. Current provider parity and hardware capacity
remain unverified. No permission to infer follows from this document.

Scientific status is unchanged: no controlled price-to-contract effect has
been estimated, and no incremental benefit over strong baselines is established.
Stop repeating equivalent cap checks; finish necessary wiring, then report the
remaining authorization gate rather than expanding software counts.
