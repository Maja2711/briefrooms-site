#!/usr/bin/env python3
from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHUNK_DIR = ROOT / "data" / "weekly-v5-patch"

payload = "".join(
    path.read_text(encoding="utf-8").strip()
    for path in sorted(CHUNK_DIR.glob("chunk-*.txt"))
)
files = json.loads(gzip.decompress(base64.b64decode(payload)).decode("utf-8"))

for rel, content in files.items():
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {rel}")

for path in sorted(CHUNK_DIR.glob("chunk-*.txt")):
    path.unlink()
if CHUNK_DIR.exists():
    CHUNK_DIR.rmdir()

for rel in (
    "scripts/apply_weekly_v5_patch.py",
    ".github/workflows/apply-weekly-v5-patch.yml",
):
    path = ROOT / rel
    if path.exists():
        path.unlink()
        print(f"removed {rel}")
