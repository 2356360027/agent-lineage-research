"""Exclusive local JSON journal for the staged loop; no execution authority.

Single-owner directory. File flush/fsync is requested; this is not a guarantee
against every filesystem/power-loss failure. A verified seal is required.
"""
import json
import os
from pathlib import Path
import re

from runtime.bird_readonly_audit import encoded, hash_file
from scripts.fetch_bird_fixed import absent, no_link_ancestors


class Journal:
    def __init__(self, directory):
        self.directory = Path(directory)
        absent(self.directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.closed = False

    def __call__(self, name, value):
        if self.closed:
            raise RuntimeError("Journal closed or invalidated")
        if type(name) is not str or re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", name) is None:
            raise ValueError("Unsafe journal record name")
        if name == "artifact_hashes":
            raise ValueError("Reserved name")
        self._write(name + ".json", value)

    def _write(self, name, value):
        try:
            no_link_ancestors(self.directory)
            data = encoded(value)
            with (self.directory / name).open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            self.closed = True
            raise

    def seal(self):
        if self.closed:
            raise RuntimeError("Journal closed or invalidated")
        self.closed = True
        files = list(self.directory.iterdir())
        if any(p.is_symlink() or not p.is_file() for p in files):
            raise ValueError("Unexpected journal entry")
        self._write("artifact_hashes.json", {p.name: hash_file(p) for p in sorted(files)})
        return verify(self.directory)


def verify(directory):
    """Integrity only: not proof of semantic correctness or completed trajectory."""
    root = Path(directory)
    no_link_ancestors(root)
    manifest = json.loads((root / "artifact_hashes.json").read_text("utf-8"))
    files = list(root.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in files):
        raise ValueError("Unexpected journal entry")
    if type(manifest) is not dict or set(manifest) != {p.name for p in files if p.name != "artifact_hashes.json"}:
        raise ValueError("Incomplete journal manifest")
    for name, expected in manifest.items():
        if hash_file(root / name) != expected:
            raise ValueError("Journal hash mismatch")
    return {"status":"INTEGRITY_VERIFIED_ONLY", "files":len(manifest)}
