#!/usr/bin/env python3
"""Archive immutable raw runs without deleting originals; verify archive content hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("directories", nargs="+", type=Path)
    a = p.parse_args()
    for run in a.directories:
        raw = run / "raw"
        if not raw.is_dir():
            p.error(f"missing raw: {raw}")
        target = run / "raw.tar.gz"
        if target.exists():
            p.error(f"refuse to overwrite {target}")
        files = [f for f in sorted(raw.rglob("*")) if f.is_file()]
        manifest = {str(f.relative_to(run)): digest(f) for f in files}
        with tarfile.open(target, "w:gz", compresslevel=6) as archive:
            archive.add(raw, arcname="raw")
        with tarfile.open(target, "r:gz") as archive:
            verified = {}
            for member in archive.getmembers():
                if member.isfile():
                    h = hashlib.sha256()
                    with archive.extractfile(member) as f:
                        for block in iter(lambda: f.read(1024*1024), b""):
                            h.update(block)
                    verified[member.name] = h.hexdigest()
            if manifest != verified:
                raise RuntimeError("archive verification mismatch")
        (run / "archive.json").write_text(json.dumps({
            "archive": target.name, "sha256": digest(target), "files": manifest,
            "bytes": target.stat().st_size, "verified": True
        }, indent=2)+"\n")
        print(target, target.stat().st_size, "bytes", len(files), "files verified")


if __name__ == "__main__":
    main()
