package research;

import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.JsonGenerator;
import org.apache.calcite.DataContext;
import org.apache.calcite.DataContexts;
import org.apache.calcite.jdbc.CalciteConnection;
import org.apache.calcite.linq4j.Enumerable;
import org.apache.calcite.linq4j.Linq4j;
import org.apache.calcite.plan.RelOptListener;
import org.apache.calcite.plan.RelOptMaterialization;
import org.apache.calcite.plan.RelOptRule;
import org.apache.calcite.plan.RelOptUtil;
import org.apache.calcite.plan.hep.HepMatchOrder;
import org.apache.calcite.plan.hep.HepPlanner;
import org.apache.calcite.plan.hep.HepProgramBuilder;
import org.apache.calcite.plan.hep.HepRelVertex;
import org.apache.calcite.rel.RelNode;
import org.apache.calcite.rel.core.TableScan;
import org.apache.calcite.rel.RelVisitor;
import org.apache.calcite.rel.metadata.ChainedRelMetadataProvider;
import org.apache.calcite.rel.metadata.DefaultRelMetadataProvider;
import org.apache.calcite.rel.metadata.RelMetadataProvider;
import org.apache.calcite.rel.rules.AggregateReduceFunctionsRule;
import org.apache.calcite.rel.rules.materialize.MaterializedViewRules;
import org.apache.calcite.rel.type.RelDataType;
import org.apache.calcite.rel.type.RelDataTypeFactory;
import org.apache.calcite.schema.ScannableTable;
import org.apache.calcite.schema.SchemaPlus;
import org.apache.calcite.schema.Statistic;
import org.apache.calcite.schema.Statistics;
import org.apache.calcite.schema.impl.AbstractTable;
import org.apache.calcite.sql.fun.SqlStdOperatorTable;
import org.apache.calcite.sql.SqlKind;
import org.apache.calcite.sql.type.SqlTypeName;
import org.apache.calcite.tools.Frameworks;
import org.apache.calcite.tools.RelBuilder;
import org.apache.calcite.tools.RelRunners;

import java.io.OutputStream;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Small, deterministic, read-only materialized-view rule audit.
 * RelBuilder programs, not the explanatory SQL strings, define the executed plans.
 * This harness tests rule coverage; it is not the full cost-based Calcite optimizer.
 */
public final class CalciteContractAudit {
  private static final RelOptRule AVG_ONLY = AggregateReduceFunctionsRule.Config.DEFAULT
      .withFunctionsToReduce(Set.of(SqlKind.AVG)).toRule();
  private static final List<RelOptRule> MV_RULES = Arrays.asList(
      MaterializedViewRules.PROJECT_AGGREGATE,
      MaterializedViewRules.AGGREGATE,
      MaterializedViewRules.PROJECT_FILTER,
      MaterializedViewRules.FILTER,
      MaterializedViewRules.PROJECT_JOIN,
      MaterializedViewRules.JOIN);

  private record Contract(String viewSql, String querySql, String boundary) { }

  private static Contract contract(String id) {
    String sufficient = "SELECT deptno,COUNT(*) n,COUNT(salary) c,SUM(salary) s "
        + "FROM emps GROUP BY deptno";
    String sum = "SELECT deptno,SUM(salary) s FROM emps GROUP BY deptno";
    return switch (id) {
      case "C1" -> new Contract("SELECT deptno,salary FROM emps",
          "SELECT salary FROM emps WHERE deptno=20", "VIEW_ONLY_SUFFICIENT_BAG_SEMANTICS");
      case "C2" -> new Contract("SELECT deptno,COUNT(salary) n FROM emps GROUP BY deptno",
          "SELECT deptno,COUNT(*) n FROM emps GROUP BY deptno", "VIEW_ONLY_INSUFFICIENT_NULLABLE_COUNT");
      case "C3" -> new Contract(sum,
          "SELECT deptno,SUM(commission) s FROM emps GROUP BY deptno", "VIEW_ONLY_INSUFFICIENT_MISSING_COLUMN");
      case "C4" -> new Contract(sum,
          "SELECT deptno,empid,SUM(salary) s FROM emps GROUP BY deptno,empid", "VIEW_ONLY_INSUFFICIENT_GROUP_GRAIN");
      case "C5" -> new Contract(sufficient,
          "SELECT COUNT(*) n,COUNT(salary) c,SUM(salary) s FROM emps", "VIEW_ONLY_SUFFICIENT_ROLLUP_EMPTY_NULL_SENSITIVE");
      case "C6" -> new Contract(sufficient,
          "SELECT AVG(salary) a FROM emps WHERE deptno IN (10,20)", "VIEW_ONLY_SUFFICIENT_SUM_COUNT_NOT_MEAN_OF_MEANS");
      case "C7" -> new Contract("SELECT deptno,COUNT(DISTINCT salary) n FROM emps GROUP BY deptno",
          "SELECT COUNT(DISTINCT salary) n FROM emps", "VIEW_ONLY_INSUFFICIENT_DISTINCT_OVERLAP");
      case "C8" -> new Contract("SELECT empid,deptno,salary FROM emps WHERE deptno=10",
          "SELECT empid,deptno,salary FROM emps WHERE deptno IN (10,20)", "PARTIAL_REUSE_PLUS_DISJOINT_BASE_COMPLEMENT_ALLOWED");
      default -> throw new IllegalArgumentException("Unknown case: " + id);
    };
  }

