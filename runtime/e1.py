"""Standard-library E1 pilot using a loopback vLLM chat endpoint.

Synthetic task worlds, real inference when --execute is supplied. No mocked
predictions or imputed failures. Seven calls per item/repetition; no retries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def world(seed, index):
    rng = random.Random(f"world-v1:{seed}:{index}")
    label = rng.randrange(2)
    observations = []
    for agent in range(3):
        reliability = rng.choice([0.6, 0.75, 0.9])
        value = label if rng.random() < reliability else 1 - label
        observations.append({"id": f"O{agent}", "source": f"R{agent}",
                             "value": value, "reliability": reliability})
    return observations, label


SYSTEM = (
    "Infer a hidden binary state Y with prior P(Y=1)=0.5. Each source emits "
    "one binary observation equal to Y with its stated reliability, symmetrically "
    "for Y=0 and Y=1. Distinct sources are conditionally independent given Y. "
    "Repeated observation IDs are the same observation, not new evidence. "
    "Peer assessments are generated from their observations and may be wrong. "
    "Return only a JSON object with answer (integer 0 or 1), p_state_1 (number "
    "between 0 and 1), and citations (list of visible observation IDs)."
)


def messages(observations, previous=None, verdicts=None):
    # Explicit allow-list. Item IDs, arm names, labels and gold never enter prompts.
    payload = {"observations": observations}
    if previous is not None:
        payload["your_previous_assessment"] = previous
        payload["peer_assessments"] = verdicts or []
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": canonical(payload)}]


def parse(raw, visible):
    choice = raw["choices"][0]
    if choice.get("finish_reason") != "stop":
        raise ValueError("Completion truncated or non-stop finish reason")
    obj = json.loads(choice["message"]["content"])
    if set(obj) != {"answer", "p_state_1", "citations"}:
        raise ValueError("Unexpected output schema")
    p = obj["p_state_1"]
    if type(obj["answer"]) is not int or obj["answer"] not in (0, 1):
        raise ValueError("Invalid answer")
    if type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("Invalid probability")
    if not isinstance(obj["citations"], list) or any(
        not isinstance(x, str) or x not in visible for x in obj["citations"]
    ):
        raise ValueError("Invalid citations")
    return obj


class Client:
    def __init__(self, config, endpoint, directory):
        url = urlsplit(endpoint)
        if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Only loopback HTTP endpoints allowed; use an SSH tunnel")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("No credentials/query allowed in endpoint")
        self.config, self.endpoint, self.directory = config, endpoint, directory

    def call(self, key, prompt, seed, visible):
        payload = {"model": self.config["model"], "messages": prompt,
                   "seed": seed, "temperature": self.config["temperature"],
                   "top_p": self.config["top_p"], "max_tokens": self.config["max_tokens"],
                   "response_format": {"type": "json_object"}}
        path = self.directory / (key + ".json")
        request_hash = digest(payload)
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["request_hash"] != request_hash:
                raise ValueError("Cache/request mismatch")
            if record["status"] != "ok":
                raise RuntimeError(f"Unresolved failed call {path}; no silent retry")
            return parse(record["raw_response"], visible)
        headers = {"Content-Type": "application/json"}
        if os.getenv("MODEL_API_KEY"):
            headers["Authorization"] = "Bearer " + os.environ["MODEL_API_KEY"]
        record = {"request_hash": request_hash, "request": payload,
                  "status": "pending", "data_status": "MODEL_ENDPOINT_PILOT",
                  "usage": None, "started_unix": time.time()}
        # Persist before dispatch. Interrupted requests cannot be silently repeated.
        save(path, record)
        started = time.perf_counter()
        try:
            req = urllib.request.Request(self.endpoint.rstrip("/") + "/chat/completions",
                                         canonical(payload).encode(), headers)
            with urllib.request.urlopen(req, timeout=180) as response:
                text = response.read().decode()
            record["raw_text"] = text
            raw = json.loads(text)
            record["raw_response"] = raw
            record["usage"] = raw.get("usage")
            result = parse(raw, visible)
            record["status"] = "ok"
            return result
        except Exception as exc:
            record["status"] = "failed"
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["wall_seconds"] = time.perf_counter() - started
            save(path, record)


def run_item(client, config, index, rep):
    obs, _ = world(config["seed"], index)
    base = int(digest([config["seed"], index, rep])[:7], 16)
    initial = [client.call(f"i{index}-r{rep}-s0-{a}", messages([o]), base + a,
                           {o["id"]}) for a, o in enumerate(obs)]
    focal = index % 3
    peers = [a for a in range(3) if a != focal]
    arms = [(e, c) for e in (0, 1) for c in (0, 1)]
    random.Random(base).shuffle(arms)
    results = {}
    for e, c in arms:
        evidence = [obs[focal]] + ([obs[a] for a in peers] if e else [])
        verdicts = [{"agent": f"peer{j}", "answer": initial[a]["answer"]}
                    for j, a in enumerate(peers)] if c else []
        # Same frozen S0 and sampling seed across paired treatments.
        prompt = messages(evidence, initial[focal], verdicts)
        assessment = client.call(f"i{index}-r{rep}-E{e}C{c}", prompt, base + 3,
                                 {o["id"] for o in evidence})
        results[f"E{e}C{c}"] = {"assessment": assessment,
                                 "evidence_hash": digest(evidence)}
    assert results["E1C0"]["evidence_hash"] == results["E1C1"]["evidence_hash"]
    return {"item": index, "repetition": rep, "focal": focal,
            "initial": initial[focal], "arms": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/e1_pilot.json")
    parser.add_argument("--output", default="runtime/runs/e1-pilot")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if not (0 <= args.shard < args.shards) or config["items"] < 1 or config["repetitions"] < 1:
        parser.error("Invalid item/repetition/shard count")
    indices = list(range(args.shard, config["items"], args.shards))
    print(json.dumps({"mode": "execute" if args.execute else "plan_no_inference",
                      "items_on_worker": len(indices),
                      "maximum_calls_no_retries": 7 * len(indices) * config["repetitions"],
                      "config_hash": digest(config)}, indent=2))
    if not args.execute:
        return
    if config.get('deployment') == 'native':
        from .native import verify
        verify(config)
    else:
        if not re.fullmatch(r"[0-9a-f]{40}", config["revision"]):
            parser.error("Pin an exact model commit before execution")
        if "@sha256:" not in config["server_image"]:
            parser.error("Pin the vLLM Docker image digest before execution")
    directory = Path(args.output) / f"shard-{args.shard:03d}"
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".running.lock"
    with lock.open("x", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    try:
        code_hash = hashlib.sha256(Path(__file__).read_text(encoding="utf-8").encode()).hexdigest()
        try:
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            commit = None
        manifest = {"config": config, "shards": args.shards, "shard": args.shard,
                    "code_hash": code_hash, "git_commit": commit, "python": sys.version,
                    "task_generator": "binary-independent-sources-v1"}
        path = directory / "manifest.json"
        if path.exists() and json.loads(path.read_text()) != manifest:
            raise ValueError("Run manifest changed; use a new output directory")
        save(path, manifest)
        client = Client(config, args.endpoint, directory)
        for index in indices:
            for rep in range(config["repetitions"]):
                result = run_item(client, config, index, rep)
                save(directory / f"result-{index}-{rep}.json", result)
                print(f"Completed item={index} repetition={rep}", flush=True)
        print("Completed worker. Score separately with python -m runtime.score.")
    finally:
        lock.unlink()


if __name__ == "__main__":
    main()
