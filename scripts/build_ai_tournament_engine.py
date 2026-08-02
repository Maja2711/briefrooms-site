#!/usr/bin/env python3
"""Assemble the AI Tournament engine from repository-safe bootstrap parts."""
from __future__ import annotations

import argparse
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTS_DIR = ROOT / "scripts" / "ai_tournament_parts"
OUT = ROOT / "scripts" / "ai_tournament_engine.py"
EXPECTED_PARTS = 7


def build(cleanup: bool = False) -> None:
    parts = sorted(PARTS_DIR.glob("engine.part*")) if PARTS_DIR.exists() else []
    if not parts:
        if OUT.exists():
            py_compile.compile(str(OUT), doraise=True)
            print(f"AI Tournament engine already exists: {OUT.relative_to(ROOT)}")
            return
        raise RuntimeError("AI Tournament engine parts are missing")
    if len(parts) != EXPECTED_PARTS:
        raise RuntimeError(f"Expected {EXPECTED_PARTS} engine parts, found {len(parts)}")
    content = "".join(path.read_text(encoding="utf-8") for path in parts)
    OUT.write_text(content, encoding="utf-8", newline="\n")
    py_compile.compile(str(OUT), doraise=True)
    print(f"built {OUT.relative_to(ROOT)} from {len(parts)} parts")
    if cleanup:
        for path in parts:
            path.unlink()
        try:
            PARTS_DIR.rmdir()
        except OSError:
            pass
        print("removed bootstrap parts")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    build(cleanup=args.cleanup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