  private static List<Object[]> fixture(String state) {
    List<Object[]> rows = new ArrayList<>(Arrays.asList(
        new Object[]{1, 10, 100, 10}, new Object[]{2, 10, 200, null},
        new Object[]{3, 10, null, 30}, new Object[]{4, 20, 300, null},
        new Object[]{5, 20, 300, null}, new Object[]{6, 20, 0, 20},
        new Object[]{7, 30, 100, null}, new Object[]{8, 30, null, null},
        new Object[]{9, null, 50, 5}));
    if (state.equals("empty")) return new ArrayList<>();
    if (state.equals("all_salary_null")) {
      return new ArrayList<>(Arrays.asList(
          new Object[]{1, 10, null, 10}, new Object[]{2, 10, null, null},
          new Object[]{3, 20, null, 30}, new Object[]{4, null, null, 5}));
    } else if (!state.equals("base")) {
      throw new IllegalArgumentException("state must be base, empty, or all_salary_null");
    }
    return rows;
  }

  private static final class Rows extends AbstractTable implements ScannableTable {
    final RelDataType rowType;
    final List<Object[]> rows;
    long scans;
    long offeredRows;

    Rows(RelDataType rowType, List<Object[]> rows) {
      this.rowType = rowType;
      this.rows = new ArrayList<>();
      for (Object[] row : rows) this.rows.add(row.clone());
    }

    @Override public RelDataType getRowType(RelDataTypeFactory factory) {
      return factory.copyType(rowType);
    }

    @Override public Enumerable<Object[]> scan(DataContext context) {
      scans++;
      offeredRows += rows.size();
      return Linq4j.asEnumerable(rows);
    }

    @Override public Statistic getStatistic() {
      // Actual fixture cardinality only; no unique-key assumptions.
      return Statistics.of(rows.size(), Collections.emptyList());
    }

    void resetCounters() { scans = 0; offeredRows = 0; }

    Map<String, Object> counters() {
      return map("scanCalls", scans, "rowsOfferedByScan", offeredRows,
          "note", "Rows offered, not measured storage reads or latency cost.");
    }
  }

  private static RelNode buildView(RelBuilder b, String id) {
    b.scan("emps");
    switch (id) {
      case "C1": return b.project(b.field("deptno"), b.field("salary")).build();
      case "C2": return b.aggregate(b.groupKey("deptno"),
          b.count(false, "n", b.field("salary"))).build();
      case "C3": case "C4": return b.aggregate(b.groupKey("deptno"),
          b.sum(false, "s", b.field("salary"))).build();
      case "C5": case "C6": return b.aggregate(b.groupKey("deptno"),
          b.countStar("n"), b.count(false, "c", b.field("salary")),
          b.sum(false, "s", b.field("salary"))).build();
      case "C7": return b.aggregate(b.groupKey("deptno"),
          b.count(true, "n", b.field("salary"))).build();
      case "C8":
        b.filter(b.equals(b.field("deptno"), b.literal(10)));
        return b.project(b.field("empid"), b.field("deptno"), b.field("salary")).build();
      default: throw new IllegalArgumentException(id);
    }
  }

