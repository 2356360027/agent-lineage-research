"""Private upload entry point; validates/extracts into a NEW sibling directory."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import runpy
import stat
import sys
import zipfile

PREFIX = "contract-pilot-74024cf-20261013"


def main():
    source = Path(sys.argv[0]).absolute()
    destination = source.parent / PREFIX
    for part in (destination, *destination.parents):
        if part.is_symlink():
            raise ValueError("Symlink extraction path rejected")
    if destination.exists():
        raise FileExistsError("Extracted directory already exists. Preserve it; do not blindly rerun.")
    with source.open("rb") as handle:
        archive_sha = hashlib.file_digest(handle, "sha256").hexdigest()
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if len(members) > 32 or len({m.filename for m in members}) != len(members):
            raise ValueError("Unexpected or duplicate members")
        content = {}
        for member in members:
            if member.file_size > 4 * 1024 * 1024:
                raise ValueError("Oversized bundle member")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Symlink bundle member")
            if member.filename == "__main__.py":
                continue
            path = PurePosixPath(member.filename)
            if (path.parts[0] != PREFIX or path.is_absolute() or ".." in path.parts
                    or "\\" in member.filename or ":" in member.filename or member.is_dir()):
                raise ValueError("Unsafe bundle member")
            relative = str(path.relative_to(PREFIX))
            content[relative] = archive.read(member)
    manifest = json.loads(content["DEPLOY_MANIFEST.json"])
    if set(content) != set(manifest["files"]) | {"DEPLOY_MANIFEST.json"}:
        raise ValueError("Missing or unexpected source files")
    for name, info in manifest["files"].items():
        raw = content[name]
        if len(raw) != info["bytes"] or hashlib.sha256(raw).hexdigest() != info["sha256"]:
            raise ValueError(f"Corrupt bundle member: {name}")
    destination.mkdir(exist_ok=False)
    for name, raw in content.items():
        output = destination / name
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(raw)
    with (destination / "UPLOAD_RECEIPT.json").open("x", encoding="utf-8") as handle:
        json.dump({"archive_sha256": archive_sha, "archive_bytes": source.stat().st_size}, handle)
    print(f"EXTRACTED: {destination}\nUPLOAD_SHA256: {archive_sha}", flush=True)
    os.chdir(destination)
    sys.path.insert(0, str(destination))
    sys.argv[0] = str(destination / "scripts/run_contract_pilot_server.py")
    runpy.run_module("scripts.run_contract_pilot_server", run_name="__main__")


if __name__ == "__main__":
    main()
