"""Offline deployment safety tests; no real server, model, network, or GPU.

Temporary-file receipts are real. Linux detection, process handles, process-group
signals, readiness failures, and frozen-plan values are deliberately mocked.
These tests are not evidence that vLLM starts or that model inference succeeds.
"""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch
import zipfile

from scripts import run_contract_pilot_server as pilot


class ServerPilotTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        # Fail closed if a test unexpectedly crosses an execution boundary.
        self.popen = self.stack.enter_context(patch.object(
            pilot.subprocess, "Popen", side_effect=AssertionError("No subprocess in offline test")))
        self.check_output = self.stack.enter_context(patch.object(
            pilot.subprocess, "check_output", side_effect=AssertionError("No GPU command in offline test")))
        self.stack.enter_context(patch.object(
            pilot.socket, "socket", side_effect=AssertionError("No network in offline test")))
        self.stack.enter_context(patch.object(
            pilot.urllib.request, "build_opener", side_effect=AssertionError("No HTTP in offline test")))
        self.killpg = self.stack.enter_context(patch.object(
            pilot.os, "killpg", create=True,
            side_effect=AssertionError("No actual process-group signal in offline test")))
        self.stack.enter_context(patch.object(
            pilot.signal, "signal", side_effect=AssertionError("No actual signal registration")))
        self.stack.enter_context(patch.object(pilot.signal, "SIGKILL", 9, create=True))
        self.stack.enter_context(patch.object(pilot.time, "sleep"))

    def manifest(self, entries):
        value = {"files": entries}
        (self.tmp / "DEPLOY_MANIFEST.json").write_bytes(pilot.encoded(value))
        return value

    def test_verify_bundle_accepts_matching_local_content_and_fixed_plan(self):
        raw = b"frozen test source\n"
        (self.tmp / "source.py").write_bytes(raw)
        expected = self.manifest({"source.py": {"bytes": len(raw), "sha256": pilot.sha(raw)}})
        plan = {"fixed": True, "calls": 24}
        with patch.object(pilot, "load_plan", return_value=plan), patch.object(
                pilot, "PLAN_SHA", pilot.sha(pilot.encoded(plan))):
            self.assertEqual(pilot.verify_bundle(self.tmp), expected)

    def test_verify_bundle_rejects_unsafe_members_before_reading_them(self):
        for name in ("../outside", "/absolute", "nested/../../outside", "nested\\file", "C:/file"):
            with self.subTest(name=name):
                self.manifest({name: {"bytes": 0, "sha256": pilot.sha(b"")}})
                with patch.object(pilot, "load_plan") as load_plan:
                    with self.assertRaisesRegex(ValueError, "Unsafe bundle member"):
                        pilot.verify_bundle(self.tmp)
                    load_plan.assert_not_called()

    def test_verify_bundle_rejects_hash_length_and_plan_changes(self):
        raw = b"original"
        path = self.tmp / "source.py"
        self.manifest({"source.py": {"bytes": len(raw), "sha256": pilot.sha(raw)}})
        for replacement in (b"modified", b"short"):
            with self.subTest(replacement=replacement):
                path.write_bytes(replacement)
                with self.assertRaisesRegex(ValueError, "Bundle content changed"):
                    pilot.verify_bundle(self.tmp)
        path.write_bytes(raw)
        with patch.object(pilot, "load_plan", return_value={"different": True}), patch.object(
                pilot, "PLAN_SHA", "0" * 64):
            with self.assertRaisesRegex(ValueError, "Fixed experiment plan changed"):
                pilot.verify_bundle(self.tmp)

    def test_child_environment_disables_online_download_and_telemetry(self):
        keys = ("OPENAI_API_KEY", "VLLM_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
                "PYTHONPATH", "PYTHONHOME")
        ambient = {key: "test-only-not-a-credential" for key in keys}
        ambient.update(PATH="test-path", HTTP_PROXY="http://example.invalid:1")
        with patch.dict(os.environ, ambient, clear=True):
            env = pilot.child_environment()
            for key in keys:
                self.assertNotIn(key, env)
                self.assertIn(key, os.environ, "Do not mutate the caller environment")
            for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY",
                        "VLLM_NO_USAGE_STATS", "DO_NOT_TRACK", "PYTHONDONTWRITEBYTECODE",
                        "PYTHONUNBUFFERED"):
                self.assertEqual(env[key], "1")
            self.assertEqual(env["PATH"], "test-path")
            # Proxy removal is not claimed; client HTTP independently disables proxies.

    def test_serve_argv_keeps_local_model_and_loopback_without_shell(self):
        model = "/gemini/code/model-cache/test-local-model"
        argv = pilot.serve_command(model)
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[:3], [pilot.sys.executable, "-m", "vllm.entrypoints.openai.api_server"])
        self.assertEqual(argv[argv.index("--model") + 1], model)
        self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")
        self.assertEqual(argv[argv.index("--port") + 1], "8000")
        self.assertEqual(argv[argv.index("--served-model-name") + 1], "Qwen/Qwen2.5-7B-Instruct")
        self.assertNotIn("--trust-remote-code", argv)
        self.assertNotIn("--api-key", argv)
        self.assertIn("--enforce-eager", argv)
        self.popen.assert_not_called()

    def test_stop_owned_group_targets_only_supplied_child_group(self):
        child = Mock(pid=76543)
        self.killpg.side_effect = None
        pilot.stop_owned_group(child)
        self.assertEqual(self.killpg.call_args_list,
                         [call(76543, pilot.signal.SIGTERM), call(76543, pilot.signal.SIGKILL)])
        child.wait.assert_called_once_with(timeout=10)
        self.check_output.assert_not_called()
        self.popen.assert_not_called()

    def test_stop_owned_group_already_gone_still_reaps_child(self):
        child = Mock(pid=76544)
        self.killpg.side_effect = ProcessLookupError
        pilot.stop_owned_group(child)
        self.killpg.assert_called_once_with(76544, pilot.signal.SIGTERM)
        child.wait.assert_called_once_with(timeout=10)

    def test_archive_hashes_every_original_file_and_keeps_nested_paths(self):
        work = self.tmp / "attempt"
        (work / "experiment" / "calls").mkdir(parents=True)
        originals = {"started.json": b'{"status":"test-only"}',
                     "experiment/calls/response.raw": b"unfavorable or invalid response\x00\xff",
                     "server.log": b"offline fixture\n"}
        for name, raw in originals.items():
            (work / name).write_bytes(raw)
        with redirect_stdout(io.StringIO()):
            pilot.archive(work)
        hashes = json.loads((work / "SUPERVISOR_HASHES.json").read_text("utf-8"))
        self.assertEqual(hashes, {name: hashlib.sha256(raw).hexdigest()
                                  for name, raw in originals.items()})
        with zipfile.ZipFile(work.with_suffix(".zip")) as saved:
            self.assertEqual(set(saved.namelist()),
                             {f"attempt/{name}" for name in originals} | {"attempt/SUPERVISOR_HASHES.json"})
            for name, raw in originals.items():
                self.assertEqual(saved.read(f"attempt/{name}"), raw)
        archive_bytes = work.with_suffix(".zip").read_bytes()
        receipt = json.loads(work.with_suffix(".archive.json").read_text("utf-8"))
        self.assertEqual(receipt["sha256"], hashlib.sha256(archive_bytes).hexdigest())
        self.assertEqual(receipt["bytes"], len(archive_bytes))

    def test_archive_never_overwrites_existing_zip(self):
        work = self.tmp / "prior"
        work.mkdir()
        (work / "response.raw").write_bytes(b"new response")
        destination = work.with_suffix(".zip")
        destination.write_bytes(b"previous archive must survive")
        with self.assertRaises(FileExistsError):
            pilot.archive(work)
        self.assertEqual(destination.read_bytes(), b"previous archive must survive")
        self.assertFalse(work.with_suffix(".archive.json").exists())

    def test_failed_idle_preflight_preserves_receipts_and_archive_without_launch(self):
        root = self.tmp / "bundle"
        root.mkdir()
        reference = {"model": "Qwen/Qwen2.5-7B-Instruct", "model_content_sha256": "test-only",
                     "packages": {}, "model_path": "replaced-with-local-path"}
        (root / "EXPECTED_MODEL.json").write_bytes(pilot.encoded(reference))
        (root / "DEPLOY_MANIFEST.json").write_bytes(pilot.encoded({"files": {}}))
        model = self.tmp / "local-model"
        model.mkdir()
        (model / "model.safetensors.index.json").write_bytes(b"{}")
        work = root / "runtime" / "runs" / "idle-failure"
        with patch.object(pilot, "ROOT", root), patch.object(pilot.sys, "platform", "linux"), \
                patch.object(pilot, "require_idle", side_effect=RuntimeError("GPU occupied")), \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "GPU occupied"):
                pilot.execute(str(model), work)
        self.popen.assert_not_called()
        self.check_output.assert_not_called()
        self.killpg.assert_not_called()
        self.assertTrue((work / "started.json").is_file())
        self.assertTrue((work / "native-current.json").is_file())
        self.assertFalse((work / "experiment").exists())
        finished = json.loads((work / "finished.json").read_text("utf-8"))
        self.assertEqual(finished["status"], "FAILED_OR_INTERRUPTED_PRESERVED")
        self.assertEqual(finished["error_type"], "RuntimeError")
        self.assertTrue(finished["cloud_instance_still_billed"])
        self.assertTrue(finished["uncertain_inflight_calls_must_not_be_retried"])
        self.assertEqual(finished["cleanup_errors"], [])
        self.assertTrue(work.with_suffix(".zip").is_file())
        self.assertTrue(work.with_suffix(".archive.json").is_file())
        with zipfile.ZipFile(work.with_suffix(".zip")) as saved:
            self.assertEqual(saved.read("idle-failure/finished.json"), (work / "finished.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
