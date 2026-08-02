#!/usr/bin/env python3
"""Install the AI Tournament public renderer on PL and EN portfolio pages."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_VERSION = "1"
SCRIPT = f'<script src="/scripts/ai-tournament-public.js?v={SCRIPT_VERSION}" defer></script>'
PATTERN = re.compile(r'<script\s+src=["\']/scripts/ai-tournament-public\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
PAGES = (
    ROOT / "pl" / "inwestycje" / "portfel-10k.html",
    ROOT / "en" / "investing" / "portfolio-10k.html",
)


def patch_text(source: str) -> str:
    source = PATTERN.sub("", source)
    if "</body>" not in source:
        raise RuntimeError("portfolio page has no closing body tag")
    return source.replace("</body>", SCRIPT + "</body>", 1)


def main() -> None:
    for path in PAGES:
        old = path.read_text(encoding="utf-8")
        new = patch_text(old)
        if new != old:
            path.write_text(new, encoding="utf-8", newline="\n")
            print(f"updated {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