  private static RelNode buildQuery(RelBuilder b, String id) {
    b.scan("emps");
    switch (id) {
      case "C1":
        b.filter(b.equals(b.field("deptno"), b.literal(20)));
        return b.project(b.field("salary")).build();
      case "C2": return b.aggregate(b.groupKey("deptno"), b.countStar("n")).build();
      case "C3": return b.aggregate(b.groupKey("deptno"),
          b.sum(false, "s", b.field("commission"))).build();
      case "C4":
        b.aggregate(b.groupKey("deptno", "empid"), b.sum(false, "s", b.field("salary")));
        // Aggregate group sets follow input ordinals, not SELECT output order.
        return b.project(b.field("deptno"), b.field("empid"), b.field("s")).build();
      case "C5": return b.aggregate(b.groupKey(), b.countStar("n"),
          b.count(false, "c", b.field("salary")), b.sum(false, "s", b.field("salary"))).build();
      case "C6":
        filterDepartments(b);
        return b.aggregate(b.groupKey(), b.avg(false, "a", b.field("salary"))).build();
      case "C7": return b.aggregate(b.groupKey(), b.count(true, "n", b.field("salary"))).build();
      case "C8":
        filterDepartments(b);
        return b.project(b.field("empid"), b.field("deptno"), b.field("salary")).build();
      default: throw new IllegalArgumentException(id);
    }
  }

  private static void filterDepartments(RelBuilder b) {
    b.filter(b.call(SqlStdOperatorTable.OR,
        b.equals(b.field("deptno"), b.literal(10)),
        b.equals(b.field("deptno"), b.literal(20))));
  }

  private static final class Trace implements RelOptListener {
    final List<Object> productions = new ArrayList<>();
    long attempted;
    @Override public void relEquivalenceFound(RelEquivalenceEvent e) { }
    @Override public void relDiscarded(RelDiscardedEvent e) { }
    @Override public void relChosen(RelChosenEvent e) { }
    @Override public void ruleAttempted(RuleAttemptedEvent e) { if (e.isBefore()) attempted++; }
    @Override public void ruleProductionSucceeded(RuleProductionEvent e) {
      if (!e.isBefore() && e.getRel() != null) {
        productions.add(map("rule", e.getRuleCall().getRule().toString(),
            "rel", RelOptUtil.toString(e.getRel()), "scanTables", scans(e.getRel())));
      }
    }
  }

  private static RelNode hep(RelNode rel, List<RelOptRule> rules,
      List<RelOptMaterialization> materializations, Trace trace) {
    HepProgramBuilder program = new HepProgramBuilder()
        .addMatchOrder(HepMatchOrder.TOP_DOWN).addMatchLimit(128);
    program.addRuleCollection(rules);
    HepPlanner planner = new HepPlanner(program.build());
    planner.addListener(trace);
    List<RelMetadataProvider> providers = new ArrayList<>();
    providers.add(DefaultRelMetadataProvider.INSTANCE);
    planner.registerMetadataProviders(providers);
    rel.getCluster().setMetadataProvider(ChainedRelMetadataProvider.of(providers));
    rel.getCluster().invalidateMetadataQuery();
    for (RelOptMaterialization materialization : materializations) {
      planner.addMaterialization(materialization);
    }
    planner.setRoot(rel);
    return planner.findBestExp();
  }

  private static List<Object[]> execute(DataContext dc, RelNode rel) throws Exception {
    List<Object[]> rows = new ArrayList<>();
    // Use Calcite's full preparation/execution path, not the subset Interpreter.
    // The independent JSON/SQLite oracle still gates all three executed artifacts.
    try (PreparedStatement statement = RelRunners.run(rel);
         ResultSet result = statement.executeQuery()) {
      int columns = result.getMetaData().getColumnCount();
      while (result.next()) {
        Object[] row = new Object[columns];
        for (int i = 0; i < columns; i++) row[i] = result.getObject(i + 1);
        rows.add(row);
      }
    }
    return rows;
  }

  private static List<Object> schema(RelDataType type) {
    List<Object> fields = new ArrayList<>();
    type.getFieldList().forEach(f -> fields.add(map("name", f.getName(),
        "index", f.getIndex(), "sqlType", f.getType().getSqlTypeName().name(),
        "nullable", f.getType().isNullable(), "fullType", f.getType().getFullTypeString())));
    return fields;
  }

