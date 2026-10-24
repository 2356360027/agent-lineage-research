"""Pure metadata fixtures and mocked Linux process lifecycle; no server/model."""
from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock
from runtime import cache_intent_deployment as deploy
from runtime.bird_readonly_audit import digest
from runtime.cache_intent_journal import verify


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.assets={n:"a"*64 for n in deploy.TOKENIZER_ASSETS}
        files={n:{"sha256":s,"size":1} for n,s in self.assets.items()}
        self.config=dict(deployment="native",model=deploy.MODEL_ID,model_path=str(Path.cwd().resolve()),
                         model_files=files,model_content_sha256=digest(files),packages={})
        self.command=deploy.recovery_server_command(self.config["model_path"])
        self.metadata=dict(object="list",data=[dict(id=deploy.MODEL_ID,root=self.config["model_path"],
                                                   max_model_len=32768)])

    def check(self): return deploy.validate_metadata(self.config,self.metadata,self.command,self.assets)

    def test_canonical_metadata_is_consistency_not_attestation(self):
        result=self.check()
        self.assertEqual(result["status"],"METADATA_CONSISTENT_ONLY")
        self.assertFalse(result["loaded_weight_attestation"])
        old=deploy.serve_command(self.config["model_path"])
        self.assertEqual(old[old.index("--max-model-len")+1],"4096")

    def test_reject_old_or_unreported_window(self):
        for value in (4096,None,True,"32768"):
            self.metadata["data"][0]["max_model_len"]=value
            with self.assertRaises(ValueError): self.check()

    def test_wrong_root_duplicate_model_and_mutated_command(self):
        original=deepcopy(self.metadata)
        self.metadata["data"][0]["root"]="relative/path"
        with self.assertRaises(ValueError): self.check()
        self.metadata=deepcopy(original); self.metadata["data"]*=2
        with self.assertRaises(ValueError): self.check()
        self.metadata=original; self.command+= ["--trust-remote-code"]
        with self.assertRaises(ValueError): self.check()

    def test_changed_tokenizer_reference_rejected(self):
        self.assets["tokenizer.json"]="b"*64
        with self.assertRaises(ValueError): self.check()

    def test_current_files_check_called_and_failure_propagates(self):
        with patch.object(deploy.native,"verify") as check:
            r=deploy.verify_current_files(self.config,self.metadata,self.command,self.assets)
            check.assert_called_once_with(self.config)
            self.assertTrue(r["local_files_rehashed"])
        with patch.object(deploy.native,"verify",side_effect=ValueError("changed")):
            with self.assertRaises(ValueError):
                deploy.verify_current_files(self.config,self.metadata,self.command,self.assets)


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name)/"run"
        self.child=Mock(pid=12345); self.child.wait.return_value=0
        self.wait_effect=None

    def run_mock(self,cleanup_error=None):
        with patch.object(deploy.sys,"platform","linux"),patch.object(deploy.subprocess,"Popen",return_value=self.child) as launch,patch.object(deploy,"stop_owned_group",side_effect=cleanup_error) as stop,patch.object(deploy,"wait_unreaped",return_value=0,side_effect=self.wait_effect):
            result=deploy.supervise_owned_worker(["fixture-python","fixture.py"],cwd=Path.cwd(),
                output=self.output,limit_seconds=2)
            self.assertTrue(launch.call_args.kwargs["start_new_session"])
            stop.assert_called_once_with(self.child)
        verify(self.output/"supervisor")
        return result

    def test_exit_still_cleans_owned_descendants(self):
        result=self.run_mock()
        self.assertEqual(result["status"],"PROCESS_EXITED")
        self.assertTrue(result["process_checks_passed"])
        self.child.wait.assert_not_called()  # Not reaped before the cleanup helper.

    def test_timeout_and_cleanup_failure_are_retained(self):
        self.wait_effect=subprocess.TimeoutExpired(["fixture"],2)
        result=self.run_mock(PermissionError("fixture"))
        self.assertEqual(result["status"],"PROCESS_TIMEOUT")
        self.assertEqual(result["cleanup"],"FAILED")
        self.assertFalse(result["cloud_instance_shutdown"])
        self.assertFalse(result["process_checks_passed"])

    def test_spawn_failure_never_targets_other_processes(self):
        with patch.object(deploy.sys,"platform","linux"),patch.object(deploy.subprocess,"Popen",side_effect=OSError("fixture")),patch.object(deploy,"stop_owned_group") as stop:
            with self.assertRaises(OSError):
                deploy.supervise_owned_worker(["fixture"],cwd=Path.cwd(),output=self.output,limit_seconds=2)
            stop.assert_not_called()
        verify(self.output/"supervisor")

    def test_unsupported_platform_does_not_launch(self):
        with patch.object(deploy.sys,"platform","win32"),patch.object(deploy.subprocess,"Popen") as launch:
            with self.assertRaises(RuntimeError):
                deploy.supervise_owned_worker(["fixture"],cwd=Path.cwd(),output=self.output,limit_seconds=2)
            launch.assert_not_called()

    def test_lost_child_ownership_never_signals_reused_pid(self):
        with patch.object(deploy.sys,"platform","linux"),patch.object(deploy.subprocess,"Popen",return_value=self.child),patch.object(deploy,"wait_unreaped",side_effect=ChildProcessError()),patch.object(deploy,"stop_owned_group") as stop:
            with self.assertRaises(ChildProcessError):
                deploy.supervise_owned_worker(["fixture"],cwd=Path.cwd(),output=self.output,limit_seconds=2)
            stop.assert_not_called()
        verify(self.output/"supervisor")


class WaitTests(unittest.TestCase):
    def test_wait_uses_wnowait_without_popen_reap(self):
        child=Mock(pid=12)
        os_api=SimpleNamespace(P_PID=1,WEXITED=2,WNOHANG=4,WNOWAIT=8,
            CLD_EXITED=10,CLD_KILLED=11,CLD_DUMPED=12,
            waitid=Mock(return_value=SimpleNamespace(si_pid=12,si_code=10,si_status=3)))
        with patch.object(deploy,"os",os_api):
            self.assertEqual(deploy.wait_unreaped(child,1),3)
        os_api.waitid.assert_called_once_with(1,12,14)
        child.wait.assert_not_called(); child.poll.assert_not_called()

    def test_deadline_does_not_wait_again(self):
        os_api=SimpleNamespace(waitid=Mock())
        with patch.object(deploy,"os",os_api),patch.object(deploy.time,"monotonic",side_effect=[0,2]):
            with self.assertRaises(subprocess.TimeoutExpired): deploy.wait_unreaped(Mock(pid=12),1)
        os_api.waitid.assert_not_called()


if __name__=="__main__": unittest.main()
