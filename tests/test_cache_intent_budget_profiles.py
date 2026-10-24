"""Budget-profile software tests, not research experiments or financial spend.

The database fixture is deliberately not a SQLite database. All backend calls
are mocked; guards reject SQLite connections and subprocess execution. Temporary
files contain synthetic receipts only. No model, network, or research DB is used.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from runtime import cache_intent_tools as tools


LEGACY = "legacy-100-v1"
NONBINDING = "nonbinding-200-v1"


def synthetic_result():
    rows = [[["integer", "1"]], [["integer", "2"]]]
    return {"status": "EXECUTED", "complete": True, "columns": ["synthetic_id"],
            "rows": rows, **tools.audit.fingerprints(rows)}


def read_receipt(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class BudgetProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "synthetic-not-a-database.mockdb"
        self.database.write_bytes(b"SOFTWARE FIXTURE: NOT SQLITE; MUST NEVER BE EXECUTED")
        self.history_sql = "SELECT synthetic_id FROM synthetic_fixture"
        self.cache = synthetic_result()
        self.cache["sql_sha256"] = tools.audit.digest(self.history_sql)
        self.inputs = {"database_path": self.database,
                       "database_sha256": tools.audit.hash_file(self.database),
                       "history_sql": self.history_sql, "cached_result": self.cache,
                       "input_receipt": {"software_fixture_only": True,
                                         "semantic_approvals": 0}}
        self.serial = 0
        backend = mock.patch.object(tools.audit, "run_child",
                                    side_effect=lambda *a, **k: deepcopy(self.cache))
        self.child = backend.start(); self.addCleanup(backend.stop)
        sqlite_guard = mock.patch.object(tools.audit.sqlite3, "connect",
                                        side_effect=AssertionError("Real SQLite is prohibited in this test"))
        self.sqlite_guard = sqlite_guard.start(); self.addCleanup(sqlite_guard.stop)
        process_guard = mock.patch.object(tools.audit.subprocess, "run",
                                         side_effect=AssertionError("Subprocesses are prohibited in this test"))
        self.process_guard = process_guard.start(); self.addCleanup(process_guard.stop)

    def tearDown(self):
        self.sqlite_guard.assert_not_called()
        self.process_guard.assert_not_called()

    def session(self, *, cost=1, available=True, **overrides):
        self.serial += 1
        arguments = self.inputs | {"output": self.root / f"session-{self.serial}",
                                   "materialized_available": available,
                                   "cache_read_cost": cost} | overrides
        return tools.ToolSession(**arguments)

    def action(self, session, name):
        arguments = {"cache_id": "v000"} if name == "read_cache" else {"sql": self.history_sql}
        return session.call(name, arguments)

    def assert_journal(self, session, number, charge, before, after):
        pending = read_receipt(session.output / f"attempt-{number:03d}.pending.json")
        completed = read_receipt(session.output / f"attempt-{number:03d}.completed.json")
        self.assertEqual(pending["attempt"], number)
        self.assertEqual((pending["charge"], pending["balance_before"], pending["balance_after"]),
                         (charge, before, after))
        self.assertFalse(pending["automatic_retry"])
        for key, value in pending.items():
            self.assertEqual(completed[key], value)
        self.assertEqual(completed["response"]["charged_units"], charge)
        self.assertEqual(completed["response"]["remaining_budget"], after)
        return completed

    def assert_manifest(self, session):
        manifest = read_receipt(session.output / "artifact_hashes.json")
        files = {p.name for p in session.output.iterdir()
                 if p.is_file() and p.name != "artifact_hashes.json"}
        self.assertEqual(set(manifest), files)
        for name, expected in manifest.items():
            self.assertEqual(tools.audit.hash_file(session.output / name), expected)
        self.assertNotIn("finalization_failure.json", files)

    def test_default_and_explicit_legacy_preserve_original_budget(self):
        default, explicit = self.session(), self.session(budget_profile=LEGACY)
        self.assertEqual(default.describe(), explicit.describe())
        for session in (default, explicit):
            self.assertEqual(session.describe()["budget_profile"], LEGACY)
            self.assertEqual(session.describe()["initial_budget"], 100)
            self.assertEqual(session.balance, 100)
            summary = session.close()
            self.assertEqual(summary["budget_profile"], LEGACY)
            self.assertEqual(summary["initial_budget"], 100)
            self.assertIs(summary["nonbinding_for_configured_horizon"], False)
            self.assertEqual(summary["spent_declared_units"], 0)
            self.assert_manifest(session)
        self.child.assert_not_called()

    def test_nonbinding_metadata_is_recorded_before_any_action(self):
        session = self.session(budget_profile=NONBINDING)
        state = session.describe()
        self.assertEqual(state["budget_profile"], NONBINDING)
        self.assertEqual((state["initial_budget"], state["remaining_budget"]), (200, 200))
        self.assertEqual((state["max_attempts"], state["sql_call_cost"]), (20, 10))
        self.assertEqual(state["cost_basis"], "declared_relative_units")
        self.assertEqual(read_receipt(session.output / "started.json")["configuration"], state)
        summary = session.close()
        self.assertEqual((summary["budget_profile"], summary["initial_budget"]), (NONBINDING, 200))
        self.assertIs(summary["nonbinding_for_configured_horizon"], True)
        self.assertEqual((summary["spent_declared_units"], summary["remaining_budget"]), (0, 200))
        self.assertFalse(summary["inference_eligible"])
        self.assertEqual(summary["model_calls_by_this_component"], 0)
        self.assert_manifest(session)

    def test_unknown_nonstring_and_noncanonical_profile_names_fail_before_output(self):
        class StringSubclass(str):
            pass
        profiles = ("", " ", "nonbinding-201-v1", "NONBINDING-200-V1", " nonbinding-200-v1",
                    "nonbinding-200-v1 ", "legacy-100-v2", 200, 100.0, True, None, [], {},
                    StringSubclass(NONBINDING))
        for index, profile in enumerate(profiles):
            output = self.root / f"invalid-profile-{index}"
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                self.session(output=output, budget_profile=profile)
            self.assertFalse(output.exists())
        self.child.assert_not_called()

    def test_numeric_budget_cost_or_horizon_overrides_are_not_constructor_api(self):
        for field, value in (("budget", 1000), ("initial_budget", 1000),
                             ("max_attempts", 1000), ("sql_call_cost", 0)):
            output = self.root / f"override-{field}"
            with self.subTest(field=field), self.assertRaises(TypeError):
                self.session(output=output, budget_profile=NONBINDING, **{field: value})
            self.assertFalse(output.exists())
        self.child.assert_not_called()

    def test_future_cost_or_attempt_increase_invalidates_fixed_nonbinding_profile(self):
        for field, value in (("SQL_COST", 11), ("MAX_ATTEMPTS", 21)):
            for cost in (1, 8):
                output = self.root / f"future-{field}-{cost}"
                with self.subTest(field=field, cost=cost), mock.patch.object(tools, field, value), \
                        self.assertRaises(ValueError):
                    self.session(output=output, cost=cost, budget_profile=NONBINDING)
                self.assertFalse(output.exists())
        self.child.assert_not_called()

    def test_legacy_twelve_expensive_reads_still_block_subsequent_sql(self):
        for explicit in (False, True):
            session = self.session(cost=8, **({"budget_profile": LEGACY} if explicit else {}))
            for _ in range(12):
                self.assertEqual(self.action(session, "read_cache")["status"], "EXECUTED")
            response = self.action(session, "run_sql")
            self.assertEqual((response["status"], response["charged_units"], response["remaining_budget"]),
                             ("BUDGET_EXHAUSTED", 0, 4))
            self.assert_journal(session, 13, 0, 4, 4)
            summary = session.close()
            self.assertEqual((summary["attempts"], summary["spent_declared_units"]), (13, 96))
            self.assertEqual(summary["status"], "TOOL_SESSION_CLOSED")
            self.assertIs(summary["nonbinding_for_configured_horizon"], False)
        self.child.assert_not_called()

    def test_nonbinding_twenty_action_sequences_are_admitted_at_both_read_prices(self):
        sequences = {
            "twenty_sql": ["run_sql"] * 20,
            "nineteen_sql_then_read": ["run_sql"] * 19 + ["read_cache"],
            "nineteen_reads_then_sql": ["read_cache"] * 19 + ["run_sql"],
            "twenty_reads": ["read_cache"] * 20,
            "alternating": ["read_cache", "run_sql"] * 10,
        }
        for cost in (1, 8):
            for label, sequence in sequences.items():
                with self.subTest(cost=cost, sequence=label):
                    session = self.session(cost=cost, budget_profile=NONBINDING)
                    remaining = 200
                    calls_before = self.child.call_count
                    for index, name in enumerate(sequence, 1):
                        charge = 10 if name == "run_sql" else cost
                        before = remaining; remaining -= charge
                        response = self.action(session, name)
                        self.assertEqual(response["status"], "EXECUTED")
                        self.assertEqual(response["charged_units"], charge)
                        self.assertEqual(response["remaining_budget"], remaining)
                        self.assert_journal(session, index, charge, before, remaining)
                    self.assertEqual(self.child.call_count - calls_before, sequence.count("run_sql"))
                    with self.assertRaisesRegex(RuntimeError, "limit"):
                        self.action(session, "run_sql")
                    self.assertFalse((session.output / "attempt-021.pending.json").exists())
                    summary = session.close()
                    self.assertEqual(summary["status"], "TOOL_SESSION_CLOSED")
                    self.assertEqual(summary["unresolved_attempts"], [])
                    self.assertEqual(summary["attempts"], 20)
                    self.assertEqual(summary["sql_calls"], sequence.count("run_sql"))
                    self.assertEqual(summary["spent_declared_units"], 200 - remaining)
                    self.assertEqual(summary["remaining_budget"], remaining)
                    self.assertIs(summary["nonbinding_for_configured_horizon"], True)
                    self.assert_manifest(session)

    def test_failed_or_timed_out_sql_is_charged_through_the_twentieth_call(self):
        statuses = ("EXECUTION_ERROR", "HARD_TIMEOUT", "DENIED", "WORKER_ERROR")
        for cost in (1, 8):
            with self.subTest(read_cost=cost):
                session = self.session(cost=cost, budget_profile=NONBINDING)
                calls_before = self.child.call_count
                for number in range(1, 21):
                    status = statuses[(number - 1) % len(statuses)]
                    self.child.side_effect = lambda *a, status=status, **k: {
                        "status": status, "complete": False, "error": "SYNTHETIC FAILURE ONLY"}
                    response = self.action(session, "run_sql")
                    self.assertEqual(response["status"], status)
                    self.assertFalse(response["complete"])
                    self.assertNotIn("rows", response)
                    self.assertEqual(response["remaining_budget"], 200 - 10 * number)
                    self.assert_journal(session, number, 10, 210 - 10 * number, 200 - 10 * number)
                self.assertEqual(self.child.call_count - calls_before, 20)
                summary = session.close()
                self.assertEqual((summary["spent_declared_units"], summary["remaining_budget"]), (200, 0))
                self.assertEqual(summary["status"], "TOOL_SESSION_CLOSED")
                self.assert_manifest(session)

    def test_invalid_requests_are_free_but_consume_entire_nonbinding_horizon(self):
        session = self.session(budget_profile=NONBINDING)
        requests = [("unknown", {}), ("read_cache", {"cache_id": "v000", "budget_profile": LEGACY}),
                    ("run_sql", {"sql": self.history_sql, "budget": 1000}),
                    ("read_cache", {"cache_id": "v000", "cost": 0})]
        for index in range(20):
            name, arguments = requests[index % len(requests)]
            response = session.call(name, arguments)
            self.assertIn(response["status"], ("UNKNOWN_TOOL", "INVALID_REQUEST"))
            self.assertEqual((response["charged_units"], response["remaining_budget"]), (0, 200))
            self.assert_journal(session, index + 1, 0, 200, 200)
        with self.assertRaisesRegex(RuntimeError, "limit"):
            self.action(session, "run_sql")
        self.child.assert_not_called()
        summary = session.close()
        self.assertEqual((summary["attempts"], summary["spent_declared_units"]), (20, 0))
        self.assertEqual(summary["status"], "TOOL_SESSION_CLOSED")
        self.assert_manifest(session)

    def test_nineteen_invalid_attempts_still_allow_twentieth_sql(self):
        session = self.session(budget_profile=NONBINDING)
        for _ in range(19):
            session.call("unknown", {})
        response = self.action(session, "run_sql")
        self.assertEqual((response["status"], response["remaining_budget"]), ("EXECUTED", 190))
        self.assert_journal(session, 20, 10, 200, 190)
        self.child.assert_called_once_with(self.database, self.history_sql)
        self.assertEqual(session.close()["status"], "TOOL_SESSION_CLOSED")

    def test_availability_is_independent_of_profile_and_read_cost(self):
        for profile, budget in ((LEGACY, 100), (NONBINDING, 200)):
            for cost in (1, 8):
                for available in (False, True):
                    with self.subTest(profile=profile, cost=cost, available=available):
                        session = self.session(cost=cost, available=available, budget_profile=profile)
                        cached = self.action(session, "read_cache")
                        charge = cost if available else 0
                        self.assertEqual(cached["status"], "EXECUTED" if available else "CACHE_UNAVAILABLE")
                        self.assertEqual((cached["charged_units"], cached["remaining_budget"]),
                                         (charge, budget - charge))
                        if not available:
                            self.assertNotIn("rows", cached)
                        recomputed = self.action(session, "run_sql")
                        self.assertEqual(recomputed["status"], "EXECUTED")
                        self.assertEqual(recomputed["remaining_budget"], budget - charge - 10)
                        state = session.describe()
                        self.assertIs(state["materialized_available"], available)
                        self.assertEqual(state["budget_profile"], profile)
                        self.assertEqual(session.close()["spent_declared_units"], charge + 10)

    def test_initial_cheap_and_expensive_arms_differ_only_in_read_cost(self):
        for available in (False, True):
            cheap = self.session(cost=1, available=available, budget_profile=NONBINDING)
            expensive = self.session(cost=8, available=available, budget_profile=NONBINDING)
            low, high = cheap.describe(), expensive.describe()
            self.assertEqual(low["initial_budget"], high["initial_budget"])
            self.assertEqual(low["initial_budget"], 200)
            self.assertEqual(low["read_cost"], 1)
            self.assertEqual(high["read_cost"], 8)
            self.assertEqual({k: v for k, v in low.items() if k != "read_cost"},
                             {k: v for k, v in high.items() if k != "read_cost"})
            for session, state in ((cheap, low), (expensive, high)):
                self.assertEqual(read_receipt(session.output / "started.json")["configuration"], state)
                self.assertEqual(session.close()["status"], "TOOL_SESSION_CLOSED")
        self.child.assert_not_called()

    def test_failed_pending_write_preserves_nonbinding_profile_without_charge(self):
        session = self.session(budget_profile=NONBINDING)
        save = tools.audit.save_new
        def fail_pending(path, value):
            if str(path).endswith(".pending.json"):
                raise OSError("SYNTHETIC PENDING FAILURE")
            return save(path, value)
        with mock.patch.object(tools.audit, "save_new", side_effect=fail_pending), self.assertRaises(OSError):
            self.action(session, "run_sql")
        self.assertEqual((session.attempts, session.balance), (0, 200))
        self.assertTrue(session.invalidated)
        self.child.assert_not_called()
        self.assertEqual(session.describe()["budget_profile"], NONBINDING)
        summary = session.close()
        self.assertEqual((summary["budget_profile"], summary["initial_budget"]), (NONBINDING, 200))
        self.assertEqual((summary["spent_declared_units"], summary["remaining_budget"]), (0, 200))
        self.assertEqual(summary["status"], "ENVIRONMENT_INVALID")
        self.assert_manifest(session)

    def test_interrupted_backend_retains_charge_and_profile_without_resume(self):
        session = self.session(budget_profile=NONBINDING)
        self.child.side_effect = KeyboardInterrupt("SYNTHETIC INTERRUPT")
        with self.assertRaises(KeyboardInterrupt):
            self.action(session, "run_sql")
        pending = read_receipt(session.output / "attempt-001.pending.json")
        self.assertEqual((pending["balance_before"], pending["balance_after"], pending["charge"]),
                         (200, 190, 10))
        with self.assertRaises(RuntimeError):
            self.action(session, "run_sql")
        self.child.assert_called_once()
        summary = session.close()
        self.assertEqual(summary["status"], "UNRESOLVED_ATTEMPTS")
        self.assertEqual(summary["unresolved_attempts"], [1])
        self.assertEqual((summary["budget_profile"], summary["initial_budget"]), (NONBINDING, 200))
        self.assertEqual((summary["spent_declared_units"], summary["remaining_budget"]), (10, 190))
        self.assertIs(summary["nonbinding_for_configured_horizon"], True)
        self.assert_manifest(session)


if __name__ == "__main__":
    unittest.main()
