# Field-definition interface diagnostic: development protocol

Prepared 2026-10-13; documented 2026-10-14 Hong Kong time. **Not authorized or
executed.** This is an outcome-informed interface repair diagnostic, not a
preregistration of the substantive cache-intent hypothesis or a new method.

## Decision addressed

The first real 24-call pilot exposed an implementation omission: descriptions
were supplied to the guided JSON schema but absent from logged text prompts.
The current candidate appends precisely those existing descriptions to the
system message. It is not yet established that this improves field placement,
retains task meaning, or affects any later program. It cannot establish a
contribution over strong checking plus on-demand recomputation.

This diagnostic asks whether the candidate is suitable for further measurement
development on the already-exposed tasks. It does not select a paper title or
algorithm. Adding definitions also changes prompt length and instructional
emphasis; even an improvement would not isolate a pure semantic-visibility
mechanism. A length-matched ablation is not part of this small repair check.

## Frozen prospective inputs

- All six existing constructed histories, on the same two target tasks and one
  database. No history was included/excluded based on its model response.
- Use each history's common available/cheap envelope: materialized=true,
  read_cost=1, SQL cost=10, budget=100. These remain textual declarations.
- Two newly called arms per history: legacy and visible_definitions. Total 12
  calls, with no repeats, replacements or retries. Old responses are not control
  observations for the new comparison.
- Same history means identical request/evidence/schema/history, response schema,
  model, paired seed, temperature=0, top_p=1, max_tokens=768 and disabled tools.
  The sole request-tree difference is messages[0].content: the fixed suffix.
- Schedule seed 2026101302; history blocks hash-ordered. Three blocks present
  legacy first, three candidate first. Both arms have one call; seeds and order
  are frozen before the prospective batch, with no search over outcomes.
- Same seed and temperature zero do not guarantee hardware-level determinism.
  Six histories sharing two targets are not six independent research samples.
- No SQL execution, cache result access, tools, weight training, questionnaire
  answers, candidate contracts or semantic labels enter model requests.

The preparation command is strictly offline and has no --execute option:

```text
python -B -m scripts.prepare_contract_interface_diagnostic --output runtime/runs/NEW_PRIVATE_PREPARATION/plan.json
```

The existing private preparation is
`runtime/runs/contract-interface-diagnostic-preparation-20261013-001/plan.json`,
61286 bytes, SHA-256
`f10fed100719736eec657a42f212c207e7394c6014de652619db8560a06d2f94`.
Do not overwrite it. It pins all request hashes and preparation source hashes.
The public run record contains metadata only, not the adapted raw task packet.

## Before any paid execution

Reconfirm current server, balance/price and specific batch approval. The user
has been asked; no reply is assumed. This protocol itself grants no authority.
Inspect current host identity, model files/packages and idle GPU/port; preserve
existing services rather than terminating them. No installation, model download,
new server, paid API or alternate credentials are authorized here.

The proposed supervisor limit is 600 seconds including preflight, model startup
and inference; cleanup/archiving/transfer and platform billing are separate.
At the user's previously reported 2 yuan/hour, the supervised window would be
at most approximately 0.34 yuan, not a total invoice or confirmed current rate.
This limit is a proposal, not a deployed process. A separate tested execution
adapter/supervisor still needs to be connected to this new fixed plan; the old
24-call runner rejects it and must not be monkeypatched to accept it.

Only processes started for this batch may be cleaned up. Cloud-instance stop
is separate. Run directories must be new and exclusive; retain all partial
artifacts. Transport/model/unsupported-format failures stop remaining requests;
completed but structurally invalid responses remain recorded and do not cause
replacement. No stopping or extension based on semantic outcomes.

## Measurement and review, defined before new responses

Automatically record only dispatch/completion/coverage, strict eight-string
format validity, transport/parse failures, reported tokens, elapsed time and
source/request/response hashes. Preserve every scheduled request in accounting.
No automatic semantic-error rate, type-C label or treatment significance test.