  private static List<String> scans(RelNode rel) {
    Set<String> names = new LinkedHashSet<>();
    new RelVisitor() {
      @Override public void visit(RelNode node, int ordinal, RelNode parent) {
        if (node instanceof HepRelVertex vertex) {
          visit(vertex.getCurrentRel(), ordinal, parent);
          return;
        }
        if (node instanceof TableScan scan) names.add(String.join(".", scan.getTable().getQualifiedName()));
        super.visit(node, ordinal, parent);
      }
    }.go(rel);
    return new ArrayList<>(names);
  }

  private static String normalizedNumber(Number value) {
    if (value instanceof Double d && !Double.isFinite(d)) return d.toString();
    if (value instanceof Float f && !Float.isFinite(f)) return f.toString();
    return new BigDecimal(value.toString()).stripTrailingZeros().toPlainString();
  }

  private static List<Object> encodeRows(List<Object[]> rows) {
    List<Object> out = new ArrayList<>();
    for (Object[] row : rows) {
      List<Object> cells = new ArrayList<>();
      for (Object value : row) {
        cells.add(value == null ? map("javaType", "null", "value", null)
            : map("javaType", value.getClass().getName(), "value", value,
                "numericCanonical", value instanceof Number n ? normalizedNumber(n) : null));
      }
      out.add(cells);
    }
    return out;
  }

  private static Map<List<String>, Long> bag(List<Object[]> rows, boolean normalizeNumbers) {
    Map<List<String>, Long> result = new LinkedHashMap<>();
    for (Object[] row : rows) {
      List<String> cells = new ArrayList<>();
      for (Object value : row) {
        if (value == null) cells.add("NULL");
        else if (normalizeNumbers && value instanceof Number n) cells.add("NUMBER:" + normalizedNumber(n));
        else cells.add(value.getClass().getName() + ":" + value);
      }
      result.merge(cells, 1L, Long::sum);
    }
    return result;
  }

  private static Map<String, Object> map(Object... pairs) {
    Map<String, Object> out = new LinkedHashMap<>();
    for (int i = 0; i < pairs.length; i += 2) out.put((String) pairs[i], pairs[i + 1]);
    return out;
  }

