"""CPU software fixtures only: no downloaded models, CIFAR data, or experiments."""

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import numpy as np
    import torch
    from runtime import tta_mean_probe as probe
except ModuleNotFoundError as exc:
    if exc.name not in {"torch", "numpy"}:
        raise
    DEPENDENCY_REASON = f"CPU fixtures skipped: optional dependency missing: {exc.name}"
else:
    DEPENDENCY_REASON = None


@unittest.skipIf(DEPENDENCY_REASON is not None, DEPENDENCY_REASON or "")
class MeanProbeTests(unittest.TestCase):
    def test_class_mixture_is_annihilated(self):
        means = np.zeros((10, 16), dtype=np.float64)
        means[:, :10] = np.eye(10)
        reference, a, q, rank, singular, tolerance = probe.projected_subspace(means)
        weights = np.arange(1, 11, dtype=np.float64)
        weights /= weights.sum()
        delta = weights @ means - reference
        self.assertEqual(rank, 9)
        np.testing.assert_allclose(q @ delta, 0, atol=1e-14)
        np.testing.assert_allclose(q @ a, 0, atol=1e-14)
        np.testing.assert_allclose(q @ q, q, atol=1e-14)
        self.assertGreater(tolerance, 0)
        self.assertEqual(len(singular), 10)

    def test_matched_norm_and_zero_ratio(self):
        vectors, norms = probe.correction_vectors(np.array([3.0, 4.0]), np.diag([1.0, 0.0]))
        self.assertAlmostEqual(np.linalg.norm(vectors["projected"]), 3)
        self.assertAlmostEqual(np.linalg.norm(vectors["matched_norm_full"]), 3)
        np.testing.assert_allclose(vectors["matched_norm_full"], np.array([1.8, 2.4]))
        self.assertAlmostEqual(norms["residual_to_delta_norm_ratio"], 0.6)
        zero, norms = probe.correction_vectors(np.zeros(2), np.eye(2))
        self.assertIsNone(norms["residual_to_delta_norm_ratio"])
        self.assertEqual(norms["residual_norm"], 0)
        np.testing.assert_array_equal(zero["matched_norm_full"], np.zeros(2))

    def test_earliest_bn_and_non_cumulative_frozen_state(self):
        model = probe.freeze_model(probe.WideResNet(depth=10, widen_factor=1))
        bn = model.get_submodule(probe.BN_PATH)
        self.assertEqual(bn.num_features, 16)
        self.assertEqual(model.bn1.num_features, 64)
        base = bn.running_mean.detach().clone()
        immutable = probe.state_digest(model, exclude=(probe.MEAN_KEY,))
        original = probe.state_digest(model)
        probe.set_mean_correction(model, base, np.ones(16))
        probe.set_mean_correction(model, base, np.full(16, 2.0))
        torch.testing.assert_close(bn.running_mean, base + 2)
        self.assertEqual(probe.state_digest(model, exclude=(probe.MEAN_KEY,)), immutable)
        with torch.inference_mode():
            model(torch.zeros(1, 3, 32, 32))
        self.assertEqual(probe.state_digest(model, exclude=(probe.MEAN_KEY,)), immutable)
        self.assertFalse(any(p.requires_grad for p in model.parameters()))
        self.assertFalse(any(m.training for m in model.modules()))
        probe.set_mean_correction(model, base, np.zeros(16))
        self.assertEqual(probe.state_digest(model), original)
        model.train()
        with self.assertRaises(ValueError):
            probe.set_mean_correction(model, base, np.zeros(16))

    def test_original_id_partitions_fixed_and_disjoint(self):
        train = np.repeat(np.arange(10), 120)
        test = np.repeat(np.arange(10), 50)
        selected = probe.select_partitions(train, test)
        self.assertEqual(selected, probe.select_partitions(train, test))
        for split, count in (("source", 1000), ("adaptation", 200), ("evaluation", 200)):
            self.assertEqual(len(selected[split]), count)
            self.assertEqual(len(set(selected[split])), count)
            labels = train if split == "source" else test
            np.testing.assert_array_equal(np.bincount(labels[selected[split]], minlength=10),
                                          np.full(10, count // 10))
        self.assertFalse(set(selected["adaptation"]) & set(selected["evaluation"]))
        self.assertEqual(len(set(selected["adaptation"] + selected["evaluation"])), 400)

    def test_no_overwrite_and_status_history(self):
        with tempfile.TemporaryDirectory() as temp:
            output = probe.create_output(Path(temp) / "run")
            probe.set_status(output, "pending")
            probe.set_status(output, "failed", partial_outputs_retained=True)
            before = (output / "status.json").read_bytes()
            with self.assertRaises(FileExistsError):
                probe.create_output(output)
            self.assertEqual((output / "status.json").read_bytes(), before)
            events = [json.loads(line) for line in (output / "status_events.jsonl").read_text().splitlines()]
            self.assertEqual([e["status"] for e in events], ["pending", "failed"])
            with self.assertRaises(FileExistsError):
                probe.write_json_new(output / "status.json", {})

    def test_checkpoint_prefix_is_only_supported_rewrite(self):
        state, stripped = probe.checkpoint_state({"state_dict": {"module.a": torch.zeros(2)}})
        self.assertTrue(stripped)
        self.assertEqual(list(state), ["a"])
        with self.assertRaises(ValueError):
            probe.checkpoint_state({"module.a": torch.zeros(1), "b": torch.zeros(1)})
        with self.assertRaises(ValueError):
            probe.checkpoint_state({"a": "not a tensor"})

    def test_safe_strict_checkpoint_fixture(self):
        model = probe.WideResNet(depth=10, widen_factor=1)
        other = probe.WideResNet(depth=10, widen_factor=1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.pt"
            torch.save({"state_dict": model.state_dict(), "optimizer": {"unused": 123}}, path)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(probe.torch, "load", wraps=torch.load) as load:
                metadata = probe.load_checkpoint(other, path, sha, probe.Deadline(30))
                self.assertTrue(load.call_args.kwargs["weights_only"])
            self.assertEqual(probe.state_digest(model), probe.state_digest(other))
            self.assertEqual(metadata["ignored_top_level_keys"], ["optimizer"])
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                probe.load_checkpoint(other, path, "0" * 64, probe.Deadline(30))

    def test_failed_run_preserves_status_without_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "failed_run"
            arguments = ["--checkpoint", "unused.pt", "--checkpoint-sha256", "0" * 64,
                         "--cifar-root", "unused_data", "--output", str(output)]
            with patch.object(probe, "execute", side_effect=RuntimeError("fixture only")) as execute:
                with patch.object(probe.torch.cuda, "is_initialized", return_value=False):
                    self.assertEqual(probe.main(arguments), 1)
            execute.assert_called_once()
            self.assertEqual(json.loads((output / "status.json").read_text())["status"], "failed")
            self.assertFalse(json.loads((output / "failure.json").read_text())["automatic_retry"])
            self.assertEqual(json.loads((output / "cost.json").read_text())["forward_examples_completed"], 0)

    def test_pickle_hash_checked_before_deserialization(self):
        with tempfile.TemporaryDirectory() as temp:
            # Invalid tiny fixture, not CIFAR data. It must never reach pickle.
            (Path(temp) / "data_batch_1").write_bytes(b"invalid fixture")
            with patch.object(probe.pickle, "loads") as deserialize:
                with self.assertRaisesRegex(ValueError, "MD5 mismatch"):
                    probe.load_cifar(temp, True, probe.Deadline(10))
                deserialize.assert_not_called()
            with self.assertRaisesRegex(ValueError, "Refusing pickle"):
                probe.load_cifar(temp, False, probe.Deadline(10))

    def test_custom_noise_is_id_stable_and_metrics_finite(self):
        images = np.full((2, 3, 32, 32), 128, dtype=np.uint8)
        joint = probe.transform_images(images, [7, 9], probe.CONDITIONS[3])
        alone = probe.transform_images(images[1:], [9], probe.CONDITIONS[3])
        np.testing.assert_array_equal(joint[1:], alone)
        self.assertTrue(np.all((joint >= 0) & (joint <= 1)))
        logits = np.zeros((2, 10), dtype=np.float32)
        metrics = probe.prediction_metrics(logits, logits, np.array([0, 1]))
        self.assertEqual(metrics["mean_probability_tv_from_frozen"], 0)
        self.assertEqual(metrics["argmax_changed_count"], 0)
        with self.assertRaises(ValueError):
            probe.probabilities(np.full((1, 10), np.nan))

    def test_deadline_and_gpu_peak_limit(self):
        deadline = probe.Deadline(0)
        with self.assertRaises(TimeoutError):
            deadline.check()
        with patch.object(probe, "cuda_memory", return_value={
            "max_memory_allocated_bytes": probe.GPU_MEMORY_LIMIT + 1,
            "max_memory_reserved_bytes": probe.GPU_MEMORY_LIMIT + 1,
        }):
            with self.assertRaises(MemoryError):
                probe.check_gpu_memory(torch.device("cuda:0"))


if __name__ == "__main__":
    unittest.main()
