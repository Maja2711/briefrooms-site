#!/usr/bin/env python3
"""Read-only, point-in-time file integrity attestations.

A locally generated manifest is NOT an independent anchor. Export its bytes to
an independently permissioned immutable object store and provide that exact
manifest back to 'verify'. Historical files are never rewritten by this tool.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "briefrooms-history-integrity-guard-v1"

def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

def digest(data):
    return hashlib.sha256(data).hexdigest()

def checked(root, name):
    if not name or name.startswith("/") or "\\" in name or any(p in ("", ".", "..") for p in name.split("/")):
        raise ValueError("unsafe path: " + repr(name))
    path = root / name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("missing, symlink or outside root: " + name)
    return path

def snapshot(root, names, timestamp=None):
    if not names or len(names) != len(set(names)):
        raise ValueError("file list must be nonempty and unique")
    entries = []
    for name in sorted(names):
        data = checked(root, name).read_bytes()
        entries.append({"path": name, "bytes": len(data), "sha256": digest(data)})
    payload = {"schema_version": SCHEMA, "created_at": timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00","Z"), "files": entries}
    payload["manifest_sha256"] = digest(canonical(payload))
    return payload

def verify(root, manifest):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA:
        raise ValueError("invalid schema")
    seal = manifest.get("manifest_sha256")
    body = {k:v for k,v in manifest.items() if k != "manifest_sha256"}
    if set(body) != {"schema_version", "created_at", "files"} or not isinstance(seal,str) or seal != digest(canonical(body)):
        raise ValueError("manifest checksum mismatch")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("empty manifest")
    names = [e.get("path") for e in entries if isinstance(e, dict)]
    if len(names) != len(entries) or names != sorted(set(names)):
        raise ValueError("invalid file entries")
    for entry in entries:
        if set(entry) != {"path", "bytes", "sha256"}:
            raise ValueError("invalid entry")
        data = checked(root, entry["path"]).read_bytes()
        if len(data) != entry["bytes"] or digest(data) != entry["sha256"]:
            raise ValueError("INTEGRITY_MISMATCH: " + entry["path"])
    return len(entries)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    seal = sub.add_parser("snapshot")
    seal.add_argument("--root", type=Path, default=Path("."))
    seal.add_argument("--files-from", required=True, type=Path, help="newline-delimited relative paths")
    seal.add_argument("--output", required=True, type=Path)
    check = sub.add_parser("verify")
    check.add_argument("--root", type=Path, default=Path("."))
    check.add_argument("--manifest", required=True, type=Path, help="independently retrieved trusted manifest")
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            names = [line.strip() for line in args.files_from.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
            result = snapshot(args.root, names)
            if args.output.exists():
                raise ValueError("refusing to overwrite an existing manifest")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as out:
                json.dump(result, out, indent=2, ensure_ascii=False, sort_keys=True)
                out.write("\n")
            print("SNAPSHOT_UNANCHORED", result["manifest_sha256"])
        else:
            n = verify(args.root, json.loads(args.manifest.read_text(encoding="utf-8")))
            print("BYTES_MATCH_ANCHOR_UNVERIFIED", n, "files; external authenticity and WORM retention are NOT verified")
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print("INTEGRITY_FAIL:", error, file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
