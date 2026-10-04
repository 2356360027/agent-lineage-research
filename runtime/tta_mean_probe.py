"""Offline, development-only BN-mean mechanism diagnostic; never a TTA benchmark.

No downloads, model APIs, torchvision, training, automatic retries, or resumes.
CIFAR pickle is executable serialization: only operator-trusted official files
whose fixed MD5 matches are deserialized. MD5 is an integrity check, NOT proof
of provenance. The operator must separately verify the official source.

Architecture adapted from RobustBench's model_zoo/architectures/wide_resnet.py
(itself based on https://github.com/yaodongyu/TRADES), preserving state_dict
names and forward semantics. Source and license inspected 2026-10-04:
https://github.com/RobustBench/robustbench/blob/master/robustbench/model_zoo/architectures/wide_resnet.py
https://github.com/RobustBench/robustbench/blob/master/LICENSE
Copyright (c) 2020 Francesco Croce, Maksym Andriushchenko, Vikash Sehwag

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pickle
import platform
import re
import sys
import time
import traceback
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


SEED = 20261004
BN_PATH = "block1.layer.0.bn1"
MEAN_KEY = BN_PATH + ".running_mean"
METHODS = ("frozen", "full_mean", "projected", "matched_norm_full")
CONDITIONS = (
    "clean", "brightness_plus0.1_clipped",
    "contrast0.6_about_perimage_mean", "gaussian_noise0.08_clipped",
)
GPU_MEMORY_LIMIT = 2 * 1024**3
GPU_MEMORY_FRACTION = 0.08
CHECKPOINT_GDRIVE_ID = "1t98aEuzeTL8P7Kpd5DIrCoCL21BNZUhC"
REGISTRY_URL = "https://github.com/RobustBench/robustbench/blob/master/robustbench/model_zoo/cifar10.py"
# Fixed official-file integrity values also published by the PyTorch maintainers:
# https://github.com/pytorch/vision/blob/main/torchvision/datasets/cifar.py
CIFAR_MD5 = {
    "data_batch_1": "c99cafc152244af753f735de768cd75f",
    "data_batch_2": "d4bba439e000b95fd0a9bffe97cbabec",
    "data_batch_3": "54ebc095f3ab1f0389bbae665268c751",
    "data_batch_4": "634d18415352ddfa80567beed471001a",
    "data_batch_5": "482c414d41f54cd18b22e5b47cb7c3cb",
    "test_batch": "40351d587109b95175f43aff81a1287e",
}


class BasicBlock(nn.Module):
    """RobustBench preactivation block, including its activated shortcut."""

    def __init__(self, in_planes, out_planes, stride):
        super().__init__()
        self.bn1 = nn.BatchNorm2d(in_planes)
        self.relu1 = nn.ReLU(inplace=True)
        self.conv1 = nn.Conv2d(in_planes, out_planes, 3, stride, 1, bias=False)
        self.bn2 = nn.BatchNorm2d(out_planes)
        self.relu2 = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(out_planes, out_planes, 3, 1, 1, bias=False)
        self.equalInOut = in_planes == out_planes
        self.convShortcut = None if self.equalInOut else nn.Conv2d(
            in_planes, out_planes, 1, stride, 0, bias=False
        )

    def forward(self, x):
        if not self.equalInOut:
            x = self.relu1(self.bn1(x))
            out = x
        else:
            out = self.relu1(self.bn1(x))
        out = self.conv2(self.relu2(self.bn2(self.conv1(out))))
        return (x if self.equalInOut else self.convShortcut(x)) + out


class NetworkBlock(nn.Module):
    def __init__(self, count, in_planes, out_planes, stride):
        super().__init__()
        self.layer = nn.Sequential(*[
            BasicBlock(in_planes if i == 0 else out_planes, out_planes,
                       stride if i == 0 else 1)
            for i in range(count)
        ])

    def forward(self, x):
        return self.layer(x)


class WideResNet(nn.Module):
    """The Standard registry architecture; smaller constructor is for fixtures."""

    def __init__(self, depth=28, widen_factor=10, num_classes=10):
        super().__init__()
        if depth < 10 or (depth - 4) % 6:
            raise ValueError("Invalid WideResNet depth")
        widths = (16, 16 * widen_factor, 32 * widen_factor, 64 * widen_factor)
        count = (depth - 4) // 6
        self.conv1 = nn.Conv2d(3, widths[0], 3, 1, 1, bias=False)
        self.block1 = NetworkBlock(count, widths[0], widths[1], 1)
        self.block2 = NetworkBlock(count, widths[1], widths[2], 2)
        self.block3 = NetworkBlock(count, widths[2], widths[3], 2)
        self.bn1 = nn.BatchNorm2d(widths[3])
        self.relu = nn.ReLU(inplace=True)
        self.fc = nn.Linear(widths[3], num_classes, bias=True)
        self.nChannels = widths[3]

    def forward(self, x):
        out = self.block3(self.block2(self.block1(self.conv1(x))))
        out = F.avg_pool2d(self.relu(self.bn1(out)), 8)
        return self.fc(out.view(-1, self.nChannels))


class Deadline:
    """Cooperative deadline: checked around file operations and each batch."""

    def __init__(self, seconds):
        self.started = time.monotonic()
        self.seconds = seconds

    def check(self):
        if time.monotonic() - self.started >= self.seconds:
            raise TimeoutError("Mechanism diagnostic reached its wall-time limit")

    @property
    def elapsed(self):
        return time.monotonic() - self.started


def write_json_new(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def create_output(path):
    path = Path(path).absolute()
    # No exist_ok, resume, deletion, or creation of a missing parent tree.
    path.mkdir(exist_ok=False)
    return path


def set_status(output, state, **fields):
    value = {"status": state, "unix_time": time.time(), **fields}
    # Only our freshly-created output directory is mutable. Preserve all events.
    with (output / "status_events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, allow_nan=False) + "\n")
        handle.flush()
    temporary = output / "status.next.json"
    write_json_new(temporary, value)
    os.replace(temporary, output / "status.json")


def read_and_hash(path, deadline):
    deadline.check()
    content = bytearray()
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            deadline.check()
            content.extend(block)
    result = bytes(content)
    deadline.check()
    return result, hashlib.sha256(result).hexdigest()


def validate_cifar_entry(entry, name):
    if not isinstance(entry, dict) or "data" not in entry or "labels" not in entry:
        raise ValueError(f"Unexpected CIFAR schema: {name}")
    images, labels = entry["data"], np.asarray(entry["labels"])
    if not isinstance(images, np.ndarray) or images.dtype != np.uint8:
        raise ValueError(f"Unexpected CIFAR image dtype: {name}")
    if images.shape != (10000, 3072) or labels.shape != (10000,):
        raise ValueError(f"Unexpected CIFAR dimensions: {name}")
    if labels.dtype.kind not in "iu" or np.any((labels < 0) | (labels >= 10)):
        raise ValueError(f"Unexpected CIFAR labels: {name}")
    return images.reshape(-1, 3, 32, 32), labels.astype(np.int64)


def load_cifar(root, trusted, deadline):
    if not trusted:
        raise ValueError("Refusing pickle without --trust-verified-official-cifar")
    root = Path(root).resolve(strict=True)
    if (root / "cifar-10-batches-py").is_dir():
        root = root / "cifar-10-batches-py"
    train_x, train_y, provenance = [], [], {}
    for name, expected_md5 in CIFAR_MD5.items():
        data, sha256 = read_and_hash(root / name, deadline)
        actual_md5 = hashlib.md5(data).hexdigest()
        if actual_md5 != expected_md5:
            raise ValueError(f"CIFAR MD5 mismatch, before unpickling: {name}")
        # No arbitrary filename/checksum override is exposed. Source trust is
        # still necessary: known MD5 values are not cryptographic provenance.
        images, labels = validate_cifar_entry(pickle.loads(data, encoding="latin1"), name)
        deadline.check()
        provenance[name] = {"path": str(root / name), "md5": actual_md5,
                            "sha256": sha256, "bytes": len(data)}
        if name == "test_batch":
            test_x, test_y = images, labels
        else:
            train_x.append(images)
            train_y.append(labels)
    return (np.concatenate(train_x), np.concatenate(train_y), test_x, test_y,
            provenance)


def select_partitions(train_labels, test_labels, seed=SEED):
    """Select original IDs, not corrupted views; fixed 100/20/20 per class."""
    source_rng = np.random.default_rng(np.random.SeedSequence([seed, 0]))
    target_rng = np.random.default_rng(np.random.SeedSequence([seed, 1]))
    source, adaptation, evaluation = [], [], []
    for label in range(10):
        train_ids = np.flatnonzero(np.asarray(train_labels) == label)
        test_ids = np.flatnonzero(np.asarray(test_labels) == label)
        if len(train_ids) < 100 or len(test_ids) < 40:
            raise ValueError("Insufficient distinct original IDs in a class")
        source.extend(source_rng.choice(train_ids, 100, replace=False).tolist())
        selected = target_rng.choice(test_ids, 40, replace=False)
        adaptation.extend(selected[:20].tolist())
        evaluation.extend(selected[20:].tolist())
    if set(adaptation) & set(evaluation):
        raise AssertionError("Adaptation/evaluation original IDs overlap")
    return {"source": source, "adaptation": adaptation, "evaluation": evaluation}


def checkpoint_state(payload):
    # RobustBench utils.load_model tries checkpoint['state_dict'], otherwise a
    # raw state_dict. Standard has no 'last' or other model-specific branch:
    # https://github.com/RobustBench/robustbench/blob/master/robustbench/utils.py
    if not isinstance(payload, Mapping):
        raise ValueError("Checkpoint must contain a tensor state_dict, not a model")
    if "state_dict" in payload:
        payload = payload["state_dict"]
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("Empty or invalid state_dict")
    if not all(isinstance(k, str) and isinstance(v, torch.Tensor)
               for k, v in payload.items()):
        raise ValueError("state_dict must map strings to tensors only")
    flags = [key.startswith("module.") for key in payload]
    if any(flags) and not all(flags):
        raise ValueError("Mixed module. prefixes are not accepted")
    stripped = all(flags)
    state = {(k[7:] if stripped else k): v for k, v in payload.items()}
    return state, stripped


def load_checkpoint(model, path, expected_sha256, deadline):
    if re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None:
        raise ValueError("--checkpoint-sha256 must be a provisioner-verified SHA256")
    content, actual = read_and_hash(path, deadline)
    if actual != expected_sha256.lower():
        raise ValueError("Checkpoint SHA256 mismatch")
    # Fail rather than falling back to unsafe pickle on older torch versions.
    payload = torch.load(io.BytesIO(content), map_location="cpu", weights_only=True)
    container = "state_dict" if isinstance(payload, Mapping) and "state_dict" in payload else "raw_state_dict"
    ignored_keys = sorted(str(k) for k in payload if k != "state_dict") if container == "state_dict" else []
    state, stripped = checkpoint_state(payload)
    expected = model.state_dict()
    if set(state) != set(expected):
        raise ValueError("Checkpoint keys do not exactly match Standard WRN-28-10")
    for key, value in state.items():
        if value.shape != expected[key].shape or value.dtype != expected[key].dtype:
            raise ValueError(f"Checkpoint shape/dtype mismatch: {key}")
        if not bool(torch.isfinite(value).all()):
            raise ValueError(f"Nonfinite checkpoint tensor: {key}")
    model.load_state_dict(state, strict=True)
    deadline.check()
    return {"path": str(Path(path).resolve()), "sha256": actual,
            "bytes": len(content), "module_prefix_stripped": stripped,
            "container": container, "ignored_top_level_keys": ignored_keys,
            "weights_only": True, "strict": True, "registry_url": REGISTRY_URL,
            "official_registry_gdrive_id": CHECKPOINT_GDRIVE_ID,
            "provenance_boundary": "SHA256 supplied by operator; origin not authenticated by runner"}


def freeze_model(model):
    model.eval()
    model.requires_grad_(False)
    return model


def state_digest(model, exclude=()):
    digest = hashlib.sha256()
    for key, value in sorted(model.state_dict().items()):
        if key in exclude:
            continue
        array = value.detach().cpu().contiguous().numpy()
        digest.update(key.encode("utf-8"))
        digest.update(str((array.shape, array.dtype.str)).encode("ascii"))
        digest.update(array.tobytes())
    return digest.hexdigest()


def projected_subspace(class_means):
    means = np.asarray(class_means, dtype=np.float64)
    if means.ndim != 2 or not np.isfinite(means).all():
        raise ValueError("Expected finite [classes, channels] source means")
    reference = means.mean(axis=0)
    a = (means - reference).T
    u, singular, _ = np.linalg.svd(a, full_matrices=False)
    tolerance = np.finfo(np.float64).eps * max(a.shape) * (singular[0] if len(singular) else 0.0)
    rank = int(np.count_nonzero(singular > tolerance))
    q = np.eye(means.shape[1], dtype=np.float64) - u[:, :rank] @ u[:, :rank].T
    return reference, a, q, rank, singular, float(tolerance)


def correction_vectors(delta, q):
    delta = np.asarray(delta, dtype=np.float64)
    residual = np.asarray(q, dtype=np.float64) @ delta
    delta_norm = float(np.linalg.norm(delta))
    residual_norm = float(np.linalg.norm(residual))
    ratio = residual_norm / delta_norm if delta_norm > 0.0 else None
    matched = delta * ratio if ratio is not None else np.zeros_like(delta)
    return {"frozen": np.zeros_like(delta), "full_mean": delta.copy(),
            "projected": residual, "matched_norm_full": matched}, {
                "delta_norm": delta_norm, "residual_norm": residual_norm,
                "residual_to_delta_norm_ratio": ratio,
            }


def set_mean_correction(model, base_mean, delta):
    bn = model.get_submodule(BN_PATH)
    if not isinstance(bn, nn.BatchNorm2d) or bn.num_features != 16:
        raise ValueError("The predeclared earliest BN path/dimension changed")
    if any(module.training for module in model.modules()):
        raise ValueError("All model modules must remain in eval mode")
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise ValueError("All model parameters must remain frozen")
    base = torch.as_tensor(base_mean, dtype=bn.running_mean.dtype, device=bn.running_mean.device)
    change = torch.as_tensor(delta, dtype=base.dtype, device=base.device)
    if (base.shape != (16,) or change.shape != (16,)
            or not bool(torch.isfinite(base).all()) or not bool(torch.isfinite(change).all())):
        raise ValueError("Invalid base mean or correction")
    with torch.no_grad():
        bn.running_mean.copy_(base + change)  # Absolute base + delta, never +=.


def transform_images(images, original_ids, condition):
    x = images.astype(np.float32) / np.float32(255.0)
    if condition == "clean":
        return x
    if condition == "brightness_plus0.1_clipped":
        return np.clip(x + np.float32(0.1), 0.0, 1.0)
    if condition == "contrast0.6_about_perimage_mean":
        mean = x.mean(axis=(1, 2, 3), keepdims=True, dtype=np.float32)
        return np.clip(mean + np.float32(0.6) * (x - mean), 0.0, 1.0)
    if condition == "gaussian_noise0.08_clipped":
        for i, original_id in enumerate(original_ids):
            rng = np.random.default_rng(np.random.SeedSequence([SEED, 3, int(original_id)]))
            noise = rng.normal(0.0, 0.08, x[i].shape).astype(np.float32)
            x[i] = np.clip(x[i] + noise, 0.0, 1.0)
        return x
    raise ValueError(f"Unknown, non-predeclared condition: {condition}")


def cuda_memory(device):
    if device.type != "cuda":
        return {"max_memory_allocated_bytes": None, "max_memory_reserved_bytes": None}
    return {"max_memory_allocated_bytes": int(torch.cuda.max_memory_allocated(device)),
            "max_memory_reserved_bytes": int(torch.cuda.max_memory_reserved(device))}


def check_gpu_memory(device):
    memory = cuda_memory(device)
    if device.type == "cuda" and max(memory.values()) > GPU_MEMORY_LIMIT:
        raise MemoryError("CUDA allocator peak exceeded the fixed 2 GiB limit")
    return memory


def forward_batches(model, images, device, batch_size, deadline, cost,
                    phase, collect_features=False, on_batch=None):
    """No labels enter model evaluation or feature collection."""
    if not 1 <= batch_size <= 8:
        raise ValueError("batch_size must be in [1, 8]")
    if any(m.training for m in model.modules()):
        raise ValueError("A model module left eval mode")
    features, logits, captured = [], [], []
    hook = None
    if collect_features:
        def capture(_module, inputs):
            captured.append(inputs[0].detach().to(torch.float64).mean(dim=(2, 3)))
        hook = model.get_submodule(BN_PATH).register_forward_pre_hook(capture)
    phase_cost = cost.setdefault(phase, {"forward_examples": 0, "forward_batches": 0,
                                      "forward_examples_attempted": 0, "forward_batches_attempted": 0,
                                      "forward_wall_seconds": 0.0,
                                      "cuda_forward_milliseconds": 0.0 if device.type == "cuda" else None})
    try:
        with torch.inference_mode():
            for start in range(0, len(images), batch_size):
                deadline.check()
                check_gpu_memory(device)
                wall_start = time.monotonic()
                tensor = torch.from_numpy(np.ascontiguousarray(images[start:start + batch_size])).to(device)
                event_start = event_end = None
                if device.type == "cuda":
                    event_start = torch.cuda.Event(enable_timing=True)
                    event_end = torch.cuda.Event(enable_timing=True)
                    event_start.record()
                phase_cost["forward_examples_attempted"] += len(tensor)
                phase_cost["forward_batches_attempted"] += 1
                output = model(tensor)
                if device.type == "cuda":
                    event_end.record()
                    event_end.synchronize()
                    phase_cost["cuda_forward_milliseconds"] += float(event_start.elapsed_time(event_end))
                values = output.detach().cpu().numpy().copy()
                if values.shape != (len(tensor), 10) or not np.isfinite(values).all():
                    raise ValueError("Nonfinite or malformed logits")
                if collect_features:
                    if len(captured) != 1:
                        raise AssertionError("Earliest BN hook must fire exactly once per forward")
                    current = captured.pop().cpu().numpy().copy()
                    if current.shape != (len(tensor), 16) or not np.isfinite(current).all():
                        raise ValueError("Nonfinite or malformed layer-input features")
                    features.append(current)
                logits.append(values)
                phase_cost["forward_examples"] += len(tensor)
                phase_cost["forward_batches"] += 1
                phase_cost["forward_wall_seconds"] += time.monotonic() - wall_start
                if on_batch is not None:
                    on_batch(start, values)
                del tensor, output
                check_gpu_memory(device)
                deadline.check()
    finally:
        if hook is not None:
            hook.remove()
    return np.concatenate(logits), np.concatenate(features) if collect_features else None


def probabilities(logits):
    values = np.asarray(logits, dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite logits before softmax")
    exponent = np.exp(values - values.max(axis=1, keepdims=True))
    probs = exponent / exponent.sum(axis=1, keepdims=True)
    if not np.isfinite(probs).all():
        raise ValueError("Nonfinite probabilities")
    return probs


def prediction_metrics(logits, frozen_logits, labels):
    probs, frozen_probs = probabilities(logits), probabilities(frozen_logits)
    pred, frozen_pred = probs.argmax(axis=1), frozen_probs.argmax(axis=1)
    truth = np.eye(10, dtype=np.float64)[labels]
    return {"examples": len(labels),
            "mean_probability_tv_from_frozen": float((0.5 * np.abs(probs - frozen_probs).sum(axis=1)).mean()),
            "argmax_changed_count": int(np.count_nonzero(pred != frozen_pred)),
            "mean_logit_l2_change": float(np.linalg.norm(logits - frozen_logits, axis=1).mean()),
            "accuracy_exploratory": float(np.mean(pred == labels)),
            "brier_sum_classes_exploratory": float(np.mean(((probs - truth)**2).sum(axis=1))),
            "corrected_from_frozen_count_exploratory": int(np.count_nonzero((frozen_pred != labels) & (pred == labels))),
            "harmed_from_frozen_count_exploratory": int(np.count_nonzero((frozen_pred == labels) & (pred != labels)))}


def execute(args, output, deadline, cost):
    if not args.trust_verified_official_cifar:
        raise ValueError("Explicit official-data trust acknowledgement is required")
    if not 1 <= args.batch_size <= 8 or not 0 < args.max_seconds <= 1800:
        raise ValueError("Require 1 <= batch_size <= 8 and 0 < max_seconds <= 1800")
    device = torch.device(args.device)
    if device.type != "cuda" or device.index != 0:
        raise ValueError("This runner is restricted to --device cuda:0; CPU is for unit fixtures only")
    train_x, train_y, test_x, test_y, input_provenance = load_cifar(
        args.cifar_root, args.trust_verified_official_cifar, deadline
    )
    selected = select_partitions(train_y, test_y)
    records = {}
    for split, ids in selected.items():
        prefix, labels = ("train", train_y) if split == "source" else ("test", test_y)
        records[split] = [{"id": f"{prefix}:{i:05d}", "index": i, "label": int(labels[i])} for i in ids]
    # Must be durable before constructing/loading a model or doing any inference.
    manifest = {
        "schema_version": 1, "mechanism_diagnostic_only": True,
        "confirmatory": False, "no_model_api": True,
        "protocol": "offline_disjoint_adaptation_and_scoring_not_online_TTA",
        "seed": SEED, "selection": records,
        "exclude_from_any_future_final_evaluation": sorted(r["id"] for split in ("adaptation", "evaluation") for r in records[split]),
        "source_examples": 1000, "adaptation_examples": 200, "evaluation_examples": 200,
        "input_files": input_provenance,
        "source_file": str(Path(__file__).resolve()),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model": "RobustBench CIFAR-10 Standard WideResNet-28-10",
        "checkpoint_expected_sha256": args.checkpoint_sha256.lower(),
        "checkpoint_path": str(Path(args.checkpoint).resolve()),
        "bn_path": BN_PATH, "coordinates": "raw layer-input channel means, no whitening",
        "bn_feature_dimension": 16, "class_subspace": "all 10 centered source class means; no rank tuning",
        "conditions": list(CONDITIONS), "conditions_are_custom_not_CIFAR_C": True,
        "contrast_mean_axes": "all channels and pixels within each individual image",
        "methods": list(METHODS), "base_plus_correction_non_cumulative": True,
        "microbatch_size": args.batch_size, "adaptation_mean_uses_all_200_once": True,
        "max_seconds": args.max_seconds, "deadline_kind": "cooperative checks per batch, not hard preemption",
        "gpu_allocator_fraction_max": GPU_MEMORY_FRACTION,
        "gpu_allocator_peak_limit_bytes": GPU_MEMORY_LIMIT,
        "gpu_limit_scope": "torch allocator only; not entire process/context/device usage",
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "torch": torch.__version__, "numpy": np.__version__,
                        "torch_cuda": torch.version.cuda},
        "statistical_scope": "descriptive development diagnostic; no significance tests or confidence intervals",
    }
    write_json_new(output / "manifest.json", manifest)
    deadline.check()
    torch.manual_seed(SEED)
    model = WideResNet()
    checkpoint = load_checkpoint(model, args.checkpoint, args.checkpoint_sha256, deadline)
    write_json_new(output / "checkpoint.json", checkpoint)
    freeze_model(model)
    baseline_state_hash = state_digest(model)
    immutable_state_hash = state_digest(model, exclude=(MEAN_KEY,))
    base_mean = model.get_submodule(BN_PATH).running_mean.detach().cpu().numpy().copy()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; there is no CPU experiment fallback")
    torch.cuda.set_device(device)
    total_memory = int(torch.cuda.get_device_properties(device).total_memory)
    effective_fraction = min(GPU_MEMORY_FRACTION, GPU_MEMORY_LIMIT / total_memory)
    torch.cuda.set_per_process_memory_fraction(effective_fraction, device)
    torch.cuda.reset_peak_memory_stats(device)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    model.to(device)
    check_gpu_memory(device)
    write_json_new(output / "device.json", {
        "name": torch.cuda.get_device_name(device),
        "total_memory_bytes": total_memory,
        "allocator_fraction_effective": effective_fraction,
        "device": str(device), "baseline_state_sha256": baseline_state_hash,
        "immutable_state_sha256": immutable_state_hash,
    })
    source_ids = selected["source"]
    source_images = train_x[source_ids].astype(np.float32) / np.float32(255.0)
    _, source_features = forward_batches(model, source_images, device, args.batch_size,
                                        deadline, cost, "source_calibration", True)
    source_labels = train_y[source_ids]
    means = np.stack([source_features[source_labels == c].mean(axis=0) for c in range(10)])
    reference, a, q, rank, singular, tolerance = projected_subspace(means)
    mixture_weights = np.arange(1, 11, dtype=np.float64)
    mixture_weights /= mixture_weights.sum()
    mixture_delta = mixture_weights @ means - reference
    write_json_new(output / "calibration.json", {
        "class_means": means.tolist(), "balanced_source_mean": reference.tolist(),
        "A": a.tolist(), "Q": q.tolist(), "rank": rank,
        "singular_values": singular.tolist(), "svd_tolerance": tolerance,
        "svd_rule": "float64_eps * max(A.shape) * largest_singular_value",
        "base_running_mean": base_mean.tolist(),
        "base_running_variance": model.get_submodule(BN_PATH).running_var.detach().cpu().tolist(),
        "source_original_ids": [r["id"] for r in records["source"]],
        "algebraic_mixture_check_not_label_shift_experiment": {
            "weights": mixture_weights.tolist(),
            "delta_norm": float(np.linalg.norm(mixture_delta)),
            "projected_norm": float(np.linalg.norm(q @ mixture_delta)),
            "note": "Mixture of the same estimated class means; construction check only, not population robustness evidence",
        },
    })
    metrics = []
    with (output / "predictions.jsonl").open("x", encoding="utf-8") as prediction_file:
        for condition in CONDITIONS:
            deadline.check()
            adapt_ids, eval_ids = selected["adaptation"], selected["evaluation"]
            adapt_x = transform_images(test_x[adapt_ids], adapt_ids, condition)
            eval_x = transform_images(test_x[eval_ids], eval_ids, condition)
            write_json_new(output / f"input_{condition}.json", {
                "adaptation_float32_sha256": hashlib.sha256(adapt_x.tobytes()).hexdigest(),
                "evaluation_float32_sha256": hashlib.sha256(eval_x.tobytes()).hexdigest(),
                "shape": list(adapt_x.shape), "dtype": str(adapt_x.dtype),
            })
            set_mean_correction(model, base_mean, np.zeros(16))
            _, adapt_features = forward_batches(model, adapt_x, device, args.batch_size,
                                                deadline, cost, f"{condition}/adaptation", True)
            target_mean = adapt_features.mean(axis=0)
            delta = target_mean - reference
            vectors, norms = correction_vectors(delta, q)
            write_json_new(output / f"correction_{condition}.json", {
                "target_adaptation_mean": target_mean.tolist(), "delta": delta.tolist(),
                "corrections": {k: v.tolist() for k, v in vectors.items()},
                "rank": rank, **norms,
            })
            frozen_logits = None
            for method in METHODS:
                deadline.check()
                set_mean_correction(model, base_mean, vectors[method])

                def record_batch(start, values):
                    probs = probabilities(values)
                    for offset, (row, prob) in enumerate(zip(values, probs)):
                        index = eval_ids[start + offset]
                        record = {"id": f"test:{index:05d}", "original_index": index,
                                  "y": int(test_y[index]), "condition": condition,
                                  "method": method, "logits": row.tolist(),
                                  "probabilities": prob.tolist(),
                                  "mechanism_diagnostic_only": True}
                        prediction_file.write(json.dumps(record, allow_nan=False) + "\n")
                    prediction_file.flush()

                logits, _ = forward_batches(model, eval_x, device, args.batch_size,
                                            deadline, cost, f"{condition}/{method}", on_batch=record_batch)
                if method == "frozen":
                    frozen_logits = logits.copy()
                applied_mean = model.get_submodule(BN_PATH).running_mean.detach().cpu().numpy().copy()
                result = {"condition": condition, "method": method, "rank": rank,
                          "correction_norm": float(np.linalg.norm(vectors[method])), **norms,
                          "applied_running_mean": applied_mean.tolist(),
                          "applied_float32_correction_norm": float(np.linalg.norm(applied_mean.astype(np.float64) - base_mean.astype(np.float64))),
                          **prediction_metrics(logits, frozen_logits, test_y[eval_ids])}
                metrics.append(result)
                write_json_new(output / f"metrics_{condition}_{method}.json", result)
    set_mean_correction(model, base_mean, np.zeros(16))
    deadline.check()
    if state_digest(model, exclude=(MEAN_KEY,)) != immutable_state_hash:
        raise AssertionError("A non-mean model state changed")
    if state_digest(model) != baseline_state_hash:
        raise AssertionError("Original model state was not restored exactly")
    deadline.check()
    write_json_new(output / "summary.json", {
        "mechanism_diagnostic_only": True, "confirmatory": False,
        "all_non_mean_state_unchanged": True, "base_state_restored": True,
        "primary_change_measure": "mean_probability_tv_from_frozen",
        "accuracy_and_brier_are_exploratory": True, "rank": rank, "metrics": metrics,
        "no_model_api": True, "significance_or_ci_computed": False,
    })


def parser():
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("--checkpoint", type=Path, required=True)
    result.add_argument("--checkpoint-sha256", required=True,
                        help="Hash fixed by the operator after verifying the official acquisition source")
    result.add_argument("--cifar-root", type=Path, required=True)
    result.add_argument("--trust-verified-official-cifar", action="store_true",
                        help="Explicit acknowledgement that official pickle data provenance was verified")
    result.add_argument("--output", type=Path, required=True, help="New directory; must not already exist")
    result.add_argument("--device", default="cuda:0")
    result.add_argument("--max-seconds", type=float, default=1800.0)
    result.add_argument("--batch-size", type=int, default=8)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    # Failure to create output must never alter an existing run.
    output = create_output(args.output)
    deadline, cost = Deadline(args.max_seconds), {}
    set_status(output, "pending", mechanism_diagnostic_only=True, no_model_api=True)
    error = None
    try:
        execute(args, output, deadline, cost)
    except BaseException as exc:
        error = exc
        write_json_new(output / "failure.json", {"type": type(exc).__name__,
                       "message": str(exc), "traceback": traceback.format_exc(),
                       "partial_outputs_retained": True, "automatic_retry": False})
    memory = {"max_memory_allocated_bytes": None, "max_memory_reserved_bytes": None}
    if torch.cuda.is_initialized():
        try:
            memory = cuda_memory(torch.device("cuda:0"))
        except Exception:
            pass  # Preserve the original failure if the CUDA context itself failed.
    write_json_new(output / "cost.json", {
        "no_model_api": True, "wall_seconds": deadline.elapsed, "phases": cost,
        "forward_examples_completed": sum(p["forward_examples"] for p in cost.values()),
        "forward_examples_attempted": sum(p["forward_examples_attempted"] for p in cost.values()),
        "cuda_forward_timing_scope": "CUDA events around model forward including feature hook; excludes transfers/writes",
        "memory_scope": "torch allocator, not all process or device memory", **memory,
    })
    set_status(output, "failed" if error is not None else "completed",
               wall_seconds=deadline.elapsed, mechanism_diagnostic_only=True,
               no_model_api=True, error_type=type(error).__name__ if error else None)
    if error is not None:
        print(f"Failed; partial evidence retained in {output}: {error}", file=sys.stderr)
        return 1
    print(f"Development mechanism diagnostic completed: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
