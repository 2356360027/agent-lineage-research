# Literature-to-identification audit: cost, memory and request fidelity

2026-10-24. Primary-source reading and design audit, NOT a new experiment,
systematic-review completeness claim, preregistration or run authorization.
Continues the existing question; no new algorithm or topic is selected.

## Primary sources and overlap

| Source/status | Checked protocol and outcomes | Implication |
|---|---|---|
| [CostBench, ACL 2026](https://aclanthology.org/2026.acl-long.584.pdf), setup and Appendix B.3–B.4 | Travel-tool planning under cost changes/blocking; cost gap, trajectory match, final-candidate correctness and invalid calls. Cost changes preserve solvability. | Cost-aware planning and reachability are not new. Our candidate distinction is pre-result requirement contradiction under identical semantic inputs, not a different tool path. |
| [Experience-Following, ACL 2026](https://aclanthology.org/2026.acl-long.27.pdf), Sections 2–4, especially 4.3 | Memory addition/deletion and downstream behavior in regression, medical, driving and IoT agents; discusses task-context mismatch, illustrating evaluator limitations using record quality. | Misleading retrieved experiences are not new. Accepted records need not be exactly correct histories under our assumptions; trusted histories alone do not establish novelty either. |
| [Useful Memories Become Faulty, arXiv v2](https://arxiv.org/html/2605.12978v2), setup/failure modes and Appendix D.5 | Consolidation/replay; D.5 shows a source-family-appropriate strategy replacing a different task's object-selection rule in generated code. | Even semantic rule replacement has a close precedent. The candidate distinction is an isolated convenience intervention with unchanged history and separate contract/subsequent-action observations. |

Named sections were inspected, not independently reproduced. The first two
have ACL proceedings records; the third is treated as a preprint. Limited
keyword searches did not establish another directly matching primary study;
search silence is not novelty evidence. Author results are not our results.

## Metric-transfer hazard

CostBench Appendix B.3.2 conditions cost/path metrics on reaching the goal,
disregards invalid calls in those calculations, and also excludes non-goal
trajectories from its stated task-completion denominator. This answers a
conditional planning question. We must not silently copy those denominators:
completion/invalidity may depend on our price treatment, making completed-only
comparisons select different subsets in the two arms.

For our diagnostic, keep the entire frozen allocation manifest:

- Report attempted, completed, failed, invalid, abstained, context-rejected and
  unresolved counts per arm, including wholly missing pairs.
- Judge a saved pre-result contract even if execution later fails. Later success
  is not eligibility for that earlier observable endpoint.
- No interpretable contract means unknown, not a match or contradiction. Report
  missing-label bounds over planned pairs and observed counts. Bounds are not
  confidence intervals or evidence of independent tasks.
- Include realized expenditure for failed attempts. Conditional cost among
  successes can be supplementary with an explicit denominator.
- Reuse `cache_intent_judgment.paired_bounds` with frozen pair IDs and unknowns.
  The helper does not validate labels or establish causal identification.

## Estimand and limits

Keep proposed read prices 1/8, SQL price 10, budget 200 and histories fixed.
These are declared units, not measured savings. Let Y_i(p) denote adjudicated
pre-result whole-contract contradiction for pair i at price p, when observable.
Report mean_i[Y_i(8)-Y_i(1)] descriptively. Negative values are consistent with
more contradictions at the cheaper price; retain either sign. History-directed
contradiction and a later aligned program require separate evidence.

Six histories are nested in TWO exposed targets. Equal pair weights describe
this diagnostic, not six independent task effects. Twelve trajectories do not
establish statistical power or population significance. Initial semantic inputs
are matched, but later tool results can differ: downstream contrasts concern
the total price effect, not a direct effect holding all later information fixed.

Price is visible before the contract. This instrumented workflow is NOT the
contract-first baseline that withholds cost/cache information until commitment.
A visible contract does not reveal private reasoning. Sequencing alone does not
establish causal mediation through the contract; both outputs may respond to
the same prompt or shared model tendencies.

## Discriminating observations

| Outcome | Interpretation |
|---|---|
| Faithful contract, cheaper valid route | Legitimate planning, not C. |
| Faithful contract, incompatible reuse or wrong SQL | A/B, not requirement reinterpretation. |
| Wrong contract without linked later program | Contradiction only; C unestablished. |
| Wrong contract toward history, linked later program agrees | Individual C candidate; attributing it to price still needs a controlled contrast. |
| Infeasible correct route, unequal evidence or ambiguous target | Identification failure or D/E investigation. Preserve records. |
| Generic memory errors without a price contrast | Convenience-specific effect not established; do not retune seeds/prices to force one. |

h200a is a compatible-history control: valid route changes are not drift.
h200b permits filtering/projection; h200c/h234c need information absent from
the cache. h234a/b need the same explicit scope policy in all arms. All are
constructed histories, not natural usage records.

Ordinary no-contract execution, no-cache recomputation, cost/cache-hidden
contract-first, fresh-context verification, structured/classical matching and
applicable counterexample checks remain later comparisons. They are not silently
added to this twelve-trajectory proposal. No-cache changes availability and is
not the clean price-only contrast.

## Decision

Continue small diagnostic preparation, not a named-method claim or larger sweep.
Before collection freeze planned denominators, scoring of pre-result contracts
despite later failure, and conditional-versus-total costs. Existing bounds code
supports this policy but cannot supply semantic labels.

Remaining gates: actual tokenizer/context feasibility, common result policy,
scope evidence, exact inputs/order/seeds/model, time/call limits, current
resources and per-batch authorization. This audit ran no model, SQL or cloud
job and created no human labels. Software tests remain software evidence.

Only a controlled effect plus a measurable residual quality–total-cost gap over
strong checking and on-demand recomputation justifies method development.
Prior memory interference or ordinary planning alone does not justify renaming.
Confirmation needs unused tasks and appropriate model coverage, not more draws
from these two development examples.
