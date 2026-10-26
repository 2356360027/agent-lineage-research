package research;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.calcite.avatica.util.Casing;
import org.apache.calcite.avatica.util.Quoting;
import org.apache.calcite.config.CalciteConnectionConfigImpl;
import org.apache.calcite.jdbc.CalciteSchema;
import org.apache.calcite.jdbc.JavaTypeFactoryImpl;
import org.apache.calcite.plan.*;
import org.apache.calcite.plan.hep.*;
import org.apache.calcite.plan.volcano.VolcanoPlanner;
import org.apache.calcite.prepare.CalciteCatalogReader;
import org.apache.calcite.rel.*;
import org.apache.calcite.rel.core.Sort;
import org.apache.calcite.rel.core.TableScan;
import org.apache.calcite.rel.metadata.*;
import org.apache.calcite.rel.rel2sql.RelToSqlConverter;
import org.apache.calcite.rel.rules.*;
import org.apache.calcite.rel.rules.materialize.MaterializedViewRules;
import org.apache.calcite.rel.type.*;
import org.apache.calcite.rex.RexBuilder;
import org.apache.calcite.schema.*;
import org.apache.calcite.schema.impl.AbstractTable;
import org.apache.calcite.sql.*;
import org.apache.calcite.sql.fun.SqlStdOperatorTable;
import org.apache.calcite.sql.parser.SqlParser;
import org.apache.calcite.sql.type.SqlTypeName;
import org.apache.calcite.sql.validate.*;
import org.apache.calcite.sql2rel.*;
import org.apache.calcite.tools.Frameworks;
import org.apache.calcite.tools.RelBuilder;
import org.apache.calcite.util.ImmutableBitSet;

import java.nio.file.*;
import java.nio.file.Path;
import java.util.*;

/** Generic public-SQL/view adapter. No original rows, oracle, or known transform.
 * Uses unmodified upstream MV rules. A cache-only rewrite is positive evidence;
 * a missing rewrite means UNKNOWN, never a proof that reuse is impossible.
 * Generates SQL for independent read-only execution; does not execute base scans.
 */
public final class ReuseSqlPlanner {
  static final ObjectMapper JSON = new ObjectMapper();
  static final RelOptRule AVG_ONLY = AggregateReduceFunctionsRule.Config.DEFAULT
      .withFunctionsToReduce(Set.of(SqlKind.AVG)).toRule();
  static final List<RelOptRule> NORMALIZE = List.of(AVG_ONLY,
      CoreRules.PROJECT_MERGE, CoreRules.FILTER_MERGE, CoreRules.FILTER_PROJECT_TRANSPOSE,
      CoreRules.AGGREGATE_PROJECT_MERGE, CoreRules.PROJECT_REMOVE);
  static final List<RelOptRule> MV_RULES = List.of(
      MaterializedViewRules.PROJECT_AGGREGATE, MaterializedViewRules.AGGREGATE,
      MaterializedViewRules.PROJECT_FILTER, MaterializedViewRules.FILTER,
      MaterializedViewRules.PROJECT_JOIN, MaterializedViewRules.JOIN);

  static class PublicTable extends AbstractTable {
    final RelDataType type;
    final List<ImmutableBitSet> keys;
    final double rows;
    PublicTable(RelDataType type, List<ImmutableBitSet> keys, double rows) {
      this.type=type; this.keys=keys; this.rows=rows;
    }
    @Override public RelDataType getRowType(RelDataTypeFactory f) { return f.copyType(type); }
    @Override public Statistic getStatistic() { return Statistics.of(rows,keys); }
  }

  static RelNode removePresentationSort(RelNode node) {
    // Public suite semantics is an unordered bag; LIMIT/OFFSET is NOT removable.
    while (node instanceof Sort s && s.fetch==null && s.offset==null) node=s.getInput();
    return node;
  }

  static RelNode parse(String sql, SqlParser.Config config, CalciteCatalogReader catalog,
      JavaTypeFactoryImpl types, RelOptCluster cluster) throws Exception {
    SqlValidator validator=SqlValidatorUtil.newValidator(SqlStdOperatorTable.instance(),
        catalog, types, SqlValidator.Config.DEFAULT.withIdentifierExpansion(true)
            .withConformance(SqlConformanceEnum.LENIENT));
    SqlNode parsed=SqlParser.create(sql,config).parseQuery();
    SqlNode validated=validator.validate(parsed);
    SqlToRelConverter converter=new SqlToRelConverter((RelOptTable.ViewExpander)null,
        validator,catalog,cluster,StandardConvertletTable.INSTANCE,
        SqlToRelConverter.config().withTrimUnusedFields(false).withExpand(true));
    RelRoot root=converter.convertQuery(validated,false,true);
    RelNode flattened=converter.flattenTypes(root.project(),true);
    RelNode decorrelated=RelDecorrelator.decorrelateQuery(flattened,
        RelBuilder.proto().create(cluster,catalog));
    return removePresentationSort(decorrelated);
  }

