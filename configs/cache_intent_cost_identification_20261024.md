# Cost intervention: identification and baseline decision audit

2026-10-24. Analytical audit of existing code, NOT model results, measured
performance or an approved batch. The current research question remains whether
reuse convenience changes the interpretation of an explicit request.

## Verified implementation facts

The current ToolSession charges `read_cache` 1 or 8 declared units and any admitted
`run_sql` 10. Both arms use budget 200 and a 20-attempt horizon. No query-size,
row-count, IO, CPU or partial-reuse discount is applied to `run_sql`.

Given a valid one-query solution and leaving model/verification overhead aside:

| Route | Read cost 1 | Read cost 8 |
|---|---:|---:|
| Cache-only answer or in-response transformation | 1 | 8 |
| One correct fresh SQL query | 10 | 10 |
| Cache read then one supplemental SQL query | 11 | 18 |

These are analytic totals from code constants, not observations from agents.
If a cache has already been read, its cost is sunk: subsequent SQL still costs
10 in both arms. Do not penalize a necessary recovery as irrational merely
because the earlier read was unnecessary in hindsight. An agent uncertain of
applicability may rationally inspect the cache; this table assumes known valid
routes and does not establish an optimal policy under uncertainty.

Memory transformations are not charged by ToolSession, but their model tokens,
generation latency and errors must be counted in total-system comparisons.
Likewise a one-SQL route is only a conditional reference route: obtaining that
correct SQL from language is not free or guaranteed. No baseline receives a
human gold contract or reference SQL during evaluation.

## Consequences for the six exposed development histories

- h200a: compatible direct result. A change in choice between correct cache use
  and recomputation is not a semantic error.
- h200b: filter label and project IDs; retaining the extra label or failing to
  filter must be judged against the whole request, not assumed from cache use.
- h200c: candidate IDs plus label lookup is possible, but a read-plus-query route
  is not cheaper in the present declared-unit metric than one correct fresh SQL.
- h234a/b: direct/count-only routes require shared, explicit scope evidence.
  Without it, verification requires base information; mere string spelling is
  not a schema guarantee. Evidence acquisition cannot be hidden as free work.
- h234c: total molecule count does not generally answer local incidence. A cheap
  but wrong direct answer is not an eligible cost-saving alternative.

Thus the current price change modifies magnitudes but does NOT reverse the
ordering of these already-known valid routes: cache-only remains below 10;
cache-plus-one-SQL remains above 10. This does not logically invalidate a
price-sensitivity diagnostic, but a null response would not rule out threshold
effects or other workloads. Do not tune costs after seeing outputs to force a
preferred behavioral change.

## Decision: separate three questions

1. **Mechanism diagnostic:** hold information and availability fixed, change
   declared read price, measure pre-result observable contract fidelity and
   genuinely later behavior separately. The planned small batch is only this
   instrumented diagnostic, not evidence that a repair method is efficient.
2. **Strong-baseline correctness:** evaluate request interpretation and whether
   classical applicability checks plus on-demand computation preserve it. Route
   labels alone cannot stand in for observed correctness or C-chain evidence.
3. **System efficiency:** later, use measured query/runtime/IO and full model,
   verification and maintenance costs on an appropriate workload. Preserve this
   as a separate experiment, not a replacement cost model silently introduced
   into question 1. No new efficiency batch is authorized here.

Do not change the 1/8/10 schedule merely to make supplementation win. Do not
report the algebraic 11/18 values as GPU usage, money, observed latency or an
empirical negative result for all partial-reuse systems.

## Prior-art boundary checked this turn

Apache Calcite's official materialized-view documentation describes residual
filtering, projection/join rewrites, aggregation rollup and partial rewrites,
using declared table constraints. These operations remain strong baselines,
not new algorithms supplied by our pipeline. The documentation does not itself
prove our current natural-language contracts are correctly inferred or that a
particular rewrite is faster in this environment.

Source: https://calcite.apache.org/docs/materialized_views.html
(read 2026-10-24; this is a documentation check, not a new Calcite reproduction).

## Execution readiness remains separate

Context feasibility is not implied by the 200-unit tool budget. Before freezing
the batch, settle a common result/context policy, actual tokenizer/service
compatibility, scope evidence, exact inputs, call/time bounds and resources.
Overflow must remain recorded, not mislabeled as drift or removed from an arm.

For strict claims of correct-route feasibility, do not use initial affordability
as proof for arbitrary later states. If a bounded rollout is exhausted, that is
a process/coverage failure; a clean mechanism claim needs evidence from states
where a correct route is still available. Do not achieve this by post-hoc
deletion of unfavorable trajectories.

Current recommendation: continue the existing small diagnostic preparation,
but do not expand the sample or introduce a named repair algorithm. The observed
human review and software tests have not established a new method advantage.
