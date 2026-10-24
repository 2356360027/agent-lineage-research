"""Local archive regression checks; skip when the private backup is absent.

No server/network/GPU calls. Modified copies are fixtures, never research data.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.audit_contract_pilot_results import encoded, inventory

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runtime/runs/contract-interface-download-20261014-001/extracted/contract-interface-20261014-001"
BUNDLE = ROOT / "deployment/contract-interface-20261014-001.zip"


@unittest.skipUnless(RUN.is_dir() and BUNDLE.is_file(), "Private archive unavailable")
class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "fixture"
        shutil.copytree(RUN, self.run)

    def rehash(self):
        for base, filename in ((self.run / "experiment", "artifact_hashes.json"),
                               (self.run, "SUPERVISOR_HASHES.json")):
            hashes = inventory(base)
            hashes.pop(filename)
            (base / filename).write_bytes(encoded(hashes))

    def invoke(self):
        return subprocess.run([sys.executable, "-B", "-m", "scripts.audit_contract_interface_results",
            "--run", str(self.run), "--bundle", str(BUNDLE), "--output", str(self.root / "audit.json")],
            cwd=ROOT, capture_output=True, timeout=30)

    def test_valid_archive_reconstructs(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.root / "audit.json").read_text())
        self.assertEqual(report["structural_counts"]["attempted_calls"], 12)
        self.assertFalse(report["semantic_scoring_performed"])

    def test_byte_corruption_rejected(self):
        (self.run / "experiment/call-000/response.raw").write_bytes(b"corrupt fixture")
        self.assertNotEqual(self.invoke().returncode, 0)

    def test_false_summary_rejected_even_with_rehashed_manifest(self):
        path = self.run / "experiment/summary.json"
        value = json.loads(path.read_text()); value["reported_prompt_tokens"] += 1
        path.write_bytes(encoded(value)); self.rehash()
        self.assertNotEqual(self.invoke().returncode, 0)

    def test_changed_request_rejected_even_with_rehashed_manifest(self):
        path = self.run / "experiment/call-000/request.json"
        value = json.loads(path.read_text()); value["seed"] += 1
        path.write_bytes(encoded(value)); self.rehash()
        self.assertNotEqual(self.invoke().returncode, 0)

    def test_changed_served_prompt_rejected_even_with_rehashed_manifest(self):
        path = self.run / "server-startup.log"
        raw = path.read_bytes()
        self.assertIn(b"Describe the current", raw)
        path.write_bytes(raw.replace(b"Describe the current", b"Describe some other", 1))
        self.rehash()
        self.assertNotEqual(self.invoke().returncode, 0)


if __name__ == "__main__":
    unittest.main()
