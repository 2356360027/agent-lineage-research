"""Offline fixtures only; no GPU, model, SQL, SSH or HTTP calls."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from runtime import contract_interface_diagnostic as client
from scripts import run_contract_interface_server as server

ENDPOINT = "http://127.0.0.1:8000/v1"


def response():
    return {"model": client.MODEL, "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({field: "fixture only" for field in client.old.prompts.CONTRACT_FIELDS})}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8}}


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = client.load_plan()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        for target in ("socket.socket", "socket.create_connection", "subprocess.Popen", "sqlite3.connect",
                       "urllib.request.build_opener"):
            self.stack.enter_context(patch(target, side_effect=AssertionError("No real execution: " + target)))

    def run_fixture(self, answers=None, clock=lambda: 0):
        answers = [response() for _ in range(12)] if answers is None else answers
        observed = []
        def transport(path, endpoint, method, timeout, request):
            observed.append(deepcopy(request))
            path.mkdir()
            client.old.save_new(path / "request.json", request)
            answer = answers[len(observed) - 1]
            if isinstance(answer, Exception):
                client.old.save_new(path / "failed-fixture.json", {"failed": True})
                raise answer
            client.old.save_new(path / "response.raw", answer)
            return answer
        summary = client.run_calls(deepcopy(self.plan), self.tmp, ENDPOINT, transport=transport, clock=clock)
        return summary, observed

    def test_success_exact_payloads_and_structural_counts(self):
        summary, requests = self.run_fixture()
        self.assertEqual(requests, [c["request"] for c in self.plan["calls"]])
        self.assertEqual(summary["status"], "COMPLETE_UNREVIEWED")
        self.assertEqual(summary["format_valid"], 12)
        self.assertEqual(summary["reported_prompt_tokens"], 120)
        self.assertEqual(summary["reported_completion_tokens"], 96)
        self.assertFalse(summary["semantic_scoring_performed"])
        self.assertEqual(summary["human_approvals"], 0)
        self.assertEqual(len(list(self.tmp.glob("assessment-*.json"))), 12)

    def test_format_invalid_kept_without_replacement(self):
        answers = [response() for _ in range(12)]
        answers[3]["choices"][0]["finish_reason"] = "length"
        summary, requests = self.run_fixture(answers)
        self.assertEqual(len(requests), 12)
        self.assertEqual(summary["format_invalid"], 1)
        self.assertEqual(summary["format_valid"], 11)
        self.assertTrue((self.tmp / "call-003/response.raw").exists())

    def test_transport_failure_stops_preserves_fixed_denominator(self):
        summary, requests = self.run_fixture([RuntimeError("fixture transport failure")])
        self.assertEqual(len(requests), 1)
        self.assertEqual(summary["transport_failed"], 1)
        self.assertEqual(summary["unattempted_calls"], 11)
        self.assertEqual(summary["status"], "INCOMPLETE_UNREVIEWED")
        self.assertTrue((self.tmp / "call-000/failed-fixture.json").exists())

    def test_model_mismatch_stops(self):
        wrong = response(); wrong["model"] = "wrong-model"
        summary, requests = self.run_fixture([wrong])
        self.assertEqual(len(requests), 1)
        self.assertEqual(summary["transport_failed"], 1)

    def test_deadline_before_any_call(self):
        times = iter([0, 601, 601])
        summary, requests = self.run_fixture(clock=lambda: next(times))
        self.assertEqual(requests, [])
        self.assertEqual(summary["unattempted_calls"], 12)
        self.assertEqual(summary["stop_reason"], "CLIENT_DEADLINE")

    def test_no_resume_or_remote_endpoint(self):
        (self.tmp / "call-000").mkdir()
        transport = Mock()
        with self.assertRaises(Exception):
            client.run_calls(self.plan, self.tmp, ENDPOINT, transport=transport)
        with self.assertRaises(ValueError):
            client.run_calls(self.plan, self.tmp, "https://example.invalid/v1", transport=transport)
        transport.assert_not_called()

    def test_plan_change_rejected_before_dispatch(self):
        plan = deepcopy(self.plan)
        plan["calls"][0]["request"]["messages"][0]["content"] += "changed"
        transport = Mock()
        with self.assertRaises(Exception):
            client.run_calls(plan, self.tmp, ENDPOINT, transport=transport)
        transport.assert_not_called()

    def test_usage_missing_not_imputed_as_complete(self):
        answers = [response() for _ in range(12)]
        answers[0].pop("usage")
        summary, _ = self.run_fixture(answers)
        self.assertEqual(summary["calls_with_reported_usage"], 11)
        self.assertFalse(summary["usage_complete_for_attempts"])

    def test_native_reference_changes_rejected(self):
        config = {"deployment": "native", "model": client.MODEL, "model_path": str(self.tmp),
                  "model_files": {}, "model_content_sha256": client.MODEL_SHA, "packages": client.PACKAGES}
        path = self.tmp / "native.json"
        client.old.save_new(path, config)
        with self.assertRaisesRegex(Exception, "content reference"):
            client.read_native(path)

    def test_execute_preflight_failure_archives_sources_and_failure(self):
        from runtime import native
        startup = self.tmp / "startup.log"; startup.write_text("fixture startup")
        output = self.tmp / "run"
        config = {"model": client.MODEL, "model_path": str(self.tmp)}
        with patch.object(client, "read_native", return_value=config), patch.object(
                native, "verify", side_effect=ValueError("fixture mismatch")), patch.object(client.old, "request_once") as request:
            with self.assertRaises(ValueError):
                client.execute(client.DEFAULT_PLAN, output, self.tmp / "dummy", startup, ENDPOINT)
            request.assert_not_called()
        self.assertTrue((output / "execution_failure.json").exists())
        hashes = json.loads((output / "artifact_hashes.json").read_text())
        for name in ("execution_failure.json", "source/runtime/contract_interface_diagnostic.py",
                     "source/runtime/cache_intent_contract_pilot.py", "plan.json"):
            self.assertEqual(hashes[name], client.old.sha((output / name).read_bytes()))

    def test_main_default_cannot_dispatch_and_execute_requires_ack(self):
        with patch.object(sys, "argv", ["client"]), patch.object(client, "execute") as execute:
            client.main()
            execute.assert_not_called()
        with patch.object(sys, "argv", ["client", "--execute"]), redirect_stdout(io.StringIO()), patch(
                "sys.stderr", io.StringIO()), patch.object(client, "execute") as execute:
            with self.assertRaises(SystemExit): client.main()
            execute.assert_not_called()


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.tmp / "model.safetensors.index.json").write_text("{}")
        self.output = self.tmp / "new-run"
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(server.sys, "platform", "linux"))
        self.stack.enter_context(patch.object(server.diagnostic, "load_plan", return_value={"fixture": True}))
        self.stack.enter_context(patch.object(server, "verify_source_manifest", return_value={"fixture": True}))
        self.stack.enter_context(patch.object(server.diagnostic, "read_native", return_value={
            "model_path": str(self.tmp), "model_content_sha256": "fixture", "packages": {}}))
        self.idle = self.stack.enter_context(patch.object(server.helpers, "require_idle"))
        self.stop = self.stack.enter_context(patch.object(server.helpers, "stop_owned_group"))
        self.stack.enter_context(patch.object(server.subprocess, "check_output", return_value=b"fixture GPU"))
        self.stack.enter_context(patch.object(server.helpers, "read_models", return_value={"data": [
            {"id": client.MODEL, "root": str(self.tmp)}]}))
        self.stack.enter_context(patch.object(server.helpers, "child_environment", return_value={}))
        self.stack.enter_context(patch("socket.socket", side_effect=AssertionError("No network")))
        self.stack.enter_context(patch("urllib.request.build_opener", side_effect=AssertionError("No HTTP")))
        self.stack.enter_context(patch("sqlite3.connect", side_effect=AssertionError("No SQL")))
        self.children = [Mock(pid=100+i, **{"wait.return_value": 0, "poll.return_value": None}) for i in range(3)]
        self.popen = self.stack.enter_context(patch.object(server.subprocess, "Popen", side_effect=self.children))

    def execute(self):
        return server.execute(self.tmp / "plan.json", self.tmp / "native.json", self.output)

    def test_success_stops_only_owned_children_and_archives(self):
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.popen.call_count, 3)
        self.assertEqual([c.args[0] for c in self.stop.call_args_list],
                         [self.children[0], self.children[2], self.children[1]])
        for call in self.popen.call_args_list:
            self.assertIs(call.kwargs["start_new_session"], True)
            self.assertNotIn("shell", call.kwargs)
        self.assertIn("runtime.contract_interface_diagnostic", self.popen.call_args_list[-1].args[0])
        state = json.loads((self.output / "finished.json").read_text())
        self.assertEqual(state["limit_seconds"], 600)
        self.assertEqual(state["maximum_inference_calls"], 12)
        self.assertTrue(state["cloud_instance_still_billed"])
        self.assertTrue(self.output.with_suffix(".zip").exists())

    def test_occupied_service_refused_without_popen(self):
        self.idle.side_effect = RuntimeError("occupied")
        with self.assertRaises(RuntimeError): self.execute()
        self.popen.assert_not_called(); self.stop.assert_not_called()
        self.assertTrue((self.output / "finished.json").exists())

    def test_timeout_cleans_and_does_not_retry(self):
        self.children[2].wait.side_effect = subprocess.TimeoutExpired("fixture", 600)
        with self.assertRaises(subprocess.TimeoutExpired): self.execute()
        self.assertEqual(self.popen.call_count, 3)
        state = json.loads((self.output / "finished.json").read_text())
        self.assertTrue(state["uncertain_inflight_calls_must_not_be_retried"])
        self.assertTrue(self.output.with_suffix(".archive.json").exists())

    def test_model_verification_failure_never_starts_server(self):
        self.children[0].wait.return_value = 1
        with self.assertRaises(Exception): self.execute()
        self.assertEqual(self.popen.call_count, 1)

    def test_existing_output_and_windows_refused(self):
        self.output.mkdir()
        with self.assertRaises(Exception): self.execute()
        self.popen.assert_not_called()
        with patch.object(server.sys, "platform", "win32"):
            with self.assertRaises(Exception): self.execute()

    def test_default_supervisor_cannot_start_and_requires_ack(self):
        with patch.object(sys, "argv", ["supervisor"]): server.main()
        self.popen.assert_not_called()
        with patch.object(sys, "argv", ["supervisor", "--execute"]), patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit): server.main()
        self.popen.assert_not_called()


class BundleTests(unittest.TestCase):
    def test_private_bundle_members_hashes_and_no_overwrite(self):
        import zipfile
        from scripts import build_contract_interface_bundle as builder
        with tempfile.TemporaryDirectory() as temp, patch("subprocess.Popen", side_effect=AssertionError("offline")), patch(
                "socket.socket", side_effect=AssertionError("offline")):
            root = Path(temp)
            archive = root / "bundle.zip"
            result = builder.build(archive)
            self.assertEqual(result["executed_calls"], 0)
            self.assertEqual(result["sha256"], client.old.sha(archive.read_bytes()))
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(len(bundle.namelist()), 16)
                # The builder uses fixed, allowlisted relative members, no links.
                for info in bundle.infolist():
                    self.assertTrue(info.filename.startswith(builder.PREFIX + "/"))
                    self.assertNotIn("..", Path(info.filename).parts)
                    self.assertEqual(info.external_attr >> 16, 0o100600)
                bundle.extractall(root / "extract")
            base = root / "extract" / builder.PREFIX
            server.verify_source_manifest(base)
            with self.assertRaises(Exception): builder.build(archive)
            (base / "runtime/contract_interface_diagnostic.py").write_text("changed")
            with self.assertRaises(Exception): server.verify_source_manifest(base)


if __name__ == "__main__":
    unittest.main()
