#!/usr/bin/env python3
"""Protect photo-first BriefRooms homepage cards without restoring legacy homepage runtimes."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- HOME_BRIEFS_START -->"
END = "<!-- HOME_BRIEFS_END -->"
SCRIPT_VERSION = "ai-outlook-direction-2"
GUARD_VERSION = "governance-v3"
FRESHNESS_VERSION = "daily-v1"
SCRIPT = f'<script src="/scripts/homepage-photo-only.js?v={SCRIPT_VERSION}" defer></script>'
GUARD_SCRIPT = f'<script src="/scripts/ai-outlook-governance-guard.js?v={GUARD_VERSION}" defer></script>'
FRESHNESS_SCRIPT = f'<script src="/scripts/ai-outlook-freshness-guard.js?v={FRESHNESS_VERSION}" defer></script>'
SCRIPT_RE = re.compile(r'<script\s+src=["\']/scripts/homepage-photo-only\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
GUARD_RE = re.compile(r'<script\s+src=["\']/scripts/ai-outlook-governance-guard\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
FRESHNESS_RE = re.compile(r'<script\s+src=["\']/scripts/ai-outlook-freshness-guard\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
CARD_RE = re.compile(r'<a\b(?=[^>]*\bclass=["\'][^"\']*\bbrief-card\b[^"\']*["\'])[^>]*>.*?</a>', re.I | re.S)
ALLOWED_HOMEPAGE_COUNTS = {8, 10}


def is_redesigned(source: str) -> bool:
    return bool(re.search(r'<body\b[^>]*\bclass=["\'][^"\']*\bbr-home\b', source, re.I))


def visual_card(card: str) -> bool:
    return bool(
        re.search(r'class=["\'][^"\']*\bthumb\b[^"\']*\bhas-image\b[^"\']*["\']', card, re.I)
        and re.search(r'<img\b[^>]+src=["\']https?://', card, re.I)
        and "media-fallback-active" not in card
    )


def photo_card(card: str) -> bool:
    """Legacy strict card contract retained for older callers/tests."""
    return bool(
        visual_card(card)
        and re.search(r"<img\b[^>]+data-br-external-media=[\"']source-linked[\"']", card, re.I)
    )


def filter_marker_block(source: str, label: str) -> str:
    marker = re.compile(rf"({re.escape(START)})(.*?)({re.escape(END)})", re.S)
    match = marker.search(source)
    if not match:
        raise RuntimeError(f"Homepage markers missing: {label}")
    cards = CARD_RE.findall(match.group(2))

    if is_redesigned(source):
        if not cards:
            raise RuntimeError(f"{label} must contain at least one static photo fallback card")
        invalid = [card for card in cards if not visual_card(card)]
        if invalid:
            raise RuntimeError(f"{label} contains a homepage card without a valid source photo")
        return source

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


def _set_photo_attribute(source: str) -> str:
    pattern = re.compile(r'<div\b(?=[^>]*\bid=["\']latest-briefs["\'])[^>]*>', re.I)
    match = pattern.search(source)
    if not match:
        raise RuntimeError("Homepage latest-briefs container missing")
    opening = match.group(0)
    if re.search(r'\sdata-home-photo-only=["\'][^"\']*["\']', opening, re.I):
        replacement = re.sub(
            r'data-home-photo-only=["\'][^"\']*["\']',
            'data-home-photo-only="true"',
            opening,
            count=1,
            flags=re.I,
        )
    else:
        replacement = opening[:-1].rstrip() + ' data-home-photo-only="true">'
    return source[: match.start()] + replacement + source[match.end() :]


def ensure_runtime(source: str) -> str:
    source = _set_photo_attribute(source)
    source = SCRIPT_RE.sub("", source)
    source = GUARD_RE.sub("", source)
    source = FRESHNESS_RE.sub("", source)

    if is_redesigned(source):
        # AI Outlook still runs and stores its own data, but the redesigned homepage
        # is no longer an AI-Outlook rendering surface. Do not restore legacy scripts.
        return source

    if "</body>" not in source:
        raise RuntimeError("Homepage closing body tag missing")
    runtime = FRESHNESS_SCRIPT + "\n" + GUARD_SCRIPT + "\n" + SCRIPT
    return source.replace("</body>", runtime + "\n</body>", 1)


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