  static RelNode hep(RelNode rel,List<RelOptRule> rules,List<RelOptMaterialization> materializations) {
    HepProgramBuilder program=new HepProgramBuilder().addMatchOrder(HepMatchOrder.TOP_DOWN)
        .addMatchLimit(256).addRuleCollection(rules);
    HepPlanner planner=new HepPlanner(program.build());
    List<RelMetadataProvider> providers=new ArrayList<>();
    providers.add(DefaultRelMetadataProvider.INSTANCE);
    planner.registerMetadataProviders(providers);
    rel.getCluster().setMetadataProvider(ChainedRelMetadataProvider.of(providers));
    rel.getCluster().invalidateMetadataQuery();
    for (RelOptMaterialization m:materializations) planner.addMaterialization(m);
    planner.setRoot(rel);
    return planner.findBestExp();
  }

  static Set<String> scans(RelNode rel) {
    Set<String> names=new TreeSet<>();
    new RelVisitor() {
      @Override public void visit(RelNode n,int ordinal,RelNode parent) {
        if (n instanceof HepRelVertex v) { visit(v.getCurrentRel(),ordinal,parent); return; }
        if (n instanceof TableScan scan) {
          List<String> path=scan.getTable().getQualifiedName(); names.add(path.get(path.size()-1));
        }
        super.visit(n,ordinal,parent);
      }
    }.go(rel);
    return names;
  }

