# Pinned Calcite baseline contract audit

This is a CPU-only audit of upstream Apache Calcite **1.40.0**, not an LLM
experiment, new algorithm, natural workload, or full cost-based MV selection
benchmark. The eight assistant-authored HR-style contracts have three fixed
fixtures each. Empty and NULL fixtures are boundary controls, not independent
real-world tasks. All historical experiments are preserved separately.

## What is executed

- The Java `RelBuilder` constructs Q, V and the MV scan in a common cluster.
- V is actually executed and its rows become a `ScannableTable`.
- Default `hep` mode uses upstream AVG-only reduction and six materialized-view
  HEP rules. Optional `volcano_base` / `volcano_mv` modes rebuild Q/V/MV in a
  fresh cluster, reset metadata, register upstream default rules, and invoke
  `Programs.standard()` with EnumerableConvention. The cost arms differ only
  in the registered materialization list (zero/one); both construct the real MV.
  No hand-written rewritten SQL is substituted for Calcite.
- Original Q, V and HEP output use `RelRunners.run` JDBC preparation/execution.
  The cost arms compile the actual selected EnumerableRel directly with
  upstream `EnumerableInterpretable.toBindable`, then bind the registered
  schema and upstream stashed parameters. Re-submitting a physical plan to
  JDBC caused a second-planner integration failure in development; those
  failed records remain, rather than being reported as upstream rule failures.
  Timings include preparation or compilation and are not operator benchmarks.
- Independent, fixed expected rows check the materialized view, original query,
  and rewritten query separately. A local in-memory SQLite preflight also checks
  all 48 expected view/query outputs. This is a numeric-valued bag oracle, not a
  cross-dialect type proof. Java value types and SQL row types are recorded too.
- Base-table fallback is legal. Failure to select an MV with these configured
  HEP rules does **not** prove an information-theoretic impossibility, or that
  Calcite's complete optimizer cannot find another plan.

COUNT DISTINCT and empty global aggregates must not be evaluated solely by
comparing two outputs from Calcite's subset `Interpreter`. The implementation
uses the full JDBC preparation path and independent oracles instead.

## Reproduce locally

Use a suitable installed JDK with `java` and `javac`. This run used a locally
available JBR 25.0.3 and Java `--release 17` compilation; it is an adaptation,
not a claim of official upstream JDK25 validation. No global environment or
Maven settings are changed.

1. Run `python -m unittest tests.test_calcite_contract_audit -v`.
2. Run `python scripts/fetch_maven_repro.py --output runtime/runs/dependencies/NEW-MAVEN-CACHE`.
   The task-local Maven 3.9.9 archive is pinned by SHA-512 and SHA-256 is recorded.
3. With that Maven executable and the chosen JDK, resolve the pinned POM using
   `dependency:build-classpath`. Pass the provided `isolated-settings.xml` as both
   `-s` and `-gs`, a task-local `-Dmaven.repo.local=...`, and a fresh absolute
   `-Dmdep.outputFile=...`. The runner hashes every resolved JAR. Only dependency
   resolution requires network access.
4. Run one command (replace the paths with the actual JDK and classpath file):

```text
python -u -m runtime.calcite_contract_audit --output runtime/runs/NEW-UNUSED-RUN --java-home PATH-TO-JDK --classpath PATH-TO-CLASSPATH-TXT
```

For the fixed 48-combination cost-control batch, append:

```text
--planner-modes volcano_base volcano_mv
```

This schedules both cost modes for each of the same 24 case/state combinations,
alternating which mode runs first. The exact schedule is frozen in `design.json`.
It is not a timing experiment, and order balancing does not make it one.

The output must be new and inside `runtime/runs`. Existing outputs are never
overwritten. Source, dependency hashes and design are saved before compilation
and query execution. Each case/state has a separate JVM, 768 MiB heap limit and
40-second process deadline (60 seconds for cost modes); timeouts and errors remain in the denominator. Do
not edit running source or tests during the batch. No hidden retries, paid APIs,
remote database mutations or cloud jobs are used.

## Recorded audit rounds

The first round `calcite-mv-repro-20261012-001` is preserved, including a harness
C4 output-column ordering error and an asymmetric aggregate-normalization setup.
The corrected round `calcite-mv-repro-20261012-002` uses the same data and expected
answers. It passed all 24 independent value, type and named-column checks: nine
MV-only combinations (C1/C5/C6), three MV-plus-base combinations (C8), and twelve
legal base fallbacks (C2/C3/C4/C7). These are baseline integration results, not
evidence of a novel method's advantage. Sanitized records retain both rounds.

The v2 runner requires `baseRows` even for empty fixtures. The Java v2 adapter
projects C4 columns in declared SELECT order, reduces only AVG before MV matching,
unwraps HEP vertices for logging, and restores output aliases only after checking
SQL row types. Pre-alias plans and schemas are retained. No outputs are repaired
by inspecting or changing returned row values.

Cost modes additionally record the selected Volcano memo-root estimated cost
and the final standard-program physical-tree cost separately: the standard
pipeline includes post-Volcano HEP transformations. The raw selected physical
tree is executed unchanged; alias restoration is an output-schema adapter
only, with SQL type checks before it. The verifier checks both arm identities,
registered MV counts, finite nonnegative estimated costs, raw results, types,
column names and scan counters. A present MV candidate is not the same as a
selected MV plan. The cost batch's completed results are recorded separately;
the preceding 24-case HEP numbers must not be relabeled as cost-planner results.

## Scope limitations

- INTEGER salaries/commissions only; C6's mean happens to be exactly 180.
  Non-integral averages, DECIMAL precision and overflow are untested.
- No joins, constraints beyond declared nullability, changing snapshots,
  unreliable provenance, concurrent updates, or natural historical cache data.
- No MV build amortization, fair latency benchmark, or new-method comparison.
- The default Calcite 1.40 VolcanoCost ordering primarily compares estimated
  row costs. Recorded CPU/IO values are not a calibrated latency/currency
  objective. Both cost arms construct V for oracle checks; costs assume a
  previously available materialization and do not include its construction.
- Intermediate HEP rule-event scan lists in the initial harness may omit scans
  hidden behind `HepRelVertex`. Use the final plan and actual scan counters;
  an empty intermediate list is not proof that a candidate is absent.
- Tiny fixture agreement is not universal SQL equivalence. A strong baseline
  that correctly recomputes is not an experimental failure.

## Primary references

- [Calcite materialized-view documentation](https://calcite.apache.org/docs/materialized_views.html)
- [RelRunners API](https://calcite.apache.org/javadocAggregate/org/apache/calcite/tools/RelRunners.html)
- [Fixed 1.40.0 source archive](https://repo.maven.apache.org/maven2/org/apache/calcite/calcite-core/1.40.0/calcite-core-1.40.0-sources.jar)
  SHA-256 `0f63c2a28b43ab4d1888a73633ccf1c76513712d27f61dd489431fd2c71b6763`.

Raw logs and JARs remain under ignored `runtime/runs`; do not upload them, private
paper artifacts, databases or authentication material to the public repository.
