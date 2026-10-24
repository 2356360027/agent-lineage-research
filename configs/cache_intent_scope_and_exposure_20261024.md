# Scope evidence and full-result exposure: development decision

2026-10-24. Local preparation only, NOT a frozen/authorized batch. No historical
prompts, labels, reference SQL or original database records are changed.

## Scope policy for the next development input version

Use an explicit, shared, snapshot-bound scope fact for task 234, rather than
silently assuming atom-ID prefixes enforce relational constraints. Proposed
model-visible text, appended to the original evidence in every 234 condition:

> Verified property of this fixed database snapshot: every non-null bond_id in
> a connected record with atom_id='TR009_12' or atom_id2='TR009_12' resolves to a
> bond whose molecule_id is 'TR009'. This property is snapshot-specific; it is
> not inferred from identifier spelling and is not a rule for other databases.

This supplies applicability information, not the bond count, bond-ID list, gold
SQL, correct contract or an instruction to reuse. It is intentionally additional
information relative to old experiments. Therefore create a new input version,
apply it identically to all compared methods/price arms, and do not merge with
old outputs or describe this as the same prompt. It narrows the ambiguity in
the experimental setting; it does not certify human understanding or prove a
method advantage. Later external-validity work may study costly scope discovery
separately, not quietly change this controlled setting mid-run.

Evidence: existing private connection-check report SHA256
`d5f0314839e435f760c454bb604eb24cd5cfd8e3bfbf1f9a754dcb2a049be86d`,
its complete zero-mismatch LEFT JOIN query, and fixed database SHA256
`35ef27ae6bdfda530e125ed369666ac0abb8bc8c0bcc0ad09407f547ecb61a93`.
The database hash was rechecked locally on this date; the query was not rerun.
Any version mismatch invalidates this receipt. The statement reveals a scope
property but does not assert a nonempty result or any particular cardinality.

Do not upload the full private report to agent context: it also contains answers.
Certificate acquisition, validation and maintenance are setup costs, not free
algorithmic reasoning. Preserve the old recorded query timings; they are not a
measurement of present full-system cost. Before any efficiency claim define and
report full setup cost and amortization uniformly, including hash checking.

### Why the explicit property matters

New in-memory fixtures use the existing DDL with foreign keys enabled. Two
worlds retain the same incident count and ID list but change a referenced bond's
molecule from TR009 to another existing molecule. Both pass FK/integrity checks;
the scoped count changes. This disproves general equivalence from DDL alone,
NOT the correctness of the real clean snapshot or any physical chemistry rule.

IDs permit a supplemental bond-membership query; the scalar alone generally
does not. Zero incidence is an exception: a scoped subset must also be empty.
Tests are finite synthetic software witnesses, not independent research tasks.

## Full-result exposure policy

Keep complete typed cache rows and complete successful SQL rows, with the same
serialization and backend caps in both arms. No price-dependent formatting,
head/tail sampling, silent truncation, semantic summary or removal of old turns.
The existing backend keeps incomplete/oversized SQL results private and exposes
an incomplete/error status rather than pretending partial rows are complete.

Before each model request, count its ENTIRE serialized chat with the verified
tokenizer/template and reserve the full chosen completion cap. If it exceeds
capacity, retain the full observations/pending request, record the context
failure, and stop that trajectory without automatic repair/retry. Earlier
contract judgments remain usable; downstream unobserved semantics stay unknown.
This is common failure handling, not proof that a correct route always fits.

The runner test now checks that a long observation and the earlier contract
remain byte-for-byte intact when the following generation rejects; it neither
executes a second tool nor invents a completed summary. A real batch supervisor
must still catch and summarize that preserved failure separately.

## What local tokenization established

Archived Qwen tokenizer/config/vocab/merges files matched their backup manifest.
Using tokenizers 0.22.1 and Jinja2 3.1.6, archived-template rendering was compared
with an independent plain-role framing construction for every measured request.
No model weights or serving process were used. The dependency was installed in
an ignored project-local directory, not system Python.

For the PRE-certificate twelve previews: initial prompts are 781–816 tokens.
A scripted placeholder contract followed by twenty complete repeated cache
reads gives a maximum terminal prompt of 3518 tokens. With a hypothetical 512
token reservation that is 4030, leaving 66 under the historical 4096 window.
This is not a real agent trajectory, a worst-case bound or a current-server
compatibility test. Actual contracts/actions/SQL results can be longer. New
certificate-bearing inputs must be counted again after versioning.

## Admission still required

Do not silently choose a larger context or shorter output cap based on outcomes.
Before running, select the intended model/window/output cap on engineering
grounds, bind real serving/template settings, and check representative correct
routes plus bounded-output stress cases. Any formal claim of feasibility
throughout a trajectory needs explicit context/horizon reasoning, not merely an
affordable first SQL query. This remains an open gate; the strict research goal
is not redefined as an initial-prompt-fit test.

Decision: prepare a NEW, identically scope-informed input version next; keep old
archives untouched. Keep full-result exposure/failure policy, but do not approve
a live run from these token counts. Server remains stopped; local-only work.

## Subsequent local preparation: scope-informed v1

The new version is now prepared by `scripts/prepare_scope_staged_inputs.py`.
It verifies the pinned scope report and database hash, adds only the statement
above to task 234 evidence, and leaves task 200 unchanged. Within each history,
the paired initial prompts still differ only in cache read price. Six unit
tests cover this transformation and rejection of invalid receipts. This is
input preparation, not human approval or permission to run inference.

Offline measurement of the new twelve previews gives initial lengths of
781–894 tokens. The largest scripted terminal prompt is 3587 tokens; adding
a hypothetical 512-token completion reservation gives 4099, exceeding the
historical 4096-token window by 3. This is a specific engineering failure of
that proposed combination, not an observed model failure or an upper bound.
Do not discard the observation, truncate context, or quietly lower the output
reservation. A future configuration must be explicitly selected and checked
before a separately approved model batch. Actual SQL outputs may be longer.

Both private artifact manifests and their member hashes were verified locally.
Historical inputs and results remain unchanged and separate. The server is
stopped; no SSH, model inference, or paid API was used for this preparation.
