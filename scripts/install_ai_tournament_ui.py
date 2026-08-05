#!/usr/bin/env python3
"""Install AI Tournament renderers and company profiles on PL and EN pages."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_VERSION = "5"
PROFILE_VERSION = "1"
SCRIPT = f'<script src="/scripts/ai-tournament-public.js?v={SCRIPT_VERSION}" defer></script>'
READINESS_SCRIPT = f'<script src="/scripts/ai-tournament-readiness.js?v={SCRIPT_VERSION}" defer></script>'
PROFILE_SCRIPT = f'<script src="/scripts/ai-tournament-company-profiles.js?v={PROFILE_VERSION}" defer></script>'
PATTERN = re.compile(r'<script\s+src=["\']/scripts/ai-tournament-public\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
READINESS_PATTERN = re.compile(r'<script\s+src=["\']/scripts/ai-tournament-readiness\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
PROFILE_PATTERN = re.compile(r'<script\s+src=["\']/scripts/ai-tournament-company-profiles\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
PAGES = (
    ROOT / "pl" / "inwestycje" / "portfel-10k.html",
    ROOT / "en" / "investing" / "portfolio-10k.html",
)


def patch_text(source: str) -> str:
    source = PATTERN.sub("", source)
    source = READINESS_PATTERN.sub("", source)
    source = PROFILE_PATTERN.sub("", source)
    if "</body>" not in source:
        raise RuntimeError("portfolio page has no closing body tag")
    scripts = SCRIPT + READINESS_SCRIPT + PROFILE_SCRIPT
    return source.replace("</body>", scripts + "</body>", 1)


def main() -> None:
    for path in PAGES:
        old = path.read_text(encoding="utf-8")
        new = patch_text(old)
        if new != old:
            path.write_text(new, encoding="utf-8", newline="\n")
            print(f"updated {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
