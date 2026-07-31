#!/usr/bin/env python3
"""Attach the Architecture 3 public renderer to the PL and EN Lab pages."""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = '<script src="/scripts/brace-spx-architecture3-public.js?v=20260801-1" defer></script>'
PAGES = (
    ROOT / "pl" / "inwestycje" / "brace-spx-lab.html",
    ROOT / "en" / "investing" / "brace-spx-lab.html",
)


def install(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if SCRIPT in source:
        return False
    if "data-brace-lab-root" not in source or "</body>" not in source:
        raise RuntimeError(f"Unexpected BRACE-SPX Lab structure: {path}")
    path.write_text(source.replace("</body>", SCRIPT + "\n</body>", 1), encoding="utf-8")
    return True


def validate() -> None:
    for path in PAGES:
        source = path.read_text(encoding="utf-8")
        if source.count(SCRIPT) != 1:
            raise RuntimeError(f"Expected one Architecture 3 renderer in {path}")
        if "data-brace-lab-root" not in source:
            raise RuntimeError(f"Missing BRACE-SPX Lab root in {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check:
        changed = [str(path.relative_to(ROOT)) for path in PAGES if install(path)]
        print("Architecture 3 panel:", ", ".join(changed) if changed else "already current")
    validate()


if __name__ == "__main__":
    main()
