"""Offline software fixtures only: no model, network, SQL, or research observations.

The optional existing-package check reads frozen inputs without executing tasks.
Every generated request/response below is a software-test fixture, not evidence
of task correctness, semantic approval, cache effects, or model performance.
"""
from contextlib import chdir, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from runtime import cache_intent_contract_pilot as pilot
from runtime import cache_intent_prompts as prompts


ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "http://127.0.0.1:8000/v1"
MODEL = "software-fixture-model-not-a-real-deployment"


def preview_fixtures():
    """Use allowed wrapper IDs solely to exercise fixed-coverage validation."""
    records = []
    for ident in sorted(pilot.HISTORIES):
        for factor in ("availability", "read_cost"):
            envelopes = list(prompts.make_pair(
                current_request="Software fixture: count distinct objects.",
                evidence="Software fixture, not a research observation.",
                schema=["CREATE TABLE software_fixture (object_id INTEGER);"],
                history={"historical_request": "Software fixture: list objects.",
                         "sql": "SELECT object_id FROM software_fixture"},
                database_sha256="a" * 64, factor=factor))
            records.append({"private_history_id": ident, "factor": factor,
                            "envelopes": envelopes,
                            "envelope_hashes": [pilot.sha(pilot.encoded(e)) for e in envelopes]})
    return records


def call_plan():
    calls = []
    for record in preview_fixtures():
        for arm, envelope in enumerate(record["envelopes"]):
            calls.append({"history_id": record["private_history_id"],
                          "factor": record["factor"], "arm": arm,
                          "seed": 71, "envelope": envelope})
    return {"calls": calls}


def contract_fixture():
    return {key: "software-test fixture: not a semantic label" for key in prompts.CONTRACT_FIELDS}


