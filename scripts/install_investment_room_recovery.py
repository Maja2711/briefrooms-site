#!/usr/bin/env python3
"""Install the investment-room recovery and consistent room order on PL/EN pages."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PL_PAGE = ROOT / "pl" / "inwestycje" / "portfel-10k.html"
EN_PAGE = ROOT / "en" / "investing" / "portfolio-10k.html"

ASSETS = {
    "tournament": '<script src="/scripts/ai-tournament-public.js?v=5" defer></script>',
    "readiness": '<script src="/scripts/ai-tournament-readiness.js?v=5" defer></script>',
    "profiles": '<script src="/scripts/ai-tournament-company-profiles.js?v=1" defer></script>',
    "summary": '<script src="/scripts/ai-tournament-summary.js?v=1" defer></script>',
    "nav_order": '<script src="/scripts/investment-room-nav-order.js?v=1" defer></script>',
    "en_recovery": '<script src="/scripts/portfolio-10k-en-recovery.js?v=1" defer></script>',
}

SCRIPT_PATTERNS = [
    re.compile(r'<script\s+src=["\']/scripts/ai-tournament-public\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I),
    re.compile(r'<script\s+src=["\']/scripts/ai-tournament-readiness\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I),
    re.compile(r'<script\s+src=["\']/scripts/ai-tournament-company-profiles\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I),
    re.compile(r'<script\s+src=["\']/scripts/ai-tournament-summary\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I),
    re.compile(r'<script\s+src=["\']/scripts/investment-room-nav-order\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I),
    re.compile(r'<script\s+src=["\']/scripts/portfolio-10k-en-recovery\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I),
]


def strip_assets(source: str) -> str:
    for pattern in SCRIPT_PATTERNS:
        source = pattern.sub("", source)
    return source


def patch_page(source: str, *, language: str) -> str:
    source = strip_assets(source)
    if language == "en":
        source = re.sub(
            r'/scripts/portfolio-10k-dashboard-en\.js\?v=[^"\']+',
            '/scripts/portfolio-10k-dashboard-en.js?v=6',
            source,
            count=1,
        )
    if "</body>" not in source:
        raise RuntimeError(f"{language} investment page has no closing body tag")

    scripts = [
        ASSETS["tournament"],
        ASSETS["readiness"],
        ASSETS["profiles"],
        ASSETS["summary"],
        ASSETS["nav_order"],
    ]
    if language == "en":
        scripts.append(ASSETS["en_recovery"])
    return source.replace("</body>", "".join(scripts) + "</body>", 1)


def process(path: Path, language: str) -> bool:
    old = path.read_text(encoding="utf-8")
    new = patch_page(old, language=language)
    if new == old:
        return False
    path.write_text(new, encoding="utf-8", newline="\n")
    print(f"updated {path.relative_to(ROOT)}")
    return True


def main() -> None:
    process(PL_PAGE, "pl")
    process(EN_PAGE, "en")


if __name__ == "__main__":
    main()
