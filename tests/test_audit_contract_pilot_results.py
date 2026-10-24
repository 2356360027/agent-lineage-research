"""Invented response fixtures only; no GPU, model, SQL or network calls."""
from contextlib import chdir
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from runtime import cache_intent_contract_pilot as pilot
from scripts import audit_contract_pilot_results as audit

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "runtime/runs/tool-input-audit-001/provenance/migration-20261002/native-new.json"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audit.encoded(value))


def seal(root, name):
    manifest = audit.inventory(root)
    manifest.pop(name, None)
    write(root / name, manifest)


def fixture(root, *, preflight_failure=False):
    old = json.loads(REF.read_text(encoding="utf-8"))
    native = {k: old[k] for k in ("deployment", "model", "model_path", "model_files", "model_content_sha256", "packages")}
    write(root / "EXPECTED_MODEL.json", native)
    write(root / "native-current.json", native)
    (root / "supervisor.py").write_bytes(b"# inert offline fixture\n")
    deploy = {"EXPECTED_MODEL.json": {"bytes": len(audit.encoded(native)), "sha256": audit.sha(audit.encoded(native))},
              "scripts/run_contract_pilot_server.py": {"bytes": len(b"# inert offline fixture\n"), "sha256": audit.sha(b"# inert offline fixture\n")}}
    if preflight_failure:
        write(root / "DEPLOY_MANIFEST.json", {"files": deploy})
        write(root / "finished.json", {"status": "FAILED_OR_INTERRUPTED_PRESERVED", "cleanup_errors": []})
        seal(root, "SUPERVISOR_HASHES.json")
        return
    exp = root / "experiment"
    for name, digest in audit.SOURCES.items():
        raw = (ROOT / name).read_bytes()
        path = exp / "source" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        deploy[name] = {"bytes": len(raw), "sha256": digest}
    with chdir(ROOT):
        plan = pilot.load_plan()
    write(exp / "plan.json", plan)
    write(exp / "native_model.json", native)
    (exp / "server-startup.log").write_bytes(b"SOFTWARE FIXTURE; no model\n")
    write(exp / "started.json", {"plan_sha256": audit.PLAN, "source_hashes": audit.SOURCES,
                               "native_manifest_sha256": audit.sha(audit.encoded(native)),
                               "startup_log_sha256": audit.sha(b"SOFTWARE FIXTURE; no model\n")})
    metadata = audit.encoded({"data": [{"id": native["model"], "root": native["model_path"]}]})
    write(exp / "model-preflight/transport.json", {"status": "ok", "http_status": 200,
          "response_truncated": False, "response_bytes": len(metadata), "response_sha256": audit.sha(metadata)})
    (exp / "model-preflight/response.raw").write_bytes(metadata)
    for index, call in enumerate(plan["calls"]):
        folder = exp / f"call-{index:03d}"
        write(folder / "request.json", pilot.payload(call, native["model"]))
        write(folder / "attempt.json", {"state": "DISPATCH_PENDING", "method": "POST"})
        contract = {key: "OFFLINE FIXTURE NOT RESEARCH DATA" for key in pilot.prompts.CONTRACT_FIELDS}
        response = {"model": native["model"], "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(contract)}}]}
        raw = audit.encoded(response)
        (folder / "response.raw").write_bytes(raw)
        write(folder / "transport.json", {"status": "ok", "http_status": 200, "response_truncated": False,
                                         "response_bytes": len(raw), "response_sha256": audit.sha(raw)})
        record = {k: call[k] for k in ("history_id", "factor", "arm", "seed")}
        record.update(index=index, status="format_valid", semantic_status="UNREVIEWED", contract=contract,
                      reported_usage={"prompt_tokens": 11, "completion_tokens": 7})
        write(exp / f"assessment-{index:03d}.json", record)
    write(exp / "summary.json", {"status": "COMPLETE_UNREVIEWED", "attempted_calls": 24,
          "planned_calls": 24, "unattempted_calls": 0, "stop_reason": None, "format_valid": 24,
          "format_invalid": 0, "transport_failed": 0, "calls_with_reported_usage": 24,
          "usage_complete_for_attempts": True, "reported_prompt_tokens": 264,
          "reported_completion_tokens": 168, "human_approvals": 0, "warnings": pilot.WARNINGS})
    seal(exp, "artifact_hashes.json")
    write(root / "DEPLOY_MANIFEST.json", {"files": deploy})
    write(root / "finished.json", {"status": "COMPLETE_UNREVIEWED", "runner_exit_code": 0, "cleanup_errors": []})
    seal(root, "SUPERVISOR_HASHES.json")


class AuditTests(unittest.TestCase):
    def check(self, root, output):
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts/audit_contract_pilot_results.py"),
                                 "--run", str(root), "--output", str(output)], capture_output=True, timeout=30)
        self.assertTrue(output.exists(), result.stderr.decode())
        return result, json.loads(output.read_bytes())

    def test_complete_fixture_and_no_raw_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / "raw"; root.mkdir()
            fixture(root)
            before = audit.inventory(root)
            result, report = self.check(root, base / "audit.json")
            self.assertEqual(result.returncode, 0, report)
            self.assertEqual(report["status"], "VERIFIED_COMPLETE_UNREVIEWED")
            self.assertEqual(report["structural_counts"]["reported_prompt_tokens"], 264)
            self.assertEqual(audit.inventory(root), before)

    def test_failed_preflight_stays_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / "raw"; root.mkdir()
            fixture(root, preflight_failure=True)
            _, report = self.check(root, base / "audit.json")
            self.assertEqual(report["status"], "VERIFIED_PRESERVED_FAILURE")
            self.assertEqual(report["recorded_call_directories"], 0)

    def test_tamper_detected_even_with_resealed_manifests(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / "raw"; root.mkdir()
            fixture(root)
            summary = json.loads((root / "experiment/summary.json").read_bytes())
            summary["reported_prompt_tokens"] += 1
            write(root / "experiment/summary.json", summary)
            seal(root / "experiment", "artifact_hashes.json"); seal(root, "SUPERVISOR_HASHES.json")
            result, report = self.check(root, base / "audit.json")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(report["status"], "AUDIT_FAILED")

    def test_extra_file_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / "raw"; root.mkdir()
            fixture(root, preflight_failure=True)
            (root / "unlisted.txt").write_bytes(b"extra")
            result, report = self.check(root, base / "audit.json")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(report["status"], "AUDIT_FAILED")


if __name__ == "__main__":
    unittest.main()