def response_fixture(*, content=None, model=MODEL, usage=True):
    value = {"model": model, "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": json.dumps(contract_fixture()) if content is None else content}}]}
    if usage:
        value["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
    return value


def frozen_worker_fixture(root):
    source = Path(root) / "source"
    (source / "runtime").mkdir(parents=True)
    (source / "runtime" / "cache_intent_contract_pilot.py").write_bytes(
        b"# Inert software fixture; never executed.\n")
    return source


class FakeHTTPResponse(io.BytesIO):
    code = 200


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        # Assertions in teardown catch forbidden calls even if product code catches
        # their exceptions. Individual tests replace only the intended fake path.
        self.guards = []
        for target in ("socket.create_connection", "socket.socket.connect",
                       "subprocess.run", "subprocess.Popen", "sqlite3.connect",
                       "urllib.request.OpenerDirector.open"):
            guard = patch(target, side_effect=AssertionError("Offline test forbids " + target))
            self.guards.append(guard.start())
            self.addCleanup(guard.stop)

    def tearDown(self):
        for guard in self.guards:
            guard.assert_not_called()


class PlanAndPayloadTests(OfflineTestCase):
    def test_synthetic_preview_coverage_and_fixed_hashes(self):
        records = preview_fixtures()
        self.assertIsNone(pilot.validate_previews(records))
        for mutate in (lambda r: r.pop(),
                       lambda r: r.__setitem__(1, deepcopy(r[0])),
                       lambda r: r[0].update(private_history_id="unknown"),
                       lambda r: r[0].update(factor="unplanned"),
                       lambda r: r[0].update(extra_label="private"),
                       lambda r: r[0].update(envelope_hashes=["0" * 64] * 2),
                       lambda r: r[0].update(envelopes=r[0]["envelopes"][:1])):
            with self.subTest(mutation=repr(mutate)):
                altered = deepcopy(records)
                mutate(altered)
                with self.assertRaises(ValueError):
                    pilot.validate_previews(altered)

    def test_rehashed_unauthorized_prompt_edits_are_rejected(self):
        records = preview_fixtures()
        for envelope in records[0]["envelopes"]:
            envelope["messages"][0]["content"] += " Unauthorized software-test edit."
        records[0]["envelope_hashes"] = [pilot.sha(pilot.encoded(e)) for e in records[0]["envelopes"]]
        with self.assertRaises(ValueError):
            pilot.validate_previews(records)

    def test_plan_is_deterministic_paired_and_unreviewed(self):
        raw = pilot.encoded(preview_fixtures())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "PRIVATE_prompt_previews.json"
            path.write_bytes(raw)
            with patch.object(pilot, "PREVIEWS_SHA", pilot.sha(raw)):
                plan = pilot.load_plan(temp)
                self.assertEqual(plan, pilot.load_plan(temp))
            self.assertEqual(len(plan["calls"]), 24)
            self.assertEqual(plan["maximum_client_seconds"], 1800)
            self.assertEqual(plan["human_approvals"], 0)
            self.assertFalse(plan["formal_inference_eligible"])
            self.assertEqual(plan["independent_targets"], 2)
            self.assertEqual(len({(c["history_id"], c["factor"], c["arm"]) for c in plan["calls"]}), 24)
            for record in preview_fixtures():
                pair = [c for c in plan["calls"] if c["history_id"] == record["private_history_id"]
                        and c["factor"] == record["factor"]]
                self.assertEqual({c["arm"] for c in pair}, {0, 1})
                self.assertEqual(len({c["seed"] for c in pair}), 1)
            with self.assertRaises(ValueError):
                pilot.load_plan(temp)
            with patch.object(pilot, "PREVIEWS_SHA", pilot.sha(raw)), patch.object(pilot, "PROMPTS_SHA", "0" * 64):
                with self.assertRaises(ValueError):
                    pilot.load_plan(temp)

    @unittest.skipUnless((ROOT / pilot.PACKAGE / "PRIVATE_prompt_previews.json").is_file(),
                         "Private frozen package is not distributed with public software tests")
    def test_existing_frozen_package_read_only_provenance(self):
        path = ROOT / pilot.PACKAGE / "PRIVATE_prompt_previews.json"
        before = pilot.sha(path.read_bytes())
        plan = pilot.load_plan(path.parent)
        self.assertEqual(before, pilot.PREVIEWS_SHA)
        self.assertEqual(pilot.sha(path.read_bytes()), before)
        self.assertEqual(len(plan["calls"]), pilot.MAX_CALLS)
        self.assertFalse(plan["formal_inference_eligible"])

    def test_payload_excludes_private_wrapper_and_does_not_alias(self):
        call = call_plan()["calls"][0]
        call.update(private_label="do-not-leak", candidate_answer="do-not-leak",
                    human_answers="do-not-leak", history_relation="do-not-leak")
        original = deepcopy(call)
        body = pilot.payload(call, MODEL)
        self.assertEqual(set(body), {"messages", "tools", "response_format", "model", "seed",
                                     "temperature", "top_p", "max_tokens", "stream"})
        self.assertNotIn("do-not-leak", json.dumps(body))
        self.assertNotIn("history_id", body)
        self.assertNotIn("factor", body)
        self.assertNotIn("arm", body)
        self.assertEqual(body["tools"], [])
        self.assertEqual(body["max_tokens"], 768)
        body["messages"][0]["content"] = "mutation only in test payload"
        body["tools"].append("fixture")
        self.assertEqual(call, original)


class ParserTests(OfflineTestCase):
    def test_exact_eight_fields_accepted_without_semantic_judgement(self):
        self.assertEqual(pilot.parse_contract(response_fixture()), contract_fixture())

    def test_duplicate_nonfinite_and_non_json_content_rejected(self):
        for content in ('{"entity":"a","entity":"b"}', '{"entity":NaN}',
                        '{"entity":Infinity}', '{"entity":-Infinity}',
                        '```json\n{}\n```', "[]", "not JSON"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                pilot.parse_contract(response_fixture(content=content))

    def test_missing_extra_empty_and_nonstring_fields_rejected(self):
        invalid = []
        value = contract_fixture(); value.pop("entity"); invalid.append(value)
        value = contract_fixture(); value["extra"] = "label"; invalid.append(value)
        for bad in ("", "   ", None, 1, True, [], {}):
            value = contract_fixture(); value["entity"] = bad; invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                pilot.parse_contract(response_fixture(content=json.dumps(value)))

    def test_truncation_tool_function_refusal_and_choice_shapes_rejected(self):
        cases = [None, [], {}, {"choices": []}, {"choices": [None]}]
        for reason in ("length", "tool_calls", None):
            value = response_fixture(); value["choices"][0]["finish_reason"] = reason; cases.append(value)
        value = response_fixture(); value["choices"] *= 2; cases.append(value)
        for field in ("tool_calls", "function_call", "refusal"):
            value = response_fixture(); value["choices"][0]["message"][field] = "unexpected"; cases.append(value)
        value = response_fixture(); value["choices"][0]["message"]["content"] = {}; cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                pilot.parse_contract(value)

    def test_usage_requires_nonnegative_integers_not_boolean_or_missing(self):
        self.assertEqual(pilot.valid_usage(response_fixture()), {"prompt_tokens": 11, "completion_tokens": 7})
        self.assertIsNone(pilot.valid_usage(None))
        self.assertIsNone(pilot.valid_usage(response_fixture(usage=False)))
        for bad in (-1, True, 1.5, "7", None):
            value = response_fixture(); value["usage"]["completion_tokens"] = bad
            self.assertIsNone(pilot.valid_usage(value))


class HTTPWorkerTests(OfflineTestCase):
    def test_only_explicit_loopback_port_is_allowed(self):
        self.assertEqual(pilot.endpoint_url(ENDPOINT), ENDPOINT)
        for url in ("http://localhost:8000/v1", "https://127.0.0.1:8000/v1",
                    "http://127.0.0.1:0/v1", "http://127.0.0.1:65536/v1",
                    "http://127.0.0.2:8000/v1", "http://user:pass@127.0.0.1:8000/v1",
                    "http://127.0.0.1:8000/v1/", "http://127.0.0.1:8000/v1?redirect=x",
                    "http://example.org:8000/v1", "http://[::1]:8000/v1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                pilot.endpoint_url(url)

    def test_redirect_handler_rejects_even_loopback_redirect(self):
        with self.assertRaises(ValueError):
            pilot.NoRedirect().redirect_request(None, None, 302, "Found", {}, ENDPOINT + "/models")

    def test_post_disables_environment_proxies_and_retains_body(self):
        raw = pilot.encoded(response_fixture())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = {"model": MODEL, "fixture": True}
            pilot.save_new(root / "request.json", request)
            opener = Mock(); opener.open.return_value = FakeHTTPResponse(raw)
            with patch.dict("os.environ", {"http_proxy": "http://do-not-use.invalid:9",
                                           "HTTP_PROXY": "http://do-not-use.invalid:9"}), \
                    patch.object(pilot.urllib.request, "build_opener", return_value=opener) as builder:
                pilot.http_worker(root, ENDPOINT, "POST", 5)
            handlers = builder.call_args.args
            self.assertIsInstance(handlers[0], urllib.request.ProxyHandler)
            self.assertEqual(handlers[0].proxies, {})
            self.assertIsInstance(handlers[1], pilot.NoRedirect)
            opener.open.assert_called_once()
            sent = opener.open.call_args.args[0]
            self.assertEqual(sent.full_url, ENDPOINT + "/chat/completions")
            self.assertEqual(sent.get_method(), "POST")
            self.assertEqual(sent.data, pilot.encoded(request))
            receipt = json.loads((root / "transport.json").read_text())
            self.assertEqual(receipt["status"], "ok")
            self.assertEqual(receipt["response_sha256"], pilot.sha(raw))
            self.assertEqual((root / "response.raw").read_bytes(), raw)

    def test_get_http_error_preserves_error_body_without_retry(self):
        raw = b'{"software_fixture_error":"unavailable"}'
        error = urllib.error.HTTPError(ENDPOINT + "/models", 503, "Unavailable", {}, io.BytesIO(raw))
        opener = Mock(); opener.open.side_effect = error
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(pilot.urllib.request, "build_opener", return_value=opener):
            pilot.http_worker(temp, ENDPOINT, "GET", 5)
            receipt = json.loads((Path(temp) / "transport.json").read_text())
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["http_status"], 503)
            self.assertEqual((Path(temp) / "response.raw").read_bytes(), raw)
            self.assertIsNone(opener.open.call_args.args[0].data)
            opener.open.assert_called_once()

    def test_oversized_response_retains_only_bound_and_marks_failed(self):
        opener = Mock(); opener.open.return_value = FakeHTTPResponse(b"x" * 33)
        with tempfile.TemporaryDirectory() as temp, patch.object(pilot, "MAX_RESPONSE_BYTES", 16), \
                patch.object(pilot.urllib.request, "build_opener", return_value=opener):
            pilot.http_worker(temp, ENDPOINT, "GET", 5)
            receipt = json.loads((Path(temp) / "transport.json").read_text())
            self.assertEqual(receipt["status"], "failed")
            self.assertTrue(receipt["response_truncated"])
            self.assertEqual(receipt["response_bytes"], 16)
            self.assertEqual((Path(temp) / "response.raw").read_bytes(), b"x" * 16)

    def test_received_prefix_survives_later_read_failure(self):
        class BrokenResponse(FakeHTTPResponse):
            def __init__(self):
                super().__init__(b"")
                self.reads = 0

            def read1(self, size):
                self.reads += 1
                if self.reads == 1:
                    return b"received-software-fixture-prefix"
                raise TimeoutError("software fixture: stalled response")

        opener = Mock(); opener.open.return_value = BrokenResponse()
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(pilot.urllib.request, "build_opener", return_value=opener):
            pilot.http_worker(temp, ENDPOINT, "GET", 5)
            receipt = json.loads((Path(temp) / "transport.json").read_text())
            self.assertEqual(receipt["error_type"], "TimeoutError")
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual((Path(temp) / "response.raw").read_bytes(), b"received-software-fixture-prefix")


class RequestOnceTests(OfflineTestCase):
    def test_archived_source_cwd_exactly_one_worker_and_exclusive_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = frozen_worker_fixture(root); directory = root / "attempt"
            raw = pilot.encoded(response_fixture())

            def worker(command, **kwargs):
                self.assertEqual(kwargs["cwd"], source)
                self.assertEqual(kwargs["timeout"], 3)
                self.assertTrue(kwargs["capture_output"])
                self.assertIn("runtime.cache_intent_contract_pilot", command)
                (directory / "response.raw").write_bytes(raw)
                pilot.save_new(directory / "transport.json", {"status": "ok", "response_sha256": pilot.sha(raw)})
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with patch.object(pilot.subprocess, "run", side_effect=worker) as dispatch:
                result = pilot.request_once(directory, ENDPOINT, "POST", 3, request={"fixture": True})
                self.assertEqual(result, response_fixture())
                self.assertEqual(json.loads((directory / "request.json").read_text()), {"fixture": True})
                with self.assertRaises(FileExistsError):
                    pilot.request_once(directory, ENDPOINT, "POST", 3, request={"fixture": False})
                dispatch.assert_called_once()
            self.assertEqual((directory / "response.raw").read_bytes(), raw)

    def test_missing_archived_source_fails_before_attempt_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "attempt"
            with self.assertRaises(ValueError):
                pilot.request_once(directory, ENDPOINT, "GET", 3)
            self.assertFalse(directory.exists())

    def test_timeout_is_single_attempt_preserves_pending_and_partial_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); frozen_worker_fixture(root); directory = root / "attempt"

            def timeout_worker(command, **kwargs):
                (directory / "response.raw").write_bytes(b"partial fixture bytes")
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])

            with patch.object(pilot.subprocess, "run", side_effect=timeout_worker) as dispatch:
                with self.assertRaisesRegex(RuntimeError, "do not retry"):
                    pilot.request_once(directory, ENDPOINT, "POST", 2, request={"fixture": True})
                dispatch.assert_called_once()
            self.assertEqual(json.loads((directory / "attempt.json").read_text())["state"], "DISPATCH_PENDING")
            stop = json.loads((directory / "client_stop.json").read_text())
            self.assertTrue(stop["server_request_may_still_be_running"])
            self.assertEqual((directory / "response.raw").read_bytes(), b"partial fixture bytes")

    def test_nonzero_worker_retains_hashes_without_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); frozen_worker_fixture(root); directory = root / "attempt"
            done = subprocess.CompletedProcess([], 2, b"fixture stdout", b"fixture stderr")
            with patch.object(pilot.subprocess, "run", return_value=done) as dispatch:
                with self.assertRaises(RuntimeError):
                    pilot.request_once(directory, ENDPOINT, "GET", 3)
                dispatch.assert_called_once()
            error = json.loads((directory / "worker_error.json").read_text())
            self.assertEqual(error["stderr_sha256"], pilot.sha(b"fixture stderr"))
            self.assertEqual(error["returncode"], 2)

    def test_mismatched_response_hash_and_invalid_outer_json_rejected(self):
        for raw, digest in ((b"{}", "0" * 64), (b'{"a":1,"a":2}', None), (b'{"a":NaN}', None)):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); frozen_worker_fixture(root); directory = root / "attempt"

                def worker(command, **kwargs):
                    (directory / "response.raw").write_bytes(raw)
                    pilot.save_new(directory / "transport.json", {"status": "ok", "response_sha256": digest or pilot.sha(raw)})
                    return subprocess.CompletedProcess(command, 0, b"", b"")

                with patch.object(pilot.subprocess, "run", side_effect=worker) as dispatch:
                    with self.assertRaises((ValueError, RuntimeError)):
                        pilot.request_once(directory, ENDPOINT, "GET", 3)
                    dispatch.assert_called_once()
                self.assertEqual((directory / "response.raw").read_bytes(), raw)


