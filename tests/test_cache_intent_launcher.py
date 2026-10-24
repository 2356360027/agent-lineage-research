"""Mock-only launcher lifecycle. Never contact a server or load model weights."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from runtime import cache_intent_launcher as launch
from runtime.cache_intent_journal import verify


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.spec={key:str(self.root/key) for key in launch.PATHS}
        self.spec.update({key:"a"*64 for key in launch.DIGESTS},limit_seconds=1800)
        self.config={"model_path":str(self.root/"model"),
                     "packages":{"torch":"fixture","vllm":"fixture","transformers":"fixture"}}
        self.pinned={"tokenizer_files_sha256":{},"template_sha256_utf8":"b"*64,"versions":{}}

    def test_default_is_nonexecuting_even_with_spec(self):
        with patch.object(launch,"launch") as execute,patch.object(launch,"worker") as worker,redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(launch.main(["--spec","nonexistent"]),0)
        execute.assert_not_called(); worker.assert_not_called()
        self.assertEqual(json.loads(stream.getvalue())["status"],"PLAN_ONLY")

    def test_spec_rejects_extra_fields_relative_paths_and_unbounded_limits(self):
        for change in ({"limit_seconds":1801},{"limit_seconds":True},
                       {"inputs":"relative"},{"approval_record_sha256":"missing"},{"retry":True}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                launch.validate_spec(self.spec|change)

    def test_nonlinux_does_not_launch_or_create_output(self):
        with patch.object(launch.sys,"platform","win32"),patch.object(launch.subprocess,"Popen") as start:
            with self.assertRaises(RuntimeError): launch.launch(self.spec)
        start.assert_not_called()
        self.assertFalse(Path(self.spec["output"]).exists())

    def test_parent_has_one_deadline_and_exclusive_output(self):
        with patch.object(launch.sys,"platform","linux"),patch.object(launch.deployment,"supervise_owned_worker",return_value={"process_checks_passed":False}) as supervise:
            self.assertFalse(launch.launch(self.spec)["process_checks_passed"])
            kwargs=supervise.call_args.kwargs
            self.assertEqual(kwargs["limit_seconds"],1800)
            self.assertIn("runtime.cache_intent_launcher",supervise.call_args.args[0])
            verify(Path(self.spec["output"])/"request")
            with self.assertRaises(ValueError): launch.launch(self.spec)
            supervise.assert_called_once()

    def mocked_worker(self,*,early_error=None,result_status="NATIVE_UNREVIEWED_BATCH_FINISHED",busy=False):
        with ExitStack() as stack:
            def p(obj,name,**kw): return stack.enter_context(patch.object(obj,name,**kw))
            p(launch.sys,"platform",new="linux")
            p(launch,"os",new=SimpleNamespace(getpid=lambda:123,getpgrp=lambda:123))
            p(launch.entry,"inspect_inputs",return_value={})
            p(launch.entry,"load_private_inputs",return_value=({}, {}, {"tokenizer":self.pinned}))
            p(launch.entry,"pinned_bytes",return_value=json.dumps(self.config).encode())
            p(launch.deployment,"validate_metadata",return_value={})
            modelcheck=p(launch.deployment.native,"verify",side_effect=early_error)
            p(launch.tokenizer,"local_counters",return_value=(Mock(),Mock(),{}))
            idle=p(launch,"require_idle",side_effect=RuntimeError("busy fixture") if busy else None)
            def started(*args,**kwargs):
                modelcheck.assert_called_once(); idle.assert_called_once()
                self.assertFalse(kwargs["start_new_session"])
                return Mock(pid=456)
            process=p(launch.subprocess,"Popen",side_effect=started)
            p(launch,"await_ready",return_value={})
            batch=p(launch.bridge,"run_native_batch",return_value={"status":result_status})
            if early_error or busy:
                with self.assertRaises((ValueError,RuntimeError)): launch.worker(self.spec)
                process.assert_not_called(); batch.assert_not_called()
                answer=None
            else:
                answer=launch.worker(self.spec)
                batch.assert_called_once()
        verify(Path(self.spec["output"])/"execution/deployment")
        return answer

    def test_model_and_idle_checks_precede_server_and_server_inherits_group(self):
        self.assertTrue(self.mocked_worker())

    def test_model_check_failure_preserved_without_start(self):
        self.mocked_worker(early_error=ValueError("fixture weights changed"))
        failure=json.loads((Path(self.spec["output"])/"execution/deployment/failure.json").read_text())
        self.assertEqual(failure["error_type"],"ValueError")
        self.assertFalse(failure["automatic_retry"])

    def test_existing_service_refused_without_start(self):
        self.mocked_worker(busy=True)

    def test_incomplete_batch_is_not_process_success(self):
        self.assertFalse(self.mocked_worker(result_status="NATIVE_UNREVIEWED_BATCH_STOPPED"))

    def test_wrong_worker_group_never_reaches_preflight(self):
        with patch.object(launch.sys,"platform","linux"),patch.object(launch,"os",SimpleNamespace(getpid=lambda:123,getpgrp=lambda:456)),patch.object(launch.entry,"inspect_inputs") as inspect:
            with self.assertRaises(RuntimeError): launch.worker(self.spec)
            inspect.assert_not_called()

    def test_readiness_does_not_retry_dead_server(self):
        server=Mock(); server.poll.return_value=1
        with patch.object(launch,"read_models") as read:
            with self.assertRaises(RuntimeError): launch.await_ready(server)
            read.assert_not_called()

    def test_readiness_timeout_has_no_model_call(self):
        server=Mock(); server.poll.return_value=None
        with patch.object(launch.time,"monotonic",side_effect=[0,301]),patch.object(launch,"read_models") as read:
            with self.assertRaises(TimeoutError): launch.await_ready(server)
            read.assert_not_called()


if __name__=="__main__": unittest.main()
