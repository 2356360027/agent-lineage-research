"""Trusted-operator loader for local, offline tool-environment software checks.

No model, network, SQL execution or semantic approval. The returned cache is
private operator data, not a model envelope. File hashes bind the observed bytes;
they do not lock the database against subsequent writes by another process.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from runtime import bird_readonly_audit as bird
from runtime.cache_intent_dev_package import validate_plan
from scripts.fetch_bird_fixed import no_link_ancestors, safe_component, safe_identifier, strict_json


VERSION = "cache-intent-tool-inputs-v1"
EXPECTED_MANIFEST_SHA256 = "6ee741f4935bcfe2b30688401ecebf83b658a38b847b026b4a360746d7a048e7"
DEFAULT_PACKAGE_DIR = Path("runtime/runs/cache-intent-dev-package-20261012-001")
DEFAULT_BIRD_DIR = Path("runtime/runs/datasets/bird-mini-fixed-20261005-002")
HISTORY_IDS = frozenset(f"h{qid}{suffix}" for qid in (200, 234) for suffix in "abc")


def _require(value, message):
    if not value:
        raise ValueError(message)


def _sha(value):
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
             "Expected lowercase SHA-256 digest")
    return value


def _plain_file(path):
    no_link_ancestors(path)
    _require(path.is_file(), "Expected a regular input file")
    return path


def _read_bound_json(path, digest):
    raw = _plain_file(path).read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == digest, "Input changed after hash verification")
    return strict_json(raw.decode("utf-8"))


def _package_manifest(package, expected):
    path = package / "artifact_hashes.json"
    manifest = _read_bound_json(path, _sha(expected))
    _require(type(manifest) is dict and bool(manifest), "Invalid artifact manifest")
    for name, digest in manifest.items():
        _require(isinstance(name, str), "Invalid artifact name")
        safe_component(name)
        _require(Path(name).name == name and name != path.name, "Invalid artifact name")
        _sha(digest)
    entries = list(package.iterdir())
    for entry in entries:
        _plain_file(entry)
    _require({p.name for p in entries} == set(manifest) | {path.name},
             "Artifact manifest does not cover the complete package")
    for name, digest in manifest.items():
        _require(bird.hash_file(package / name) == digest, "Package artifact bytes changed")
    required = {"started.json", "summary.json", "candidate_plan.json",
                "database_hashes_after.json", "readonly_source.py"}
    required.update(f"{ident}.result.json" for ident in HISTORY_IDS)
    _require(required <= manifest.keys(), "Package lacks required artifacts")
    return manifest


def _typed_cell(cell):
    _require(type(cell) is list and len(cell) == 2, "Malformed typed cache cell")
    kind, value = cell
    if kind == "null":
        _require(value is None, "Invalid null cell")
        return
    _require(isinstance(value, str), "Typed non-null values must be strings")
    try:
        if kind == "integer":
            _require(str(int(value)) == value, "Noncanonical integer cell")
        elif kind == "real":
            _require(float.fromhex(value).hex() == value, "Noncanonical real cell")
        elif kind == "blob":
            _require(bytes.fromhex(value).hex() == value, "Noncanonical blob cell")
        else:
            _require(kind == "text", "Unknown cache cell type")
    except (TypeError, OverflowError) as exc:
        raise ValueError("Malformed typed cache value") from exc


def _verify_result(result, historical_sql):
    _require(type(result) is dict and result.get("complete") is True and
             result.get("status") == "EXECUTED" and result.get("denied") == [],
             "Cached history must be a complete, successful execution")
    _require(result.get("sql_sha256") == bird.digest(historical_sql),
             "Cache SQL digest does not match the selected historical SQL")
    rows, columns = result.get("rows"), result.get("columns")
    _require(type(rows) is list and type(columns) is list and bool(columns) and
             all(isinstance(c, str) for c in columns), "Malformed cached result shape")
    for row in rows:
        _require(type(row) is list and len(row) == len(columns), "Cache row arity mismatch")
        for cell in row:
            _typed_cell(cell)
    expected = bird.fingerprints(rows) | {
        "retained_rows": len(rows),
        "result_bytes": sum(len(bird.encoded(row)) for row in rows),
        "value_types": dict(Counter(cell[0] for row in rows for cell in row)),
    }
    for key, value in expected.items():
        _require(key in result and bird.encoded(result[key]) == bird.encoded(value),
                 "Cache row metadata or fingerprint mismatch")


def load_inputs(package_dir, bird_dir, history_id,
                expected_manifest_sha256=EXPECTED_MANIFEST_SHA256):
    """Return exactly five local-only inputs after provenance and row checks.

    The default manifest digest pins the existing pending-review package. An
    override is for an explicitly trusted operator (including synthetic tests),
    never a model-supplied argument. Target-reference artifacts are hashed but
    never parsed. Private plan metadata is validated operator-side, not returned;
    only the chosen historical SQL and cached result leave the loader.
    """
    _require(isinstance(history_id, str) and history_id in HISTORY_IDS,
             "Unknown fixed history identifier")
    package, dataset = Path(package_dir).absolute(), Path(bird_dir).absolute()
    for directory in (package, dataset):
        no_link_ancestors(directory)
        _require(directory.is_dir(), "Input directory is missing")
    artifacts = _package_manifest(package, expected_manifest_sha256)

    def load(name):
        return _read_bound_json(package / name, artifacts[name])

    summary, started, plan = load("summary.json"), load("started.json"), load("candidate_plan.json")
    _require(type(summary) is dict and summary.get("status") == "CANDIDATES_PENDING_HUMAN_REVIEW"
             and summary.get("inference_eligible") is False
             and summary.get("database_hashes_unchanged") is True
             and type(summary.get("human_approvals")) is int and summary["human_approvals"] == 0
             and type(summary.get("model_calls")) is int and summary["model_calls"] == 0,
             "Package is not the pending-review, offline development package")
    _require(type(started) is dict and started.get("plan_sha256") == artifacts["candidate_plan.json"]
             and bird.encoded(started.get("limits")) == bird.encoded(bird.LIMITS),
             "Archived plan or execution limits do not match")
    try:
        validate_plan(plan)
        histories = [h for target in plan["targets"] for h in target["histories"]]
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid archived candidate plan") from exc
    _require(len(histories) == 6 and {h["id"] for h in histories} == HISTORY_IDS,
             "Archived history set differs from the six fixed histories")
    selected = [h for h in histories if h["id"] == history_id]
    _require(len(selected) == 1, "Historical SQL must have exactly one source record")
    historical_sql = selected[0]["sql"]
    result_name = f"{history_id}.result.json"
    cached_result = load(result_name)
    _verify_result(cached_result, historical_sql)

    backend_path = _plain_file(Path(bird.__file__).absolute())
    backend_sha = bird.hash_file(backend_path)
    _require(backend_sha == artifacts["readonly_source.py"],
             "Current readonly backend differs from the archived backend")
    before, after = started.get("database_hashes_before"), load("database_hashes_after.json")
    _require(type(before) is dict and before == after and "toxicology" in before,
             "Recorded database hashes disagree")
    for digest in before.values():
        _sha(digest)
    for name in ("dataset_manifest.json", "source_manifest.json", "questions.jsonl", "selected_db_ids.json"):
        _plain_file(dataset / name)
    acquisition_path = dataset / "dataset_manifest.json"
    acquisition_sha = bird.hash_file(acquisition_path)
    acquisition = _read_bound_json(acquisition_path, acquisition_sha)
    _require(type(acquisition) is dict and type(acquisition.get("selected_db_ids")) is list,
             "Invalid original acquisition manifest")
    database_ids = acquisition["selected_db_ids"]
    for database_id in database_ids:
        _require(isinstance(database_id, str), "Invalid database identifier")
        safe_identifier(database_id)
    _require(len(database_ids) == len(set(database_ids)) and set(database_ids) == set(before),
             "Acquisition and package database sets disagree")
    paths, actual = {}, {}
    for database_id in database_ids:
        path = _plain_file(dataset / "databases" / database_id / f"{database_id}.sqlite")
        _require(not any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")),
                 "Database sidecar present; immutable snapshot not established")
        paths[database_id], actual[database_id] = path, bird.hash_file(path)
    _require(actual == before, "Current database bytes differ from the recorded snapshot")
    verified_acquisition = bird.verify_download(dataset, database_ids, actual)
    _require(verified_acquisition == acquisition and bird.hash_file(acquisition_path) == acquisition_sha,
             "Acquisition manifest changed during verification")
    receipt = {
        "loader_version": VERSION,
        "package_manifest_sha256": expected_manifest_sha256,
        "package_artifact_count": len(artifacts),
        "history_id": history_id,
        "candidate_plan_sha256": artifacts["candidate_plan.json"],
        "history_result_sha256": artifacts[result_name],
        "history_sql_sha256": bird.digest(historical_sql),
        "cache_bag_sha256": cached_result["bag_sha256"],
        "cache_sequence_sha256": cached_result["sequence_sha256"],
        "readonly_source_sha256": backend_sha,
        "started_sha256": artifacts["started.json"],
        "summary_sha256": artifacts["summary.json"],
        "database_hashes_after_sha256": artifacts["database_hashes_after.json"],
        "bird_acquisition_manifest_sha256": acquisition_sha,
        "database_sha256": actual["toxicology"],
        "inference_eligible": False,
        "human_approvals": 0,
    }
    return {"database_path": paths["toxicology"], "database_sha256": actual["toxicology"],
            "history_sql": historical_sql, "cached_result": deepcopy(cached_result),
            "input_receipt": receipt}
