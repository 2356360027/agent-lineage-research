"""Local source packaging and isolated import/SQLite fixture checks; no model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from scripts import prepare_staged_source_bundle as bundle


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)/"bundle"
        self.root = Path(__file__).resolve().parents[1]
        self.receipt = bundle.build(self.root,self.output)

    def test_only_source_and_no_overwrite(self):
        self.assertEqual(self.receipt["files"],len(bundle.FILES))
        with self.assertRaises(ValueError): bundle.build(self.root,self.output)
        self.assertEqual(set(p.relative_to(self.output).as_posix() for p in self.output.rglob("*") if p.is_file()),
                         {"source/"+p for p in bundle.FILES}|{"source_manifest.json"})

    def test_tampering_detected(self):
        (self.output/"source/runtime/cache_intent_staged_loop.py").write_bytes(b"# changed")
        with self.assertRaises(ValueError):
            bundle.verify(self.output,self.receipt["source_manifest_sha256"])

    def test_extra_file_detected(self):
        (self.output/"source/extra.txt").write_bytes(b"extra")
        with self.assertRaises(ValueError):
            bundle.verify(self.output,self.receipt["source_manifest_sha256"])

    def test_copied_modules_and_real_sql_child_use_bundle(self):
        code = '''
from pathlib import Path
import json, sqlite3, tempfile
from runtime import cache_intent_staged_adapter, cache_intent_staged_loop, cache_intent_journal
from runtime import cache_intent_staged_preview, cache_intent_tools, native, bird_readonly_audit
from scripts import prepare_staged_source_bundle
modules = [cache_intent_staged_adapter,cache_intent_staged_loop,cache_intent_journal,
           cache_intent_staged_preview,cache_intent_tools,native,bird_readonly_audit,
           prepare_staged_source_bundle]
for module in modules:
    assert Path(module.__file__).resolve().is_relative_to(Path.cwd().resolve())
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory)/"fixture.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE fixture (id INTEGER)")
    connection.execute("INSERT INTO fixture VALUES (7)")
    connection.commit(); connection.close()
    result = bird_readonly_audit.run_child(path,"SELECT id FROM fixture")
    assert result["complete"], result
    assert result["rows"] == [[["integer","7"]]], result
print(json.dumps({"imported_modules":len(modules),"synthetic_readonly_calls":1,"model_calls":0}))
'''
        env = os.environ.copy()
        env.pop("PYTHONPATH",None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run([sys.executable,"-B","-c",code],cwd=self.output/"source",
                                env=env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)["synthetic_readonly_calls"],1)
        bundle.verify(self.output,self.receipt["source_manifest_sha256"])


if __name__ == "__main__": unittest.main()
