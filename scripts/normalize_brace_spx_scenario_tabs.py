#!/usr/bin/env python3
"""Keep the BRACE-SPX Lab tab green on both PL and EN scenario pages."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "pl" / "inwestycje" / "spx-scenariusze-2026.html",
    ROOT / "en" / "investing" / "spx-scenarios-2026.html",
)
GREEN_STYLE = (
    "display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;"
    "border:1px solid #166534!important;border-radius:999px;"
    "background:#15803d!important;color:#fff!important;font-weight:800;"
    "text-decoration:none;box-shadow:0 7px 18px rgba(21,128,61,.24)!important"
)
PATTERN = re.compile(
    r'(<a\b[^>]*href="/(?:pl/inwestycje|en/investing)/brace-spx-lab\.html"[^>]*?)style="[^"]*"([^>]*>BRACE-SPX Lab</a>)',
    re.IGNORECASE,
)


def normalize(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    updated, count = PATTERN.subn(rf'\1style="{GREEN_STYLE}"\2', source, count=1)
    if count != 1:
        raise RuntimeError(f"Expected exactly one BRACE-SPX Lab scenario tab in {path}")
    if updated == source:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def validate(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    required = (
        'background:#15803d!important',
        'color:#fff!important',
        'border:1px solid #166534!important',
    )
    if 'href="/' not in source or any(item not in source for item in required):
        raise RuntimeError(f"Enforced green BRACE-SPX Lab tab missing in {path}")


def main() -> None:
    changed = [str(path.relative_to(ROOT)) for path in TARGETS if normalize(path)]
    for path in TARGETS:
        validate(path)
    print("BRACE-SPX green tabs:", ", ".join(changed) if changed else "already current")


if __name__ == "__main__":
    main()