class RunCallsTests(OfflineTestCase):
    def run_fixture(self, output, *, transport=None, clock=lambda: 0):
        transport = transport or Mock(side_effect=lambda *a, **k: response_fixture())
        with redirect_stdout(io.StringIO()):
            result = pilot.run_calls(call_plan(), MODEL, output, ENDPOINT, transport=transport, clock=clock)
        self.assertEqual(result, json.loads((Path(output) / "summary.json").read_text()))
        return result, transport

    def test_all_calls_complete_only_format_not_semantic_success(self):
        with tempfile.TemporaryDirectory() as temp:
            summary, transport = self.run_fixture(temp)
            self.assertEqual(summary["status"], "COMPLETE_UNREVIEWED")
            self.assertEqual(summary["attempted_calls"], 24)
            self.assertEqual(summary["unattempted_calls"], 0)
            self.assertEqual(summary["format_valid"], 24)
            self.assertEqual(summary["human_approvals"], 0)
            self.assertEqual(summary["reported_prompt_tokens"], 24 * 11)
            self.assertEqual(summary["reported_completion_tokens"], 24 * 7)
            self.assertTrue(summary["usage_complete_for_attempts"])
            self.assertEqual(transport.call_count, 24)
            records = [json.loads(p.read_text()) for p in Path(temp).glob("assessment-*.json")]
            self.assertEqual(len(records), 24)
            self.assertTrue(all(r["semantic_status"] == "UNREVIEWED" for r in records))
            self.assertNotIn("accuracy", summary)
            self.assertNotIn("effect", summary)
            for called in transport.call_args_list:
                self.assertEqual(called.args[1:3], (ENDPOINT, "POST"))
                self.assertGreater(called.args[3], 0)
                self.assertLessEqual(called.args[3], 180)

    def test_invalid_contract_is_retained_and_does_not_stop_or_retry(self):
        responses = [response_fixture(content="invalid software fixture")] + [response_fixture() for _ in range(23)]
        with tempfile.TemporaryDirectory() as temp:
            summary, transport = self.run_fixture(temp, transport=Mock(side_effect=responses))
            self.assertEqual(summary["status"], "COMPLETE_UNREVIEWED")
            self.assertEqual(summary["format_invalid"], 1)
            self.assertEqual(summary["format_valid"], 23)
            self.assertEqual(transport.call_count, 24)
            first = json.loads((Path(temp) / "assessment-000.json").read_text())
            self.assertNotIn("contract", first)
            self.assertEqual(first["reported_usage"], {"prompt_tokens": 11, "completion_tokens": 7})

    def test_transport_failure_stops_and_keeps_unattempted_coverage(self):
        transport = Mock(side_effect=[response_fixture(), TimeoutError("software fixture failure")])
        with tempfile.TemporaryDirectory() as temp:
            summary, _ = self.run_fixture(temp, transport=transport)
            self.assertEqual(summary["status"], "INCOMPLETE_UNREVIEWED")
            self.assertEqual(summary["attempted_calls"], 2)
            self.assertEqual(summary["unattempted_calls"], 22)
            self.assertEqual(summary["transport_failed"], 1)
            self.assertEqual(summary["stop_reason"], "TRANSPORT_OR_MODEL_ID_FAILURE")
            self.assertFalse(summary["usage_complete_for_attempts"])
            self.assertEqual(summary["reported_prompt_tokens"], 11)
            self.assertEqual(transport.call_count, 2)
            self.assertFalse((Path(temp) / "assessment-002.json").exists())

    def test_model_id_mismatch_stops_but_preserves_reported_usage(self):
        transport = Mock(return_value=response_fixture(model="other-software-fixture-model"))
        with tempfile.TemporaryDirectory() as temp:
            summary, _ = self.run_fixture(temp, transport=transport)
            self.assertEqual(summary["attempted_calls"], 1)
            self.assertEqual(summary["unattempted_calls"], 23)
            self.assertEqual(summary["transport_failed"], 1)
            self.assertEqual(summary["calls_with_reported_usage"], 1)
            self.assertEqual(summary["reported_completion_tokens"], 7)
            self.assertTrue(summary["usage_complete_for_attempts"])
            transport.assert_called_once()

    def test_missing_usage_is_not_imputed_as_zero_or_marked_complete(self):
        responses = [response_fixture(usage=False)] + [response_fixture() for _ in range(23)]
        with tempfile.TemporaryDirectory() as temp:
            summary, _ = self.run_fixture(temp, transport=Mock(side_effect=responses))
            self.assertEqual(summary["status"], "COMPLETE_UNREVIEWED")
            self.assertEqual(summary["calls_with_reported_usage"], 23)
            self.assertFalse(summary["usage_complete_for_attempts"])
            self.assertEqual(summary["reported_prompt_tokens"], 23 * 11)

    def test_deadline_before_first_call_creates_incomplete_summary_without_dispatch(self):
        times = iter([0, pilot.MAX_SECONDS, pilot.MAX_SECONDS])
        with tempfile.TemporaryDirectory() as temp:
            summary, transport = self.run_fixture(temp, clock=lambda: next(times))
            self.assertEqual(summary["attempted_calls"], 0)
            self.assertEqual(summary["unattempted_calls"], 24)
            self.assertEqual(summary["stop_reason"], "CLIENT_BATCH_DEADLINE")
            self.assertEqual(summary["status"], "INCOMPLETE_UNREVIEWED")
            transport.assert_not_called()

    def test_remaining_deadline_caps_request_and_stops_after_first(self):
        times = iter([0, pilot.MAX_SECONDS - 5, pilot.MAX_SECONDS, pilot.MAX_SECONDS])
        with tempfile.TemporaryDirectory() as temp:
            summary, transport = self.run_fixture(temp, clock=lambda: next(times))
            self.assertEqual(summary["attempted_calls"], 1)
            self.assertEqual(summary["unattempted_calls"], 23)
            self.assertEqual(summary["stop_reason"], "CLIENT_BATCH_DEADLINE")
            self.assertEqual(transport.call_args.args[3], 5)
            transport.assert_called_once()

    def test_partial_schedule_rejected_without_dispatch_or_summary(self):
        plan = call_plan(); plan["calls"].pop()
        transport = Mock()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                pilot.run_calls(plan, MODEL, temp, ENDPOINT, transport=transport, clock=lambda: 0)
            transport.assert_not_called()
            self.assertFalse((Path(temp) / "summary.json").exists())


