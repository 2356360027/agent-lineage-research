"""Local tool environment, NOT a model runner, approval gate or new algorithm.

The trusted operator supplies hash-bound inputs. Agent arguments cannot choose
database paths, costs or cache rows. Costs are enforced relative units, not yuan
or measured runtime. No retries, resumption, remote endpoints or arbitrary code.
Separate pending/completed records preserve interrupted attempts. A future runner
must keep this directory private and enforce human/task/resource admission.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import shutil
import sys
import time

from runtime import bird_readonly_audit as audit
from scripts.fetch_bird_fixed import absent, no_link_ancestors, read_json

VERSION = "cache-intent-local-tools-v2"
BUDGET, SQL_COST, MAX_ATTEMPTS = 100, 10, 20
LEGACY_PROFILE = "legacy-100-v1"
NONBINDING_PROFILE = "nonbinding-200-v1"
RECOVERY_PROFILE = "exploration-200-recovery-10-v2"
SHORT_RECOVERY_PROFILE = "exploration-20-recovery-10-v1"


def _profile_budget(profile, cache_cost):
    """Trusted operator selection, never a model-supplied numeric override.

    Keep the original wiring behavior. The second profile eliminates cost-unit
    admission failures within the common attempt horizon, not the horizon
    itself or SQL/environment failures. These are relative units, not money.
    """
    if type(profile) is not str or profile not in (LEGACY_PROFILE, NONBINDING_PROFILE, RECOVERY_PROFILE, SHORT_RECOVERY_PROFILE):
        raise ValueError("Unknown fixed budget profile")
    initial = (30 if profile == SHORT_RECOVERY_PROFILE else BUDGET if profile == LEGACY_PROFILE
               else 210 if profile == RECOVERY_PROFILE else 200)
    if profile == NONBINDING_PROFILE and initial < MAX_ATTEMPTS * max(SQL_COST, cache_cost):
        raise ValueError("Budget profile is not nonbinding for configured charges/horizon")
    return initial


def valid_result(record):
    """Validate a completed typed result; no claim of semantic correctness."""
    if (type(record) is not dict or record.get("complete") is not True or
            record.get("status") != "EXECUTED"):
        raise ValueError("Expected complete executed result")
    columns, rows = record.get("columns"), record.get("rows")
    if (type(columns) is not list or not columns or len(columns) > audit.LIMITS["max_columns"] or
            any(type(c) is not str for c in columns) or type(rows) is not list or
            len(rows) > audit.LIMITS["max_rows"]):
        raise ValueError("Invalid result shape")
    for row in rows:
        if type(row) is not list or len(row) != len(columns):
            raise ValueError("Invalid row width")
        for cell in row:
            if type(cell) is not list or len(cell) != 2:
                raise ValueError("Invalid typed cell")
            tag, value = cell
            if tag == "null":
                ok = value is None
            elif tag == "integer":
                ok = (type(value) is str and len(value) <= 20 and
                      re.fullmatch(r"0|-?[1-9][0-9]*", value) is not None and
                      -(2**63) <= int(value) < 2**63)
            elif tag == "real":
                try:
                    ok = (type(value) is str and math.isfinite(float.fromhex(value)) and
                          float.fromhex(value).hex() == value)
                except ValueError:
                    ok = False
            elif tag == "text":
                ok = type(value) is str
            elif tag == "blob":
                ok = type(value) is str and re.fullmatch(r"(?:[0-9a-f]{2})*", value) is not None
            else:
                ok = False
            if not ok:
                raise ValueError("Invalid typed value")
            expansion = 2 if tag == "blob" else 1
            if type(value) is str and len(value.encode("utf-8")) > expansion * audit.LIMITS["max_value_bytes"]:
                raise ValueError("Typed value too large")
    if len(audit.encoded(rows)) > audit.LIMITS["max_result_bytes"] + 2 * len(rows) + 2:
        raise ValueError("Result too large")
    computed = audit.fingerprints(rows)
    if any(record.get(k) != computed[k] for k in ("bag_sha256", "sequence_sha256")):
        raise ValueError("Result fingerprint mismatch")


class ToolSession:
    """Single-owner, synchronous environment; no thread-safe/concurrent use.

    Accepted executions are charged before accessing data. SQL failure/timeout
    retains that charge. Invalid requests, unavailable cache and insufficient
    budget are free rejections but consume an attempt. Calls are never retried.
    """
    def __init__(self, *, database_path, database_sha256, history_sql,
                 cached_result, input_receipt, output, materialized_available,
                 cache_read_cost, budget_profile=LEGACY_PROFILE):
        self.database = Path(database_path)
        if type(materialized_available) is not bool or type(cache_read_cost) is not int or cache_read_cost not in (1, 8):
            raise ValueError("Expected boolean availability and read cost 1 or 8")
        self.initial_budget = _profile_budget(budget_profile, cache_read_cost)
        self.budget_profile = budget_profile
        self.nonbinding_for_configured_horizon = budget_profile in (NONBINDING_PROFILE, RECOVERY_PROFILE, SHORT_RECOVERY_PROFILE)
        self.max_attempts = 3 if budget_profile == SHORT_RECOVERY_PROFILE else 21 if budget_profile == RECOVERY_PROFILE else MAX_ATTEMPTS
        if type(database_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", database_sha256) is None:
            raise ValueError("Invalid database fingerprint")
        if type(history_sql) is not str or not history_sql.strip():
            raise ValueError("Missing historical SQL")
        valid_result(cached_result)
        if cached_result.get("sql_sha256") != audit.digest(history_sql):
            raise ValueError("Cache not bound to historical SQL")
        self.expected_db = database_sha256
        self._check_database()
        self.output = Path(output)
        absent(self.output)
        self.available, self.cache_cost = materialized_available, cache_read_cost
        self.balance, self.attempts, self.sql_calls = self.initial_budget, 0, 0
        self.closed, self.invalidated = False, False
        self.cache = deepcopy(cached_result)
        self.output.mkdir(parents=True, exist_ok=False)
        audit.save_new(self.output / "started.json", {
            "version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "TOOL_ENVIRONMENT_ONLY_NO_INFERENCE_APPROVAL",
            "configuration": self.describe(), "input_receipt": deepcopy(input_receipt),
            "history_sql_sha256": audit.digest(history_sql),
            "cache_rows_sha256": cached_result["sequence_sha256"],
            "source_sha256": audit.hash_file(__file__),
            "readonly_source_sha256": audit.hash_file(audit.__file__),
            "python": sys.version, "limits": audit.LIMITS, "inference_eligible": False})
        shutil.copyfile(__file__, self.output / "tool_source.py")
        shutil.copyfile(audit.__file__, self.output / "readonly_source.py")

    def describe(self):
        """Public state only: never expose reference SQL, outcomes or review labels."""
        state = {"cache_id": "v000", "materialized_available": self.available,
                "read_cost": self.cache_cost, "sql_call_cost": SQL_COST,
                "budget_profile": self.budget_profile,
                "nonbinding_for_configured_horizon": self.nonbinding_for_configured_horizon,
                "initial_budget": self.initial_budget, "remaining_budget": self.balance,
                "max_attempts": self.max_attempts, "attempts_used": self.attempts,
                "cost_basis": "declared_relative_units", "database_sha256": self.expected_db}
        if self.budget_profile == RECOVERY_PROFILE:
            state.update(exploration_attempts=20, exploration_allowance=200,
                         recovery_allowance=10, last_attempt_sql_only=True)
        elif self.budget_profile == SHORT_RECOVERY_PROFILE:
            state.update(exploration_attempts=2, exploration_allowance=20,
                         recovery_allowance=10, last_attempt_sql_only=True)
        return state

    def _check_database(self):
        no_link_ancestors(self.database)
        if (not self.database.is_file() or
                any(Path(str(self.database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")) or
                audit.hash_file(self.database) != self.expected_db):
            raise ValueError("Database snapshot changed, missing or has sidecars")

    def _execute_sql(self, sql):
        """Operator-overridable backend; agent arguments cannot select it."""
        return audit.run_child(self.database, sql)

    def _classify(self, name, arguments):
        if type(name) is not str or type(arguments) is not dict:
            return "INVALID_REQUEST", 0
        if self.budget_profile == RECOVERY_PROFILE and self.attempts == 20 and name != "run_sql":
            return "RECOVERY_SQL_ONLY", 0
        if self.budget_profile == SHORT_RECOVERY_PROFILE and self.attempts == 2 and name != "run_sql":
            return "RECOVERY_SQL_ONLY", 0
        if name == "read_cache":
            if arguments != {"cache_id": "v000"}:
                return "INVALID_REQUEST", 0
            if not self.available:
                return "CACHE_UNAVAILABLE", 0
            cost = self.cache_cost
        elif name == "run_sql":
            sql = arguments.get("sql")
            if (set(arguments) != {"sql"} or type(sql) is not str or not sql.strip() or
                    len(sql.encode("utf-8")) > audit.LIMITS["max_sql_bytes"]):
                return "INVALID_REQUEST", 0
            cost = SQL_COST
        else:
            return "UNKNOWN_TOOL", 0
        return ("BUDGET_EXHAUSTED", 0) if cost > self.balance else ("ADMITTED", cost)

    def call(self, name, arguments):
        if self.closed or self.invalidated:
            raise RuntimeError("Session closed or invalidated; never silently resume")
        if self.attempts >= self.max_attempts:
            raise RuntimeError("Attempt limit reached; no additional calls")
        request = {"tool": name, "arguments": arguments}
        try:
            raw = audit.encoded(request)
        except (TypeError, ValueError, UnicodeError):
            request = {"unserializable_request": True}
            status, cost = "INVALID_REQUEST", 0
        else:
            if len(raw) > audit.LIMITS["max_sql_bytes"] * 6 + 1024:
                request = {"oversized_request_sha256": hashlib.sha256(raw).hexdigest()}
                status, cost = "INVALID_REQUEST", 0
            else:
                status, cost = self._classify(name, arguments)
        number = self.attempts + 1
        pending = {"attempt": number, "request": request, "admission": status,
                   "charge": cost, "balance_before": self.balance,
                   "balance_after": self.balance-cost, "automatic_retry": False}
        try:
            audit.save_new(self.output / f"attempt-{number:03d}.pending.json", pending)
        except BaseException:
            self.invalidated = True
            raise
        self.attempts, self.balance = number, self.balance-cost
        started = time.perf_counter()
        response = {"status": status, "complete": False, "charged_units": cost,
                    "remaining_budget": self.balance, "attempt": number}
        private = {}
        if status == "ADMITTED":
            try:
                self._check_database()
                if name == "read_cache":
                    result = deepcopy(self.cache)  # Never recompute a missing cache.
                else:
                    self.sql_calls += 1
                    result = self._execute_sql(arguments["sql"])
                private["backend_result"] = result
                self._check_database()
                if result.get("complete") is True:
                    valid_result(result)
                    response.update(status="EXECUTED", complete=True,
                                    columns=deepcopy(result["columns"]), rows=deepcopy(result["rows"]))
                else:
                    # Truncated/partial rows remain in private logs, not complete answers.
                    response.update(status=result.get("status", "BACKEND_ERROR"))
                    if result.get("status") in ("EXECUTION_ERROR", "DENIED") and type(result.get("error")) is str:
                        response["error"] = result["error"][:1024]
            except Exception as exc:
                self.invalidated = True
                private["exception_type"] = type(exc).__name__
                response.update(status="ENVIRONMENT_INVALID", complete=False)
            except BaseException:
                # A terminal interrupt preserves pending plus the original charge.
                self.invalidated = True
                raise
        elapsed = time.perf_counter()-started
        record = {**pending, "response": response, "private": private,
                  "environment_invalidated": self.invalidated,
                  "measured_action_wall_seconds": elapsed}
        try:
            audit.save_new(self.output / f"attempt-{number:03d}.completed.json", record)
        except BaseException:
            self.invalidated = True
            raise
        return deepcopy(response)

    def _resolved(self, number):
        """An existing, possibly partial file is not a completed journal record."""
        try:
            paths = [self.output / f"attempt-{number:03d}.{kind}.json"
                     for kind in ("pending", "completed")]
            for path in paths:
                no_link_ancestors(path)
            pending, completed = (read_json(path) for path in paths)
            keys = {"attempt", "request", "admission", "charge", "balance_before",
                    "balance_after", "automatic_retry"}
            if (type(pending) is not dict or set(pending) != keys or
                    type(completed) is not dict or not keys <= completed.keys() or
                    audit.encoded({k: completed[k] for k in keys}) != audit.encoded(pending)):
                return False
            response = completed.get("response")
            if (type(response) is not dict or type(response.get("complete")) is not bool or
                    type(response.get("status")) is not str or
                    type(completed.get("private")) is not dict or
                    type(completed.get("environment_invalidated")) is not bool or
                    type(pending["attempt"]) is not int or pending["attempt"] != number or
                    pending["automatic_retry"] is not False):
                return False
            for key in ("charge", "balance_before", "balance_after"):
                if type(pending[key]) is not int or not 0 <= pending[key] <= self.initial_budget:
                    return False
            if pending["balance_before"] - pending["balance_after"] != pending["charge"]:
                return False
            required = {"attempt": number, "charged_units": pending["charge"],
                        "remaining_budget": pending["balance_after"]}
            return (required.keys() <= response.keys() and
                    audit.encoded({k: response[k] for k in required}) == audit.encoded(required))
        except Exception:
            return False

    def close(self):
        """Seal first; a summary alone is not proof of completed finalization.

        Consumers must validate the full artifact manifest and reject any
        finalization_failure.json. Partial files are retained, never overwritten.
        """
        if self.closed:
            raise RuntimeError("Already closed")
        self.closed = True
        try:
            try:
                self._check_database()
            except Exception:
                self.invalidated = True
            unresolved = [i for i in range(1, self.attempts+1) if not self._resolved(i)]
            if unresolved:
                self.invalidated = True
            summary = {"status": "UNRESOLVED_ATTEMPTS" if unresolved else
                                 "ENVIRONMENT_INVALID" if self.invalidated else "TOOL_SESSION_CLOSED",
                       "unresolved_attempts": unresolved,
                       "attempts": self.attempts, "sql_calls": self.sql_calls,
                       "budget_profile": self.budget_profile, "initial_budget": self.initial_budget,
                       "nonbinding_for_configured_horizon": self.nonbinding_for_configured_horizon,
                       "spent_declared_units": self.initial_budget-self.balance, "remaining_budget": self.balance,
                       "model_calls_by_this_component": 0, "semantic_approvals": 0, "inference_eligible": False,
                       "finalization_requires": "Full verified artifact manifest and no finalization_failure.json",
                       "warnings": ["Not a model experiment or empirical performance score.",
                                    "Declared costs are not measured runtime or financial spend.",
                                    ("Every otherwise admitted action is affordable within the common attempt horizon; "
                                     "this does not guarantee task success or extra attempts."
                                     if self.nonbinding_for_configured_horizon else
                                     "Correct recomputation is affordable initially, not after arbitrary spending."),
                                    "Local SQLite defenses are not an OS security sandbox."]}
            audit.save_new(self.output / "summary.json", summary)
            audit.save_new(self.output / "artifact_hashes.json", {
                p.name: audit.hash_file(p) for p in sorted(self.output.iterdir()) if p.is_file()})
            return summary
        except BaseException as exc:
            self.invalidated = True
            try:
                audit.save_new(self.output / "finalization_failure.json", {
                    "error_type": type(exc).__name__, "closed": True,
                    "automatic_retry": False, "inference_eligible": False})
            except BaseException:
                pass  # Preserve the original failure even if the failure receipt cannot be written.
            raise
