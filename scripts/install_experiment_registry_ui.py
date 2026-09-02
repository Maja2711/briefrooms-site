#!/usr/bin/env python3
"""Keep Portfolio10K pages pointed at the current navigation guard.

The Lab UI is injected by the shared navigation guard. This installer only bumps
that guard's cache key in both language variants, avoiding large generated HTML
rewrites in feature code while ensuring browsers fetch the Lab-aware version.
"""
from __future__ import annotations

import re
from pathlib import Path

PAGES = (
    Path("pl/inwestycje/portfel-10k.html"),
    Path("en/investing/portfolio-10k.html"),
)
TARGET = "portfolio-10k-navigation-guard.js?v=6"
PATTERN = re.compile(r"portfolio-10k-navigation-guard\.js\?v=\d+")


def update_page(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "portfolio-10k-navigation-guard.js" not in text:
        raise RuntimeError(f"Navigation guard script missing from {path}")
    updated, count = PATTERN.subn(TARGET, text)
    if count != 1:
        raise RuntimeError(f"Expected exactly one navigation guard cache key in {path}, found {count}")
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    changed = []
    for path in PAGES:
        if update_page(path):
            changed.append(str(path))
    print("Experiment Registry UI cache key current:", ", ".join(changed) if changed else "no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
