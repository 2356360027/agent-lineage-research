"""Read-only NumPy audit of frozen tta_mean_probe.py development artifacts.

Only --run artifacts and an explicitly supplied --source file are read. No
torch, inference, data/checkpoint loading, network, subprocesses, or writes.
This checks artifact consistency, not model correctness or checkpoint origin.
Usage: python scripts/verify_tta_mean_pilot.py --run RUN [--source RUNNER.py]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import numpy as np


CONDITIONS = (
    "clean", "brightness_plus0.1_clipped",
    "contrast0.6_about_perimage_mean", "gaussian_noise0.08_clipped",
)
METHODS = ("frozen", "full_mean", "projected", "matched_norm_full")
BN_PATH = "block1.layer.0.bn1"
GDRIVE_ID = "1t98aEuzeTL8P7Kpd5DIrCoCL21BNZUhC"
MEMORY_LIMIT = 2 * 1024**3
CIFAR_MD5 = {
    "data_batch_1": "c99cafc152244af753f735de768cd75f",
    "data_batch_2": "d4bba439e000b95fd0a9bffe97cbabec",
    "data_batch_3": "54ebc095f3ab1f0389bbae665268c751",
    "data_batch_4": "634d18415352ddfa80567beed471001a",
    "data_batch_5": "482c414d41f54cd18b22e5b47cb7c3cb",
    "test_batch": "40351d587109b95175f43aff81a1287e",
}
ALG_ATOL, ALG_RTOL = 1e-11, 1e-8
PROB_ATOL, PROB_RTOL = 1e-12, 1e-9
METRIC_ATOL, METRIC_RTOL = 2e-8, 2e-6


class AuditError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AuditError(message)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def invalid_constant(value):
    raise AuditError(f"Nonfinite JSON constant: {value}")


def parse_json(text):
    return json.loads(text, object_pairs_hook=unique_keys, parse_constant=invalid_constant)


def finite_array(value, shape, name):
    try:
        array = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise AuditError(f"{name}: expected numeric array") from error
    require(array.shape == shape, f"{name}: shape {array.shape}, expected {shape}")
    require(bool(np.isfinite(array).all()), f"{name}: nonfinite value")
    return array


def close_array(actual, expected, name, atol=ALG_ATOL, rtol=ALG_RTOL):
    value = finite_array(actual, expected.shape, name)
    require(bool(np.allclose(value, expected, atol=atol, rtol=rtol)),
            f"{name}: numeric mismatch (max absolute error {float(np.max(np.abs(value - expected))):.6g})")
    return value


def finite_number(value, name, minimum=None):
    require(isinstance(value, (int, float)) and not isinstance(value, bool),
            f"{name}: expected numeric scalar")
    require(math.isfinite(value), f"{name}: nonfinite scalar")
    if minimum is not None:
        require(value >= minimum, f"{name}: below {minimum}")
    return value


def exact_integer(value, expected, name):
    require(isinstance(value, int) and not isinstance(value, bool) and value == expected,
            f"{name}: expected integer {expected}, received {value!r}")


def close_number(actual, expected, name, atol=ALG_ATOL, rtol=ALG_RTOL):
    finite_number(actual, name)
    require(math.isclose(actual, float(expected), abs_tol=atol, rel_tol=rtol),
            f"{name}: {actual!r} != recomputed {float(expected)!r}")


def digest_string(value, name):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            f"{name}: expected lowercase SHA256")
    return value


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def softmax(logits):
    exponent = np.exp(logits - logits.max(axis=1, keepdims=True))
    return exponent / exponent.sum(axis=1, keepdims=True)


class Auditor:
    def __init__(self, run, source=None):
        self.run = Path(run).resolve(strict=True)
        require(self.run.is_dir(), "--run must be a directory")
        self.source = source
        self.stage = "initialization"
        self.checks_completed = []
        self.report = {}

    def read(self, name):
        # Names are program constants or drawn from the fixed condition/method
        # grid, never from an artifact-supplied path.
        path = self.run / name
        require(path.is_file(), f"Missing required artifact: {name}")
        return parse_json(path.read_text(encoding="utf-8"))

    def done(self):
        self.checks_completed.append(self.stage)

    def audit_status(self):
        self.stage = "completed_status_and_state_freeze_claims"
        self.status = self.read("status.json")
        require(self.status.get("status") == "completed", "Run is not completed")
        require(not (self.run / "failure.json").exists(), "Completed run contains failure.json")
        require(self.status.get("mechanism_diagnostic_only") is True, "Status lacks diagnostic-only scope")
        require(self.status.get("no_model_api") is True, "Status no_model_api must be true")
        events = []
        with (self.run / "status_events.jsonl").open("r", encoding="utf-8") as handle:
            for line in handle:
                require(bool(line.strip()), "Blank status event")
                events.append(parse_json(line))
        require([e.get("status") for e in events] == ["pending", "completed"],
                "Expected exactly pending -> completed; no retry/resume events")
        require(events[-1] == self.status, "Terminal status differs from final status event")
        self.summary = self.read("summary.json")
        for key in ("mechanism_diagnostic_only", "all_non_mean_state_unchanged",
                    "base_state_restored", "accuracy_and_brier_are_exploratory", "no_model_api"):
            require(self.summary.get(key) is True, f"summary.{key} must be true")
        for key in ("confirmatory", "significance_or_ci_computed"):
            require(self.summary.get(key) is False, f"summary.{key} must be false")
        require(self.summary.get("primary_change_measure") == "mean_probability_tv_from_frozen",
                "Wrong primary prediction-change measure")
        self.done()

    def audit_manifest(self):
        self.stage = "manifest_and_original_id_partitions"
        self.manifest = m = self.read("manifest.json")
        require(m.get("seed") == 20261004, "Unexpected selection seed")
        require(m.get("conditions") == list(CONDITIONS), "Unexpected condition grid")
        require(m.get("methods") == list(METHODS), "Unexpected method grid")
        require(m.get("bn_path") == BN_PATH and m.get("bn_feature_dimension") == 16,
                "Wrong predeclared BN layer/dimension")
        require(m.get("coordinates") == "raw layer-input channel means, no whitening",
                "Unexpected coordinate system")
        require(m.get("protocol") == "offline_disjoint_adaptation_and_scoring_not_online_TTA",
                "Unexpected evaluation protocol")
        for key in ("mechanism_diagnostic_only", "no_model_api", "conditions_are_custom_not_CIFAR_C",
                    "base_plus_correction_non_cumulative", "adaptation_mean_uses_all_200_once"):
            require(m.get(key) is True, f"manifest.{key} must be true")
        require(m.get("confirmatory") is False, "Manifest must be non-confirmatory")
        require(m.get("contrast_mean_axes") == "all channels and pixels within each individual image",
                "Unexpected contrast operation")
        self.partitions = {}
        for split, prefix, count, per_class, bound in (
            ("source", "train", 1000, 100, 50000),
            ("adaptation", "test", 200, 20, 10000),
            ("evaluation", "test", 200, 20, 10000),
        ):
            records = m["selection"][split]
            require(isinstance(records, list) and len(records) == count, f"Wrong {split} size")
            by_id, labels = {}, []
            for record in records:
                index, label = record["index"], record["label"]
                require(isinstance(index, int) and not isinstance(index, bool) and 0 <= index < bound,
                        f"Invalid {split} original index")
                require(isinstance(label, int) and not isinstance(label, bool) and 0 <= label < 10,
                        f"Invalid {split} label")
                require(record["id"] == f"{prefix}:{index:05d}", f"Malformed original ID in {split}")
                require(record["id"] not in by_id, f"Duplicate original ID in {split}")
                by_id[record["id"]] = record
                labels.append(label)
            require(np.array_equal(np.bincount(labels, minlength=10), np.full(10, per_class)),
                    f"Wrong per-class original-ID counts in {split}")
            self.partitions[split] = by_id
            exact_integer(m[f"{split}_examples"], count, f"manifest.{split}_examples")
        adapt = set(self.partitions["adaptation"])
        evaluation = set(self.partitions["evaluation"])
        require(not adapt & evaluation, "Adaptation and evaluation IDs overlap")
        excluded = m["exclude_from_any_future_final_evaluation"]
        require(excluded == sorted(adapt | evaluation) and len(excluded) == 400,
                "Future-final exclusion list must contain all 400 development originals exactly once")
        require(set(m["selection"]) == set(self.partitions), "Unexpected extra partition")
        digest_string(m["source_sha256"], "manifest.source_sha256")
        self.report["original_id_counts"] = {k: len(v) for k, v in self.partitions.items()}
        self.done()

    def audit_provenance(self):
        self.stage = "recorded_input_checkpoint_and_optional_source_hashes"
        m = self.manifest
        checkpoint = self.read("checkpoint.json")
        expected = digest_string(m["checkpoint_expected_sha256"], "checkpoint expected SHA256")
        require(digest_string(checkpoint["sha256"], "checkpoint SHA256") == expected,
                "Checkpoint actual/expected recorded SHA256 differ")
        require(checkpoint.get("weights_only") is True and checkpoint.get("strict") is True,
                "Checkpoint must record weights_only and strict loading")
        require(checkpoint.get("official_registry_gdrive_id") == GDRIVE_ID, "Wrong model registry ID")
        require(checkpoint.get("container") in {"state_dict", "raw_state_dict"},
                "Unsupported checkpoint container")
        require(isinstance(checkpoint.get("module_prefix_stripped"), bool), "Missing prefix handling record")
        finite_number(checkpoint["bytes"], "checkpoint.bytes", minimum=1)
        inputs = m["input_files"]
        require(set(inputs) == set(CIFAR_MD5), "Wrong CIFAR input-file set")
        for name, md5 in CIFAR_MD5.items():
            require(inputs[name]["md5"] == md5, f"Wrong official-file MD5 record: {name}")
            digest_string(inputs[name]["sha256"], f"input SHA256: {name}")
            finite_number(inputs[name]["bytes"], f"input bytes: {name}", minimum=1)
        for condition in CONDITIONS:
            info = self.read(f"input_{condition}.json")
            require(info["shape"] == [200, 3, 32, 32] and info["dtype"] == "float32",
                    f"Wrong transformed-input description: {condition}")
            for split in ("adaptation", "evaluation"):
                digest_string(info[f"{split}_float32_sha256"], f"{condition}/{split} SHA256")
        source_check = {"requested": self.source is not None, "matched": None,
                        "recorded_sha256": m["source_sha256"]}
        if self.source is not None:
            actual = file_sha256(self.source)
            require(actual == m["source_sha256"], "--source SHA256 differs from executed-source record")
            source_check.update(matched=True, actual_sha256=actual)
        self.report["source_check"] = source_check
        self.report["checkpoint_recorded_sha256"] = expected
        self.done()

    def audit_calibration(self):
        self.stage = "class_mean_SVD_and_projector_reconstruction"
        self.calibration = c = self.read("calibration.json")
        self.means = means = finite_array(c["class_means"], (10, 16), "source class means")
        self.reference = reference = means.mean(axis=0)
        a = (means - reference).T
        close_array(c["balanced_source_mean"], reference, "balanced source mean")
        close_array(c["A"], a, "centered A")
        u, singular, _ = np.linalg.svd(a, full_matrices=False)
        tolerance = np.finfo(np.float64).eps * max(a.shape) * singular[0]
        self.rank = rank = int(np.count_nonzero(singular > tolerance))
        q = np.eye(16, dtype=np.float64) - u[:, :rank] @ u[:, :rank].T
        self.q = close_array(c["Q"], q, "reconstructed Q")
        close_array(self.q.T, self.q, "Q symmetry")
        close_array(self.q @ self.q, self.q, "Q idempotence")
        close_array(self.q @ a, np.zeros_like(a), "Q annihilates centered class means")
        close_array(c["singular_values"], singular, "singular values")
        close_number(c["svd_tolerance"], tolerance, "SVD machine-epsilon tolerance", atol=0.0)
        exact_integer(c["rank"], rank, "calibration rank")
        exact_integer(self.summary["rank"], rank, "summary rank")
        require(c["svd_rule"] == "float64_eps * max(A.shape) * largest_singular_value",
                "Unexpected rank-selection rule")
        self.base_mean = finite_array(c["base_running_mean"], (16,), "base running mean")
        variance = finite_array(c["base_running_variance"], (16,), "base running variance")
        require(bool((variance >= 0).all()), "Negative base variance")
        require(c["source_original_ids"] == list(self.partitions["source"]),
                "Calibration source-ID order differs from manifest")
        self.report["reconstructed_rank"] = rank
        self.report["projector_max_absolute_error"] = float(np.max(np.abs(self.q - q)))
        self.done()

    def audit_corrections(self):
        self.stage = "delta_projection_and_matched_norm_controls"
        self.corrections = {}
        for condition in CONDITIONS:
            c = self.read(f"correction_{condition}.json")
            target = finite_array(c["target_adaptation_mean"], (16,), f"{condition} target mean")
            delta = close_array(c["delta"], target - self.reference, f"{condition} delta")
            residual = self.q @ delta
            dn, rn = float(np.linalg.norm(delta)), float(np.linalg.norm(residual))
            ratio = rn / dn if dn > 0 else None
            expected = {"frozen": np.zeros(16), "full_mean": delta,
                        "projected": residual,
                        "matched_norm_full": delta * ratio if ratio is not None else np.zeros(16)}
            require(set(c["corrections"]) == set(METHODS), f"Wrong correction methods: {condition}")
            for method, vector in expected.items():
                close_array(c["corrections"][method], vector, f"{condition}/{method} correction")
            close_number(c["delta_norm"], dn, f"{condition} delta norm")
            close_number(c["residual_norm"], rn, f"{condition} residual norm")
            if ratio is None:
                require(c["residual_to_delta_norm_ratio"] is None, "Zero-delta ratio must be null")
            else:
                close_number(c["residual_to_delta_norm_ratio"], ratio, f"{condition} norm ratio")
            exact_integer(c["rank"], self.rank, f"{condition} rank")
            close_number(np.linalg.norm(expected["matched_norm_full"]), rn,
                         f"{condition} matched-norm control")
            self.corrections[condition] = {"vectors": expected, "delta_norm": dn,
                                           "residual_norm": rn, "ratio": ratio}
        self.done()

    def audit_predictions(self):
        self.stage = "3200_predictions_ID_label_grid_and_softmax"
        expected_ids = self.partitions["evaluation"]
        self.cells = {(c, m): {} for c in CONDITIONS for m in METHODS}
        count, max_softmax_error = 0, 0.0
        with (self.run / "predictions.jsonl").open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                require(bool(line.strip()), f"Blank prediction line {line_number}")
                row = parse_json(line)
                cell = (row["condition"], row["method"])
                require(cell in self.cells, f"Unexpected prediction grid cell {cell}")
                original = row["id"]
                require(original in expected_ids, f"Non-evaluation ID in predictions: {original}")
                require(original not in self.cells[cell], f"Duplicate prediction: {cell}/{original}")
                record = expected_ids[original]
                exact_integer(row["original_index"], record["index"], "prediction original index")
                exact_integer(row["y"], record["label"], "prediction label")
                require(row.get("mechanism_diagnostic_only") is True, "Prediction lacks diagnostic-only marker")
                logits = finite_array(row["logits"], (10,), f"logits line {line_number}")
                probs = finite_array(row["probabilities"], (10,), f"probabilities line {line_number}")
                require(bool(((probs >= 0) & (probs <= 1)).all()), "Probability outside [0,1]")
                # Frozen runner saves float32 logits without rounding/truncation.
                require(np.array_equal(logits, logits.astype(np.float32).astype(np.float64)),
                        f"Logits are not exact float32 values at line {line_number}")
                recomputed = softmax(logits[None, :])[0]
                close_array(probs, recomputed, f"softmax line {line_number}", PROB_ATOL, PROB_RTOL)
                close_number(float(probs.sum()), 1.0, "probability sum", PROB_ATOL, PROB_RTOL)
                max_softmax_error = max(max_softmax_error, float(np.max(np.abs(probs - recomputed))))
                self.cells[cell][original] = logits
                count += 1
        exact_integer(count, 3200, "prediction count")
        for cell, rows in self.cells.items():
            require(set(rows) == set(expected_ids) and len(rows) == 200,
                    f"Missing/extra prediction IDs in {cell}")
        self.report["prediction_count"] = count
        self.report["softmax_max_absolute_error"] = max_softmax_error
        self.done()

    def audit_metrics(self):
        self.stage = "independent_16_cell_metric_recomputation"
        summary_cells = {}
        require(isinstance(self.summary["metrics"], list) and len(self.summary["metrics"]) == 16,
                "Expected 16 summary metric cells")
        for value in self.summary["metrics"]:
            cell = (value["condition"], value["method"])
            require(cell in self.cells and cell not in summary_cells, "Duplicate/unexpected summary cell")
            summary_cells[cell] = value
        order = list(self.partitions["evaluation"])
        labels = np.asarray([self.partitions["evaluation"][i]["label"] for i in order], dtype=np.int64)
        truth = np.eye(10, dtype=np.float64)[labels]
        self.report["recomputed_metrics"] = []
        for condition in CONDITIONS:
            baseline = np.stack([self.cells[condition, "frozen"][i] for i in order]).astype(np.float32)
            baseline_probs = softmax(baseline.astype(np.float64))
            baseline_pred = baseline_probs.argmax(axis=1)
            correction = self.corrections[condition]
            for method in METHODS:
                cell = (condition, method)
                logits = np.stack([self.cells[cell][i] for i in order]).astype(np.float32)
                probs = softmax(logits.astype(np.float64))
                pred = probs.argmax(axis=1)
                expected = {
                    "examples": 200,
                    "mean_probability_tv_from_frozen": float((0.5 * np.abs(probs - baseline_probs).sum(axis=1)).mean()),
                    "argmax_changed_count": int(np.count_nonzero(pred != baseline_pred)),
                    "mean_logit_l2_change": float(np.linalg.norm(logits - baseline, axis=1).mean()),
                    "accuracy_exploratory": float(np.mean(pred == labels)),
                    "brier_sum_classes_exploratory": float(((probs - truth)**2).sum(axis=1).mean()),
                    "corrected_from_frozen_count_exploratory": int(np.count_nonzero((baseline_pred != labels) & (pred == labels))),
                    "harmed_from_frozen_count_exploratory": int(np.count_nonzero((baseline_pred == labels) & (pred != labels))),
                }
                vector = correction["vectors"][method]
                applied_mean = self.base_mean.astype(np.float32) + vector.astype(np.float32)
                extra = {
                    "correction_norm": float(np.linalg.norm(vector)),
                    "delta_norm": correction["delta_norm"],
                    "residual_norm": correction["residual_norm"],
                    "applied_float32_correction_norm": float(np.linalg.norm(applied_mean.astype(np.float64) - self.base_mean.astype(np.float32).astype(np.float64))),
                }
                standalone = self.read(f"metrics_{condition}_{method}.json")
                for location, saved in (("summary", summary_cells[cell]), ("standalone", standalone)):
                    require((saved["condition"], saved["method"]) == cell, "Mislabeled metric artifact")
                    exact_integer(saved["rank"], self.rank, f"{cell}/{location} rank")
                    for key, value in expected.items():
                        name = f"{cell}/{location}/{key}"
                        if isinstance(value, int):
                            exact_integer(saved[key], value, name)
                        else:
                            close_number(saved[key], value, name, METRIC_ATOL, METRIC_RTOL)
                    for key, value in extra.items():
                        close_number(saved[key], value, f"{cell}/{location}/{key}")
                    ratio = correction["ratio"]
                    if ratio is None:
                        require(saved["residual_to_delta_norm_ratio"] is None, "Zero-delta metric ratio must be null")
                    else:
                        close_number(saved["residual_to_delta_norm_ratio"], ratio, f"{cell}/{location}/ratio")
                    close_array(saved["applied_running_mean"], applied_mean.astype(np.float64),
                                f"{cell}/{location}/absolute base+correction mean", atol=0.0, rtol=0.0)
                self.report["recomputed_metrics"].append({"condition": condition, "method": method, **expected})
        self.done()

    def audit_cost(self):
        self.stage = "5000_completed_forwards_and_recorded_GPU_limits"
        cost, device = self.read("cost.json"), self.read("device.json")
        require(cost.get("no_model_api") is True, "Cost no_model_api must be true")
        exact_integer(cost["forward_examples_completed"], 5000, "completed forward examples")
        exact_integer(cost["forward_examples_attempted"], 5000, "attempted forward examples")
        batch = self.manifest["microbatch_size"]
        require(isinstance(batch, int) and not isinstance(batch, bool) and 1 <= batch <= 8,
                "Invalid microbatch size")
        expected = {"source_calibration": 1000}
        for condition in CONDITIONS:
            expected[f"{condition}/adaptation"] = 200
            expected.update({f"{condition}/{method}": 200 for method in METHODS})
        require(set(cost["phases"]) == set(expected), "Missing/extra forward phases")
        total = 0
        for phase, size in expected.items():
            entry = cost["phases"][phase]
            for key in ("forward_examples", "forward_examples_attempted"):
                exact_integer(entry[key], size, f"{phase}/{key}")
            for key in ("forward_batches", "forward_batches_attempted"):
                exact_integer(entry[key], math.ceil(size / batch), f"{phase}/{key}")
            finite_number(entry["forward_wall_seconds"], f"{phase}/wall", minimum=0)
            finite_number(entry["cuda_forward_milliseconds"], f"{phase}/CUDA milliseconds", minimum=0)
            total += entry["forward_examples"]
        exact_integer(total, 5000, "sum of forward examples")
        for key in ("max_memory_allocated_bytes", "max_memory_reserved_bytes"):
            value = finite_number(cost[key], key, minimum=0)
            require(value <= MEMORY_LIMIT, f"Recorded {key} exceeds 2 GiB")
        require(cost["max_memory_allocated_bytes"] <= cost["max_memory_reserved_bytes"],
                "Allocated peak exceeds reserved peak")
        finite_number(cost["wall_seconds"], "wall seconds", minimum=0)
        require(device["device"] == "cuda:0", "Unexpected execution device")
        total_memory = finite_number(device["total_memory_bytes"], "device total memory", minimum=1)
        close_number(device["allocator_fraction_effective"], min(0.08, MEMORY_LIMIT / total_memory),
                     "effective allocator fraction", atol=1e-14)
        close_number(self.manifest["gpu_allocator_fraction_max"], 0.08, "allocator fraction max")
        exact_integer(self.manifest["gpu_allocator_peak_limit_bytes"], MEMORY_LIMIT, "allocator memory limit")
        digest_string(device["baseline_state_sha256"], "baseline state SHA256")
        digest_string(device["immutable_state_sha256"], "immutable state SHA256")
        self.report["cost"] = {key: cost[key] for key in (
            "wall_seconds", "forward_examples_completed", "forward_examples_attempted",
            "max_memory_allocated_bytes", "max_memory_reserved_bytes")}
        self.done()

    def verify(self):
        self.audit_status()
        self.audit_manifest()
        self.audit_provenance()
        self.audit_calibration()
        self.audit_corrections()
        self.audit_predictions()
        self.audit_metrics()
        self.audit_cost()
        return {
            "audit_status": "passed", "run": str(self.run),
            "mechanism_diagnostic_only": True, "confirmatory": False,
            "read_only": True, "model_inference_performed": False,
            "checks_completed": self.checks_completed,
            "tolerances": {"algebra_atol": ALG_ATOL, "algebra_rtol": ALG_RTOL,
                           "softmax_atol": PROB_ATOL, "softmax_rtol": PROB_RTOL,
                           "metric_atol": METRIC_ATOL, "metric_rtol": METRIC_RTOL},
            "limits": [
                "Artifact consistency only; no independent re-inference or accuracy claim.",
                "State freeze is checked from runner-recorded flags/hashes, not loaded model tensors.",
                "Dataset and checkpoint bytes are not reopened; their recorded hashes and expected values are compared.",
                "ID partitions and class counts are checked; sampling from the full official label arrays is not replayed.",
                "An optional --source checks the exact runner bytes; omitted means source bytes were not independently checked.",
                "Source statistics and target means are not re-extracted from images; their algebraic consequences are checked.",
            ], **self.report,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--source", type=Path, help="Optional exact executed runner source to hash, never import")
    args = parser.parse_args(argv)
    auditor = None
    try:
        auditor = Auditor(args.run, args.source)
        report = auditor.verify()
    except Exception as error:
        report = {"audit_status": "failed", "read_only": True,
                  "model_inference_performed": False, "error_type": type(error).__name__,
                  "error": str(error), "stage": auditor.stage if auditor else "initialization",
                  "checks_completed": auditor.checks_completed if auditor else []}
        print(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False))
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