  static void run(JsonNode input,Map<String,Object> result) throws Exception {
    long started=System.nanoTime();
    SchemaPlus root=Frameworks.createRootSchema(true);
    // SQLite AVG(integer) is real-valued. Default Calcite AVG integer typing is
    // not silently treated as equivalent. This disclosed adapter override is
    // shared by Q and H; the MV rules themselves are unchanged.
    JavaTypeFactoryImpl types=new JavaTypeFactoryImpl(new RelDataTypeSystemImpl() {
      @Override public RelDataType deriveAvgAggType(RelDataTypeFactory f,RelDataType arg) {
        return f.createTypeWithNullability(f.createSqlType(SqlTypeName.DOUBLE),true);
      }
    });
    for (JsonNode table:input.get("tables")) {
      RelDataTypeFactory.Builder builder=types.builder();
      List<String> names=new ArrayList<>();
      for (JsonNode col:table.get("columns")) {
        String name=col.get("name").asText(); names.add(name);
        RelDataType t=types.createSqlType(SqlTypeName.valueOf(col.get("type").asText()));
        builder.add(name,types.createTypeWithNullability(t,col.get("nullable").asBoolean()));
      }
      List<Integer> pk=new ArrayList<>();
      for (JsonNode key:table.get("primary_key")) pk.add(names.indexOf(key.asText()));
      if (pk.contains(-1)) throw new IllegalArgumentException("Unknown primary-key column");
      List<ImmutableBitSet> keys=pk.isEmpty()?List.of():List.of(ImmutableBitSet.of(pk));
      root.add(table.get("name").asText(),new PublicTable(builder.build(),keys,1000));
    }
    Properties props=new Properties(); props.setProperty("caseSensitive","true");
    props.setProperty("materializationsEnabled","true");
    CalciteConnectionConfigImpl connection=new CalciteConnectionConfigImpl(props);
    CalciteCatalogReader catalog=new CalciteCatalogReader(CalciteSchema.from(root),List.of(),types,connection);
    VolcanoPlanner planner=new VolcanoPlanner(null,Contexts.of(connection));
    planner.addRelTraitDef(ConventionTraitDef.INSTANCE);
    planner.addRelTraitDef(RelCollationTraitDef.INSTANCE);
    RelOptCluster cluster=RelOptCluster.create(planner,new RexBuilder(types));
    SqlParser.Config config=SqlParser.config().withCaseSensitive(true)
        .withQuotedCasing(Casing.UNCHANGED).withUnquotedCasing(Casing.UNCHANGED)
        .withQuoting(Quoting.DOUBLE_QUOTE).withConformance(SqlConformanceEnum.LENIENT);
    RelNode query=parse(input.get("current_sql_quoted").asText(),config,catalog,types,cluster);
    RelNode history=parse(input.get("history_sql_quoted").asText(),config,catalog,types,cluster);
    result.put("query_rel",RelOptUtil.toString(query));
    result.put("history_rel",RelOptUtil.toString(history));
    RelDataType viewType=history.getRowType();
    List<String> columns=new ArrayList<>();
    for (JsonNode col:input.get("cache").get("columns")) columns.add(col.asText());
    if (!viewType.getFieldNames().equals(columns))
      throw new IllegalArgumentException("Parsed history columns differ from observed cache: "+viewType.getFieldNames());
    root.add("cache",new PublicTable(viewType,List.of(),input.get("cache").get("rows").size()));
    RelNode cache=RelBuilder.proto().create(cluster,catalog).scan("cache").build();
    RelNode normalizedQ=hep(query,NORMALIZE,List.of());
    RelNode normalizedH=hep(history,NORMALIZE,List.of());
    result.put("normalized_query_rel",RelOptUtil.toString(normalizedQ));
    result.put("normalized_history_rel",RelOptUtil.toString(normalizedH));
    RelOptMaterialization mv=new RelOptMaterialization(cache,normalizedH,null,List.of("cache"));
    RelNode rewritten=hep(normalizedQ,MV_RULES,List.of(mv));
    String engine="HEP_MV_RULES";
    // The two documented upstream rewriting approaches are complementary.
    // SubstitutionVisitor also handles projected subexpressions inside aggregates.
    // Do not intentionally weaken the baseline to only one configured rule path.
    List<RelNode> alternatives=new ArrayList<>();
    try {
      for (var pair:RelOptMaterializations.useMaterializedViews(normalizedQ,List.of(mv))) {
        if (scans(pair.left).equals(Set.of("cache"))) alternatives.add(pair.left);
      }
      result.put("substitution_cache_only_alternatives",alternatives.size());
    } catch (RuntimeException e) {
      result.put("substitution_error",e.getClass().getName()+": "+e.getMessage());
    }
    if (!scans(rewritten).equals(Set.of("cache")) && !alternatives.isEmpty()) {
      alternatives.sort(Comparator.comparing(RelOptUtil::toString));
      rewritten=alternatives.get(0); engine="UPSTREAM_SUBSTITUTION";
    }
    result.put("selected_engine",engine);
    Set<String> accessed=scans(rewritten);
    result.put("selected_rel",RelOptUtil.toString(rewritten));
    result.put("scan_tables",accessed);
    boolean cacheOnly=accessed.equals(Set.of("cache"));
    result.put("admissibility",cacheOnly?"applicable":"unknown");
    if (cacheOnly) {
      // Preserve public Q's output names after aggregate reduction introduces
      // internal $f fields. This is schema transport, not a private answer fix.
      RelBuilder naming=RelBuilder.proto().create(cluster,catalog).push(rewritten);
      rewritten=naming.project(naming.fields(),query.getRowType().getFieldNames(),true).build();
      SqlDialect dialect=SqlDialect.DatabaseProduct.SQLITE.getDialect();
      SqlNode statement=new RelToSqlConverter(dialect).visitRoot(rewritten).asStatement();
      result.put("rewrite_sql",statement.toSqlString(dialect).getSql());
    }
    result.put("planner_seconds",(System.nanoTime()-started)/1e9);
    result.put("status","PLANNED");
  }

  public static void main(String[] args) throws Exception {
    if (args.length!=2) throw new IllegalArgumentException("input.json output.json required");
    Path out=Path.of(args[1]);
    if (Files.exists(out)) throw new IllegalArgumentException("No overwrite or retry");
    JsonNode input=JSON.readTree(Files.readString(Path.of(args[0])));
    Map<String,Object> result=new LinkedHashMap<>();
    result.put("case_id",input.get("case_id").asText());
    result.put("upstream","Apache Calcite 1.40.0");
    result.put("normalization_rules",NORMALIZE.stream().map(Object::toString).toList());
    result.put("materialization_rules",MV_RULES.stream().map(Object::toString).toList());
    result.put("match_limit_per_program",256);
    result.put("adapter_limits","AVG returns DOUBLE; PK and nullability only. No CHECK/range/FK metadata. "
        +"Unordered bag; only root sorts without LIMIT/OFFSET removed. No base rows or oracle provided. "
        +"Missing rewrite means unknown, not inapplicable. Base-row estimate 1000 is not observed data.");
    try { run(input,result); }
    catch (Throwable e) {
      result.put("status","PLANNER_ERROR"); result.put("admissibility","unknown");
      result.put("error_type",e.getClass().getName()); result.put("error",String.valueOf(e.getMessage()));
    }
    Files.writeString(out,JSON.writerWithDefaultPrettyPrinter().writeValueAsString(result),
        StandardOpenOption.CREATE_NEW);
  }
}