  private static void run(Map<String, Object> report, String id, String state) throws Exception {
    Contract spec = contract(id);
    report.put("explanatorySql", map("V", spec.viewSql(), "Q", spec.querySql(),
        "authority", "RelBuilder programs in this fixed source are authoritative; these strings are not parsed or rewritten."));
    report.put("theoreticalBoundary", spec.boundary());
    report.put("costPlanning", map("status", "NOT_RUN", "reason", "This batch is a HEP rule-coverage and execution audit, not full Volcano cost selection."));
    report.put("rules", MV_RULES.stream().map(Object::toString).toList());
    report.put("preprocessingRules", List.of(AVG_ONLY.toString()));
    report.put("preprocessingFunctionsToReduce", List.of("AVG"));
    report.put("harnessRevision", "v2: explicit C4 output projection; AVG-only reduction preserves SUM matching; unwrap HEP trace vertices");
    report.put("ruleMatchLimit", 128);
    report.put("fixtureTypeDeviation", "salary and commission are nullable INTEGER, not proposed DECIMAL(12,2); no cross-type or numeric-precision claim.");
    report.put("avgTypeLimitation", "C6 intentionally retains upstream AVG(INTEGER). The base-fixture mean is exactly 180; non-integral means, DECIMAL arithmetic, and general weighted-AVG numeric precision are not tested.");
    report.put("comparisonPolicy", "Unordered multiset; retain duplicates and NULL. Strict bag includes Java value class; numeric-normalized bag compares exact decimal spellings without tolerance. Numeric normalization does not override SQL type differences.");
    report.put("executionPolicy", "RelRunners.run performs Calcite JDBC preparation and execution, not the subset Interpreter. Execution timings include preparation; this is not a timing benchmark. HEP selection and subsequent execution preparation are distinct; no full materialization cost-selection claim.");

    Class.forName("org.apache.calcite.jdbc.Driver");
    try (Connection jdbc = DriverManager.getConnection("jdbc:calcite:")) {
      CalciteConnection connection = jdbc.unwrap(CalciteConnection.class);
      SchemaPlus root = connection.getRootSchema();
      RelDataType employeeType = connection.getTypeFactory().builder()
          .add("empid", SqlTypeName.INTEGER).nullable(false)
          .add("deptno", SqlTypeName.INTEGER).nullable(true)
          .add("salary", SqlTypeName.INTEGER).nullable(true)
          .add("commission", SqlTypeName.INTEGER).nullable(true).build();
      Rows employees = new Rows(employeeType, fixture(state));
      root.add("emps", employees);
      report.put("schema", schema(employeeType));
      report.put("constraints", List.of("empid NOT NULL", "No declared unique keys", "No cross-department salary-disjointness guarantee"));
      report.put("baseRows", encodeRows(employees.rows));

      RelBuilder builder = RelBuilder.create(Frameworks.newConfigBuilder().defaultSchema(root).build());
      DataContext context = DataContexts.of(connection, root);
      RelNode view = buildView(builder, id);
      RelNode query = buildQuery(builder, id);
      report.put("viewRel", RelOptUtil.toString(view));
      report.put("originalQueryRel", RelOptUtil.toString(query));
      report.put("querySchema", schema(query.getRowType()));
      report.put("viewSchema", schema(view.getRowType()));

      long start = System.nanoTime();
      List<Object[]> materializedRows = execute(context, view);
      report.put("materializationMs", (System.nanoTime() - start) / 1e6);
      report.put("materializationBaseCounters", employees.counters());
      report.put("materializedViewRows", encodeRows(materializedRows));
      Rows mv = new Rows(view.getRowType(), materializedRows);
      root.add("MV", mv);
      RelNode mvScan = builder.scan("MV").build();
      RelOptMaterialization materialization = new RelOptMaterialization(
          mvScan, view, null, Collections.singletonList("MV"));

      // AVG reduction is upstream code, also recorded; no handcrafted SUM/COUNT rewrite.
      Trace preprocessing = new Trace();
      start = System.nanoTime();
      RelNode executionQuery = hep(query, List.of(AVG_ONLY),
          Collections.emptyList(), preprocessing);
      report.put("preprocessingMs", (System.nanoTime() - start) / 1e6);
      report.put("preprocessingProductions", preprocessing.productions);
      report.put("executionQueryRel", RelOptUtil.toString(executionQuery));
      employees.resetCounters();
      mv.resetCounters();
      start = System.nanoTime();
      List<Object[]> originalRows = execute(context, executionQuery);
      report.put("originalExecutionMs", (System.nanoTime() - start) / 1e6);
      report.put("originalRows", encodeRows(originalRows));
      report.put("originalExecutionCounters", map("emps", employees.counters(), "MV", mv.counters()));

      Trace trace = new Trace();
      report.put("ruleProductions", trace.productions); // Retain partial trace if planning throws.
      start = System.nanoTime();
      RelNode rewritten = hep(executionQuery, MV_RULES, List.of(materialization), trace);
      report.put("planningMs", (System.nanoTime() - start) / 1e6);
      report.put("rawRewrittenRel", RelOptUtil.toString(rewritten));
      report.put("rawRewrittenSchema", schema(rewritten.getRowType()));
      if (!RelOptUtil.areRowTypesEqual(query.getRowType(), rewritten.getRowType(), false)) {
        throw new IllegalStateException("Rewritten SQL row types differ before alias restoration");
      }
      // MV rules may use internal aliases. Restore only the declared output names,
      // without casting, reordering, changing expressions, or inspecting row values.
      rewritten = builder.push(rewritten).rename(query.getRowType().getFieldNames()).build();
      report.put("outputNameAdapter", "RelBuilder.rename after strict row-type check; raw pre-adapter plan/schema retained");
      report.put("ruleAttempts", trace.attempted);
      report.put("rewrittenRel", RelOptUtil.toString(rewritten));
      report.put("rewrittenSchema", schema(rewritten.getRowType()));
      report.put("rewrittenScanTables", scans(rewritten));
      employees.resetCounters();
      mv.resetCounters();
      start = System.nanoTime();
      List<Object[]> rewrittenRows = execute(context, rewritten);
      report.put("rewrittenExecutionMs", (System.nanoTime() - start) / 1e6);
      report.put("rewrittenRows", encodeRows(rewrittenRows));
      report.put("rewrittenExecutionCounters", map("emps", employees.counters(), "MV", mv.counters()));
      boolean strict = bag(originalRows, false).equals(bag(rewrittenRows, false));
      boolean normalized = bag(originalRows, true).equals(bag(rewrittenRows, true));
      boolean types = RelOptUtil.areRowTypesEqual(query.getRowType(), rewritten.getRowType(), false);
      report.put("comparison", map("typedBagEqual", strict, "numericNormalizedBagEqual", normalized,
          "sqlRowTypesEqualIgnoringNames", types, "originalRowCount", originalRows.size(),
          "rewrittenRowCount", rewrittenRows.size()));
      report.put("classification", map("usesMaterializedViewAtExecution", mv.scans > 0,
          "readsBaseAtExecution", employees.scans > 0,
          "note", "Base fallback is allowed. No reuse is not evidence of algorithmic impossibility. Tiny-fixture agreement is not a universal equivalence proof."));
      report.put("status", strict && types ? "EXECUTED_EQUAL" : normalized && types
          ? "EXECUTED_NUMERIC_EQUAL_TYPE_REVIEW" : "RESULT_MISMATCH_REQUIRES_REVIEW");
    }
  }

