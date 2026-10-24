"""Fetch verified Maven to task-local cache; no global install or settings edit."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import urllib.request
import zipfile

VERSION = "3.9.9"
SHA512 = "8beac8d11ef208f1e2a8df0682b9448a9a363d2ad13ca74af43705549e72e74c9378823bf689287801cbbfc2f6ea9596201d19ccacfdfb682ee8a2ff4c4418ba"
URL = f"https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/{VERSION}/apache-maven-{VERSION}-bin.zip"


def fetch(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    with urllib.request.urlopen(URL, timeout=30) as response:
        raw = response.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024 or hashlib.sha512(raw).hexdigest() != SHA512:
        raise ValueError("Pinned Maven artifact mismatch")
    archive = output / f"apache-maven-{VERSION}-bin.zip"
    with archive.open("xb") as f:
        f.write(raw)
    extracted = {}
    with zipfile.ZipFile(archive) as z:
        if sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024:
            raise ValueError("Archive expansion bound exceeded")
        names = set()
        for i in z.infolist():
            p = PurePosixPath(i.filename)
            if p.is_absolute() or ".." in p.parts or ":" in i.filename or "\\" in i.filename:
                raise ValueError("Unsafe archive name")
            if stat.S_ISLNK(i.external_attr >> 16) or i.filename.casefold() in names:
                raise ValueError("Link or duplicate archive entry")
            names.add(i.filename.casefold())
            dest = (output / Path(*p.parts)).resolve()
            if not dest.is_relative_to(output):
                raise ValueError("Archive path escaped cache")
            if i.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                content = z.read(i)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("xb") as f:
                    f.write(content)
                extracted[i.filename] = hashlib.sha256(content).hexdigest()
    manifest = {"url": URL, "version": VERSION, "sha512": SHA512,
                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "files": extracted, "installation": "workspace cache only; global environment unchanged"}
    with (output / "manifest.json").open("x", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(json.dumps({k: v for k, v in manifest.items() if k != "files"}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=Path)
    fetch(p.parse_args().output)
