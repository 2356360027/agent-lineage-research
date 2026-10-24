"""Offline transport/admission fixtures only; real HTTP and model calls are forbidden."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime import cache_intent_native_batch as native
from runtime.cache_intent_journal import verify
from scripts import prepare_staged_source_bundle as bundle


class NativeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        # Even accidental direct network use must fail before network access.
        self.net=patch("urllib.request.OpenerDirector.open",side_effect=AssertionError("No network"))
        self.net.start(); self.addCleanup(self.net.stop)

    def transport(self):
        snap=self.root/"snapshot"
        receipt=bundle.build(Path(__file__).resolve().parents[1],snap)
        # Running-source admission has its own tests; isolate transport-copy behavior here.
        with patch.object(native.entry,"check_running_source"):
            obj=native.FrozenTransport(snap,receipt["source_manifest_sha256"],self.root/"experiment")
        (self.root/"experiment/h200a-p1").mkdir(parents=True)
        return obj

    def call(self,obj,n=1,endpoint="http://127.0.0.1:8000/v1"):
        return obj("h200a-p1",self.root/f"experiment/h200a-p1/network/http-g{n:03d}",
                   endpoint,17,{"fixture":True})

    def test_verified_copy_and_bounded_worker_arguments(self):
        obj=self.transport()
        with patch.object(native.client,"request_once",return_value={"mock":True}) as request:
            self.assertEqual(self.call(obj),{"mock":True})
            self.call(obj,2)
        self.assertEqual(request.call_count,2)
        self.assertEqual(request.call_args.args[1:],("http://127.0.0.1:8000/v1","POST",17))
        for name,raw in obj.sources.items():
            self.assertEqual((self.root/"experiment/h200a-p1/network/source"/name).read_bytes(),raw)

    def test_wrong_path_or_endpoint_never_dispatches(self):
        obj=self.transport()
        with patch.object(native.client,"request_once") as request:
            with self.assertRaises(ValueError): self.call(obj,2)
            with self.assertRaises(ValueError): self.call(obj,endpoint="https://example.org/v1")
        request.assert_not_called()

    def test_changed_copied_source_stops_before_next_http(self):
        obj=self.transport()
        with patch.object(native.client,"request_once",return_value={}) as request:
            self.call(obj)
            path=self.root/"experiment/h200a-p1/network/source/runtime/__init__.py"
            path.write_bytes(b"# fixture corruption")
            with self.assertRaises(ValueError): self.call(obj,2)
        self.assertEqual(request.call_count,1)

    def test_failed_http_does_not_retry_same_generation(self):
        obj=self.transport()
        with patch.object(native.client,"request_once",side_effect=TimeoutError) as request:
            with self.assertRaises(TimeoutError): self.call(obj)
            with self.assertRaises(ValueError): self.call(obj)
        self.assertEqual(request.call_count,1)

    def args(self):
        return dict(inputs="unused",input_sha="a"*64,plan_sha="b"*64,source_bundle="unused",
            source_sha="c"*64,database="unused",native_config={},served_metadata={},server_command=[],
            tokenizer_assets={"tokenizer.json":"d"*64},count_chat=lambda x:0,count_text=lambda x:0,
            template_sha256="e"*64,output=self.root/"run",approval_record_sha256="f"*64)

    def test_missing_audit_reference_rejected_before_any_admission(self):
        with patch.object(native.entry,"inspect_inputs") as inspect:
            with self.assertRaises(ValueError):
                native.run_native_batch(**(self.args()|{"approval_record_sha256":""}))
        inspect.assert_not_called()
        self.assertFalse((self.root/"run").exists())

    def test_failed_model_admission_retains_sealed_preflight_without_transport(self):
        receipt={"tokenizer":{"tokenizer_files_sha256":{"tokenizer.json":"d"*64},
                              "template_sha256_utf8":"e"*64}}
        with patch.object(native.entry,"inspect_inputs",return_value={"TEST_ONLY":True}), \
             patch.object(native.entry,"load_private_inputs",return_value=({}, {}, receipt)), \
             patch.object(native.deployment,"verify_current_files",side_effect=ValueError("fixture")), \
             patch.object(native,"FrozenTransport") as transport:
            with self.assertRaises(ValueError): native.run_native_batch(**self.args())
        transport.assert_not_called()
        verify(self.root/"run/preflight")
        record=json.loads((self.root/"run/preflight/failure.json").read_text())
        self.assertFalse(record["automatic_retry"])


if __name__=="__main__": unittest.main()
