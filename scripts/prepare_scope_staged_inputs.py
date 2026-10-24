"""Versioned, private, scope-informed previews; no SQL, model or run approval."""
import argparse
from copy import deepcopy
from pathlib import Path

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_prompts as prompts
from runtime import cache_intent_staged_preview as staged
from scripts.audit_staged_input_exposure import inspect
from scripts.fetch_bird_fixed import absent, no_link_ancestors, read_json

VERSION = "scope-informed-staged-development-v1"
SCOPE_REPORT_SHA256 = "d5f0314839e435f760c454bb604eb24cd5cfd8e3bfbf1f9a754dcb2a049be86d"
SCOPE_SQL = ("SELECT COUNT(*) AS rows FROM connected c LEFT JOIN bond b ON b.bond_id=c.bond_id "
    "WHERE (c.atom_id='TR009_12' OR c.atom_id2='TR009_12') AND "
    "(b.bond_id IS NULL OR b.molecule_id IS NULL OR b.molecule_id<>'TR009')")
SCOPE_TEXT = ("Verified property of this fixed database snapshot: every non-null bond_id in "
    "a connected record with atom_id='TR009_12' or atom_id2='TR009_12' resolves to a "
    "bond whose molecule_id is 'TR009'. This property is snapshot-specific; it is "
    "not inferred from identifier spelling and is not a rule for other databases.")


def validate_scope_report(report, database_sha256):
    """Check the property receipt; caller must also pin its file hash and DB."""
    if (report.get("status") != "SNAPSHOT_ONLY_NO_SEMANTIC_APPROVAL" or
            report.get("database_unchanged") is not True or
            report.get("database_sha256_before") != database_sha256 or
            report.get("database_sha256_after") != database_sha256):
        raise ValueError("Scope receipt is not bound to unchanged database")
    record = report["results"]["target_mismatched_or_missing_bond"]
    if (record.get("status") != "EXECUTED" or record.get("truncated") is not False
            or record.get("sql") != SCOPE_SQL or record.get("columns") != ["rows"]
            or record.get("rows") != [[0]] or type(record["rows"][0][0]) is not int):
        raise ValueError("Scope query does not establish the required zero-mismatch property")


def augment_pair(pair, target_id):
    if type(target_id) is not int or target_id not in (200,234) or len(pair) != 2:
        raise ValueError("Unsupported target/pair")
    packets = [staged.validate_preview(e) for e in pair]
    difference = prompts._diff(*(prompts._expanded(e) for e in pair))
    price_path = ("messages",1,"content","context","cache","read_cost")
    if difference != [price_path] or [p["context"]["cache"]["read_cost"] for p in packets] != [1,8]:
        raise ValueError("Expected canonical price-1/8 pair")
    packet = packets[0]
    if target_id == 200:
        return deepcopy(pair)
    evidence = packet["evidence"]
    if type(evidence) is not str or SCOPE_TEXT in evidence:
        raise ValueError("Missing original evidence or scope already appended")
    new_pair = staged.make_pair(packet["current_request"], evidence+"\n"+SCOPE_TEXT,
        packet["schema"], packet["history"], packet["context"]["database_sha256"])
    for before, after in zip(pair, new_pair):
        if prompts._diff(prompts._expanded(before),prompts._expanded(after)) != [
                ("messages",1,"content","evidence")]:
            raise ValueError("Non-evidence field changed")
    return new_pair


def build(package, manifest_sha256, scope_report, database, output):
    output = Path(output); absent(output)
    receipt, original = inspect(package, manifest_sha256)
    scope_report, database = Path(scope_report), Path(database)
    for path in (scope_report,database): no_link_ancestors(path)
    if audit.hash_file(scope_report) != SCOPE_REPORT_SHA256:
        raise ValueError("Wrong historical scope report")
    database_sha = receipt["database_sha256_from_receipt"]
    if (audit.hash_file(database) != database_sha or any(
            Path(str(database)+s).exists() for s in ("-wal","-shm","-journal"))):
        raise ValueError("Wrong database or snapshot sidecars")
    validate_scope_report(read_json(scope_report),database_sha)
    targets = {r["history_id"]:r["target_id"] for r in receipt["cases"]}
    revised = [{"history_id":p["history_id"],
                "arms":augment_pair(p["arms"],targets[p["history_id"]])} for p in original]
    metadata = {"version":VERSION,"status":"INPUT_PREPARATION_NOT_RUN_ADMISSION",
        "original_manifest_sha256":manifest_sha256,"scope_report_sha256":SCOPE_REPORT_SHA256,
        "database_sha256":database_sha,"scope_text_sha256":audit.digest(SCOPE_TEXT),
        "original_previews_sha256":audit.digest(original),"revised_previews_sha256":audit.digest(revised),
        "source_sha256":audit.hash_file(__file__),"input_audit_source_sha256":receipt["source_sha256"],
        "preview_source_sha256":audit.hash_file(staged.__file__),
        "changed_histories":["h234a","h234b","h234c"],"unchanged_histories":["h200a","h200b","h200c"],
        "paired_difference":"read_cost_only", "model_calls":0,"sql_queries":0,
        "human_approval_created":False,"inference_authorized":False,
        "setup_cost":"Must account for historical property acquisition and ongoing validation; not free.",
        "do_not_pool_with_old_inputs":True}
    output.mkdir(parents=True,exist_ok=False)
    audit.save_new(output/"receipt.json",metadata)
    audit.save_new(output/"PRIVATE_staged_previews.json",revised)
    with (output/"builder_source.py").open("xb") as stream:
        stream.write(Path(__file__).read_bytes())
    audit.save_new(output/"artifact_hashes.json",{
        p.name:audit.hash_file(p) for p in sorted(output.iterdir()) if p.is_file()})
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("package","manifest-sha256","scope-report","database","output"):
        parser.add_argument("--"+name,required=True)
    a = parser.parse_args()
    print(audit.encoded(build(a.package,a.manifest_sha256,a.scope_report,a.database,a.output)).decode())
