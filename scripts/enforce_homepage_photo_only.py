#!/usr/bin/env python3
"""Keep only source-linked photographic cards on BriefRooms homepages."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- HOME_BRIEFS_START -->"
END = "<!-- HOME_BRIEFS_END -->"
SCRIPT_VERSION = "ai-outlook-analysis-1"
GUARD_VERSION = "governance-v3"
FRESHNESS_VERSION = "daily-v1"
SCRIPT = f'<script src="/scripts/homepage-photo-only.js?v={SCRIPT_VERSION}" defer></script>'
GUARD_SCRIPT = f'<script src="/scripts/ai-outlook-governance-guard.js?v={GUARD_VERSION}" defer></script>'
FRESHNESS_SCRIPT = f'<script src="/scripts/ai-outlook-freshness-guard.js?v={FRESHNESS_VERSION}" defer></script>'
SCRIPT_RE = re.compile(
    r'<script\s+src=["\']/scripts/homepage-photo-only\.js(?:\?[^"\']*)?["\']\s+defer></script>',
    re.I,
)
GUARD_RE = re.compile(
    r'<script\s+src=["\']/scripts/ai-outlook-governance-guard\.js(?:\?[^"\']*)?["\']\s+defer></script>',
    re.I,
)
FRESHNESS_RE = re.compile(
    r'<script\s+src=["\']/scripts/ai-outlook-freshness-guard\.js(?:\?[^"\']*)?["\']\s+defer></script>',
    re.I,
)
CARD_RE = re.compile(
    r'<a\b(?=[^>]*\bclass=["\'][^"\']*\bbrief-card\b[^"\']*["\'])[^>]*>.*?</a>',
    re.I | re.S,
)
ALLOWED_HOMEPAGE_COUNTS = {8, 10}


def photo_card(card: str) -> bool:
    return bool(
        re.search(r'class=["\'][^"\']*\bthumb\b[^"\']*\bhas-image\b[^"\']*["\']', card, re.I)
        and re.search(r"<img\b[^>]+data-br-external-media=[\"']source-linked[\"']", card, re.I)
        and "media-fallback-active" not in card
    )


def filter_marker_block(source: str, label: str) -> str:
    marker = re.compile(rf"({re.escape(START)})(.*?)({re.escape(END)})", re.S)
    match = marker.search(source)
    if not match:
        raise RuntimeError(f"Homepage markers missing: {label}")
    cards = CARD_RE.findall(match.group(2))
    kept = [card for card in cards if photo_card(card)]
    if len(cards) not in ALLOWED_HOMEPAGE_COUNTS:
        raise RuntimeError(f"{label} must start with exactly 8 or 10 homepage cards; got {len(cards)}")
    if len(kept) != len(cards):
        raise RuntimeError(
            f"{label} has {len(cards) - len(kept)} card(s) without a source-linked photo; "
            "refusing to create an invalid 7- or 9-card publication"
        )
    block = "\n" + "\n".join(kept) + "\n"
    return source[: match.start()] + match.group(1) + block + match.group(3) + source[match.end() :]


def ensure_runtime(source: str) -> str:
    source = re.sub(
        r'(<div\s+id=["\']latest-briefs["\']\s+class=["\']brief-grid["\'])'
        r'(?:\s+data-home-photo-only=["\'][^"\']*["\'])?',
        r'\1 data-home-photo-only="true"',
        source,
        count=1,
        flags=re.I,
    )
    if "</body>" not in source:
        raise RuntimeError("Homepage closing body tag missing")
    source = SCRIPT_RE.sub("", source)
    source = GUARD_RE.sub("", source)
    source = FRESHNESS_RE.sub("", source)
    runtime = FRESHNESS_SCRIPT + "\n" + GUARD_SCRIPT + "\n" + SCRIPT
    source = source.replace("</body>", runtime + "\n</body>", 1)
    return source


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def process(path: Path) -> bool:
    old = path.read_text(encoding="utf-8")
    label = display_path(path)
    new = ensure_runtime(filter_marker_block(old, label))
    if new == old:
        return False
    path.write_text(new, encoding="utf-8", newline="\n")
    print(f"updated {label}")
    return True


def main() -> None:
    process(ROOT / "pl" / "index.html")
    process(ROOT / "en" / "index.html")


if __name__ == "__main__":
    main()