  private static void writeJson(JsonGenerator out, Object value) throws Exception {
    if (value == null) out.writeNull();
    else if (value instanceof Map<?, ?> object) {
      out.writeStartObject();
      for (Map.Entry<?, ?> field : object.entrySet()) {
        out.writeFieldName(field.getKey().toString());
        writeJson(out, field.getValue());
      }
      out.writeEndObject();
    } else if (value instanceof Iterable<?> items) {
      out.writeStartArray();
      for (Object item : items) writeJson(out, item);
      out.writeEndArray();
    } else if (value instanceof Boolean b) out.writeBoolean(b);
    else if (value instanceof BigDecimal n) out.writeNumber(n);
    else if (value instanceof BigInteger n) out.writeNumber(n);
    else if (value instanceof Integer || value instanceof Short || value instanceof Byte) out.writeNumber(((Number) value).intValue());
    else if (value instanceof Long n) out.writeNumber(n);
    else if (value instanceof Float n && Float.isFinite(n)) out.writeNumber(n);
    else if (value instanceof Double n && Double.isFinite(n)) out.writeNumber(n);
    else out.writeString(value.toString());
  }

  public static void main(String[] args) throws Exception {
    Map<String, String> cli = new LinkedHashMap<>();
    if (args.length % 2 != 0) throw new IllegalArgumentException("Use --case C1 --state base --output absolute-path.json");
    for (int i = 0; i < args.length; i += 2) {
      if (!List.of("--case", "--state", "--output").contains(args[i])) throw new IllegalArgumentException("Unknown argument: " + args[i]);
      if (cli.put(args[i], args[i + 1]) != null) throw new IllegalArgumentException("Duplicate argument: " + args[i]);
    }
    String id = cli.get("--case");
    String requestedState = cli.getOrDefault("--state", "base");
    String state = requestedState.equals("all_null") || requestedState.equals("all-null")
        ? "all_salary_null" : requestedState;
    if (id == null || !cli.containsKey("--output")) throw new IllegalArgumentException("--case and --output are required");
    contract(id);
    Path output = Path.of(cli.get("--output"));
    if (!output.isAbsolute()) throw new IllegalArgumentException("--output must be absolute");
    if (Files.exists(output)) throw new IllegalStateException("Refusing to overwrite: " + output);
    Map<String, Object> report = map("formatVersion", 1, "caseId", id, "state", state, "requestedState", requestedState,
        "startedAt", Instant.now().toString(), "javaVersion", System.getProperty("java.version"),
        "calciteRequiredVersion", "1.40.0", "calcitePackageVersion", RelNode.class.getPackage().getImplementationVersion(),
        "backend", "CPU_ONLY_CALCITE_HEP_AND_REL_RUNNERS_JDBC", "status", "STARTED");
    int exit = 0;
    try {
      run(report, id, state);
      if ("RESULT_MISMATCH_REQUIRES_REVIEW".equals(report.get("status"))) exit = 3;
    } catch (Throwable failure) {
      report.put("status", "ERROR");
      report.put("error", map("class", failure.getClass().getName(), "message", failure.getMessage(),
          "stack", Arrays.stream(failure.getStackTrace()).limit(30).map(Object::toString).toList()));
      exit = 2;
    } finally {
      report.put("finishedAt", Instant.now().toString());
      Files.createDirectories(output.getParent());
      try (OutputStream stream = Files.newOutputStream(output, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
           JsonGenerator json = new JsonFactory().createGenerator(stream)) {
        json.useDefaultPrettyPrinter();
        writeJson(json, report);
      }
    }
    System.out.println("STATUS=" + report.get("status") + " OUTPUT=" + output);
    if (exit != 0) System.exit(exit);
  }
}