For semantic interpretation, first obtain the existing independent reviews of
the two current requests, separately from history/candidate answers and model
outputs. Then review the six historical task/cache relations. A sample being
structurally valid or matching a public SQL result does not approve its meaning.

Later output reviewers should receive the current request, evidence, schema,
the same eight field definitions and a shuffled output ID. Hide arm labels,
prompt variant, paired output and prior assistant comments. Preserve duplicate
output mappings privately; do not count duplicates as independent targets.
Two reviewers work independently before disagreements are reconciled. If a
reviewer has already seen outputs/arm information, record that exposure instead
of calling the review blind. Reviewers' identities/answers stay private.

For each field record one of: supported and appropriately placed; supported
content in the wrong field; omitted/incomplete requirement; contradicted or
unsupported requirement; equivalent wording; unresolved due to task ambiguity;
or cannot judge. These categories are review guidance, not generated gold.
Also assess whether the complete contract retains the current request's key
requirements, because a differently placed value may still occur elsewhere.
Retain reasoning and disagreements, not just a majority vote.

Report raw matched changes and unresolved cases, separated from structural
coverage. No confidence interval or generalization claim from two targets. No
reviewer input means semantic results remain UNREVIEWED, never zero errors.
Without a later genuine program event, type C stays unobserved even if a
contract is judged wrong.

## Development decisions

1. Static differences/pairing/hash checks fail: do not launch; repair and version
   before execution. Preserve superseded preparations.
2. Execution fails/incomplete: preserve and back up; do not silently rerun or
   replace calls. Report the fixed planned denominator and unresolved state.
3. No independent semantic review: only technical collection conclusions; do
   not announce that v2 fixed task interpretation.
4. A newly confirmed key-requirement contradiction/omission in the candidate
   blocks automatic adoption as the default measurement interface. Investigate
   without dropping the history or searching seeds for a favorable outcome.
5. If review supports better field placement with no detected new key damage,
   record a limited development decision, not efficacy confirmation. Actual
   tool-enabled cache contrasts and strong verifier/recompute baselines remain
   separate, newly frozen experiments with their own authorization.

This check cannot settle the long-term goal. Its value is avoiding false
mechanism claims from a defective measurement interface, not adding sample
counts or relabeling an ordinary prompt fix as a research method.

## Execution-readiness update: 2026-10-14

The earlier preparation status above is historical. A separate client,
`runtime.contract_interface_diagnostic`, and Linux supervisor,
`scripts.run_contract_interface_server`, now accept only the frozen 12-call
plan. Neither changes the old scientific runner or preparation source files.
Default invocation remains offline. Execution requires explicit acknowledgement,
the verified private bundle, the expected native-model manifest and a fresh
output directory. Cleanup covers owned processes, not cloud-instance billing.

108 offline software fixtures passed, including 18 new client/supervisor/bundle
checks. Process, network and model behavior in those fixtures are mocked; these
are not model experiments or proof of server execution. The private 16-member
bundle is 54,120 bytes, SHA-256
`1ce205c7a5213f424e5b97bfd5fa14b3e77f7a40645ce24d3291b9c0fe1ef370`.
It includes the fixed requests and historical model-content reference, but no
participant answers, credentials or weights. It must not enter public GitHub.

The user has now authorized this batch only and confirmed sufficient balance
at 2 yuan/hour. A live SSH check found the existing GPU and port idle. This
authorizes at most 12 calls within the 600-second supervised window, not retries,
follow-up batches or adoption of the candidate interface. Human task and output
review remain outstanding. Actual outcome and backup verification belong in a
separate run record; preparation alone is not execution.

### Subsequent execution

The separately approved batch completed on 2026-10-14. See
`configs/run_records/contract_interface_diagnostic_20261014_001.json` for actual
coverage, resources and backup hashes. All 12 calls completed with valid format;
semantic correctness remains unreviewed. This does not authorize another batch.
