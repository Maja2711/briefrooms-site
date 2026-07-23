#!/usr/bin/env python3
"""Keep only source-linked photographic cards on BriefRooms homepages."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- HOME_BRIEFS_START -->"
END = "<!-- HOME_BRIEFS_END -->"
SCRIPT = '<script src="/scripts/homepage-photo-only.js?v=1" defer></script>'


def photo_card(card: str) -> bool:
    return bool(
        'class="thumb has-image"' in card
        and re.search(r"<img\b[^>]+data-br-external-media=[\"']source-linked[\"']", card, re.I)
        and "media-fallback-active" not in card
    )


def filter_marker_block(source: str, label: str) -> str:
    marker = re.compile(
        rf"({re.escape(START)})(.*?)({re.escape(END)})",
        re.S,
    )
    match = marker.search(source)
    if not match:
        raise RuntimeError(f"Homepage markers missing: {label}")

    cards = re.findall(r'<a class="brief-card" href="[^"]+">.*?</a>', match.group(2), re.S)
    kept = [card for card in cards if photo_card(card)]
    if not kept:
        print(f"WARNING: {label} has no source-linked photo cards after filtering")
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
    if SCRIPT not in source:
        if "</body>" not in source:
            raise RuntimeError("Homepage closing body tag missing")
        source = source.replace("</body>", SCRIPT + "\n</body>", 1)
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
