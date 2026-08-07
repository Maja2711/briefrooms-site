#!/usr/bin/env python3
"""Install AI Tournament renderers and keep Investment Room navigation current."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_VERSION = "5"
PROFILE_VERSION = "1"
SUMMARY_VERSION = "1"
ROOM_CONTROLLER_VERSION = "7"
NAV_GUARD_VERSION = "2"
SCRIPT = f'<script src="/scripts/ai-tournament-public.js?v={SCRIPT_VERSION}" defer></script>'
READINESS_SCRIPT = f'<script src="/scripts/ai-tournament-readiness.js?v={SCRIPT_VERSION}" defer></script>'
PROFILE_SCRIPT = f'<script src="/scripts/ai-tournament-company-profiles.js?v={PROFILE_VERSION}" defer></script>'
SUMMARY_SCRIPT = f'<script src="/scripts/ai-tournament-summary.js?v={SUMMARY_VERSION}" defer></script>'
NAV_GUARD_SCRIPT = f'<script src="/scripts/portfolio-10k-navigation-guard.js?v={NAV_GUARD_VERSION}" defer></script>'
PATTERN = re.compile(r'<script\s+src=["\']/scripts/ai-tournament-public\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
READINESS_PATTERN = re.compile(r'<script\s+src=["\']/scripts/ai-tournament-readiness\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
PROFILE_PATTERN = re.compile(r'<script\s+src=["\']/scripts/ai-tournament-company-profiles\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
SUMMARY_PATTERN = re.compile(r'<script\s+src=["\']/scripts/ai-tournament-summary\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
NAV_GUARD_PATTERN = re.compile(r'<script\s+src=["\']/scripts/portfolio-10k-navigation-guard\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
ROOM_CONTROLLER_PATTERN = re.compile(
    r'<script\s+src=["\'](?P<path>/scripts/portfolio-10k-dashboard(?:-en)?\.js)(?:\?[^"\']*)?["\']\s+defer></script>',
    re.I,
)
PAGES = (
    ROOT / "pl" / "inwestycje" / "portfel-10k.html",
    ROOT / "en" / "investing" / "portfolio-10k.html",
)


def patch_text(source: str) -> str:
    # Keep a dedicated cache key for the stable room controller. Bumping this
    # version forces already-cached browsers/CDN edges to request the current
    # controller after a room recovery without changing the controller logic.
    source = ROOM_CONTROLLER_PATTERN.sub(
        lambda match: f'<script src="{match.group("path")}?v={ROOM_CONTROLLER_VERSION}" defer></script>',
        source,
    )
    source = PATTERN.sub("", source)
    source = READINESS_PATTERN.sub("", source)
    source = PROFILE_PATTERN.sub("", source)
    source = SUMMARY_PATTERN.sub("", source)
    source = NAV_GUARD_PATTERN.sub("", source)
    if "</body>" not in source:
        raise RuntimeError("portfolio page has no closing body tag")
    scripts = SCRIPT + READINESS_SCRIPT + PROFILE_SCRIPT + SUMMARY_SCRIPT + NAV_GUARD_SCRIPT
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
