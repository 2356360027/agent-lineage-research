"""Fetch one pinned pure-Python upstream wheel; no installation or source execution."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

VERSION = "30.22.0"
FILENAME = f"sqlglot-{VERSION}-py3-none-any.whl"
SHA256 = "90aa461490fcd95d14ec3842a97506ae20f6d3e9313307ad31be793d479cca65"
SIZE = 777816


def fetch(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    metadata_url = f"https://pypi.org/pypi/sqlglot/{VERSION}/json"
    with urllib.request.urlopen(metadata_url, timeout=30) as response:
        metadata = response.read(2*1024*1024+1)
    if len(metadata) > 2*1024*1024:
        raise ValueError("Metadata too large")
    (destination/"pypi_metadata.json").write_bytes(metadata)
    obj = json.loads(metadata)
    entry, = [x for x in obj["urls"] if x["filename"] == FILENAME]
    if entry["digests"]["sha256"] != SHA256 or entry["size"] != SIZE:
        raise ValueError("Pinned artifact changed")
    with urllib.request.urlopen(entry["url"], timeout=30) as response:
        raw = response.read(SIZE+1)
    if len(raw) != SIZE or hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError("Wheel size/hash mismatch")
    wheel = destination/FILENAME
    wheel.write_bytes(raw)
    with zipfile.ZipFile(wheel) as z:
        suspicious = [n for n in z.namelist() if n.endswith((".pth", ".pyd", ".so", ".exe"))]
        if suspicious:
            raise ValueError(f"Unexpected executable/native payloads: {suspicious}")
        licenses = [n for n in z.namelist() if n.endswith("/LICENSE")]
        if len(licenses) != 1:
            raise ValueError("Missing/ambiguous upstream license")
        (destination/"UPSTREAM_LICENSE.txt").write_bytes(z.read(licenses[0]))
    record = {"package":"sqlglot", "version":VERSION, "filename":FILENAME,
              "sha256":SHA256, "bytes":SIZE, "metadata_url":metadata_url,
              "artifact_url":entry["url"], "source_project":"https://github.com/tobymao/sqlglot",
              "metadata_sha256":hashlib.sha256(metadata).hexdigest(),
              "installation":"none; load verified pure-Python wheel through task-local sys.path",
              "license_file":"UPSTREAM_LICENSE.txt"}
    (destination/"manifest.json").write_text(json.dumps(record,indent=2)+"\n",encoding="utf-8")
    return record


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(fetch(a.output),indent=2))