class ExecuteBoundaryTests(OfflineTestCase):
    def native_fixture(self, root):
        """Inert manifest only; verification must be mocked in every caller."""
        root = Path(root)
        config = {"deployment": "native", "model": MODEL,
                  "model_path": str(root / "nonexistent-software-fixture-weights"),
                  "model_files": {}, "model_content_sha256": "a" * 64,
                  "packages": {}}
        config_path = root / "fixture-native.json"
        startup_path = root / "fixture-startup.log"
        pilot.save_new(config_path, config)
        startup_path.write_bytes(b"Software fixture startup log. No model was started.\n")
        return config, config_path, startup_path

    def assert_artifact_manifest(self, output):
        manifest = json.loads((output / "artifact_hashes.json").read_text())
        actual = {p.relative_to(output).as_posix(): pilot.sha(p.read_bytes())
                  for p in output.rglob("*") if p.is_file() and p.name != "artifact_hashes.json"}
        self.assertEqual(manifest, actual)

    def test_existing_output_refused_before_verification_or_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, config_path, startup_path = self.native_fixture(root)
            output = root / "existing-output"; output.mkdir()
            (output / "preserve.txt").write_bytes(b"Existing software fixture: do not overwrite.")
            with patch("runtime.native.verify") as verify, \
                    patch.object(pilot, "request_once") as request, \
                    patch.object(pilot, "run_calls") as inference:
                with self.assertRaises(FileExistsError):
                    pilot.execute(call_plan(), output, config_path, startup_path, ENDPOINT)
                verify.assert_not_called(); request.assert_not_called(); inference.assert_not_called()
            self.assertEqual(list(output.iterdir()), [output / "preserve.txt"])
            self.assertEqual((output / "preserve.txt").read_bytes(),
                             b"Existing software fixture: do not overwrite.")

    def test_verification_failure_retains_failure_and_hash_manifest_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config, config_path, startup_path = self.native_fixture(root)
            output = root / "failed-fixture-output"
            with chdir(ROOT), patch("runtime.native.verify", side_effect=ValueError("Fixture mismatch")) as verify, \
                    patch.object(pilot, "request_once") as request, \
                    patch.object(pilot, "run_calls") as inference:
                with self.assertRaisesRegex(ValueError, "Fixture mismatch"):
                    pilot.execute(call_plan(), output, config_path, startup_path, ENDPOINT)
                verify.assert_called_once_with(config)
                request.assert_not_called(); inference.assert_not_called()
            failure = json.loads((output / "execution_failure.json").read_text())
            self.assertEqual(failure["state"], "UNRESOLVED_DO_NOT_RESUME")
            self.assertEqual(failure["error_type"], "ValueError")
            self.assertFalse((output / "summary.json").exists())
            self.assertTrue((output / "source/runtime/cache_intent_contract_pilot.py").is_file())
            self.assert_artifact_manifest(output)

    def test_mocked_preflight_and_loop_snapshot_sources_and_exact_artifact_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config, config_path, startup_path = self.native_fixture(root)
            output = root / "successful-software-fixture-output"
            plan = call_plan()
            metadata = {"data": [{"id": MODEL, "root": config["model_path"]}]}
            summary = {"status": "SOFTWARE_FIXTURE_NO_INFERENCE", "actual_model_calls": 0}

            def fake_preflight(directory, endpoint, method, timeout):
                self.assertEqual((endpoint, method, timeout), (ENDPOINT, "GET", 15))
                Path(directory).mkdir()
                pilot.save_new(Path(directory) / "fixture-metadata.json", metadata)
                return metadata

            def fake_loop(actual_plan, model, directory, endpoint):
                self.assertEqual((actual_plan, model, directory, endpoint), (plan, MODEL, output, ENDPOINT))
                pilot.save_new(directory / "summary.json", summary)
                return summary

            with chdir(ROOT), patch("runtime.native.verify") as verify, \
                    patch.object(pilot, "request_once", side_effect=fake_preflight) as request, \
                    patch.object(pilot, "run_calls", side_effect=fake_loop) as inference:
                self.assertEqual(pilot.execute(plan, output, config_path, startup_path, ENDPOINT), summary)
                verify.assert_called_once_with(config)
                request.assert_called_once(); inference.assert_called_once()
            started = json.loads((output / "started.json").read_text())
            expected_sources = {"runtime/cache_intent_contract_pilot.py", "runtime/cache_intent_prompts.py",
                                "runtime/native.py", "runtime/e1.py", "scripts/fetch_bird_fixed.py",
                                "runtime/__init__.py", "scripts/__init__.py"}
            self.assertEqual(set(started["source_hashes"]), expected_sources)
            for relative, digest in started["source_hashes"].items():
                frozen = output / "source" / relative
                self.assertEqual(frozen.read_bytes(), (ROOT / relative).read_bytes())
                self.assertEqual(pilot.sha(frozen.read_bytes()), digest)
            self.assertEqual(started["plan_sha256"], pilot.sha(pilot.encoded(plan)))
            self.assertEqual(started["native_manifest_sha256"], pilot.sha(pilot.encoded(config)))
            self.assertEqual(started["startup_log_sha256"], pilot.sha(startup_path.read_bytes()))
            self.assertEqual((output / "server-startup.log").read_bytes(), startup_path.read_bytes())
            self.assertFalse((output / "execution_failure.json").exists())
            self.assert_artifact_manifest(output)

    def test_cli_execute_requires_acknowledgement_and_required_paths_before_execute(self):
        cases = [[], ["--ack-unreviewed-contract-only", "--output", "fixture-output",
                      "--startup-log", "fixture-startup.log"],
                 ["--native-config", "fixture-native.json", "--startup-log", "fixture-startup.log",
                  "--output", "fixture-output"]]
        for args in cases:
            with self.subTest(args=args), patch.object(pilot.sys, "argv", ["fixture-cli", "--execute", *args]), \
                    patch.object(pilot, "load_plan", return_value=call_plan()), \
                    patch.object(pilot, "execute") as execute, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    pilot.main()
                self.assertEqual(error.exception.code, 2)
                execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
