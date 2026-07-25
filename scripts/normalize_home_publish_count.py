#!/usr/bin/env python3
"""Keep the homepage feed and rendered cards atomically at exactly 8 or 10."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATHS = {
    "pl": (ROOT / "pl/home_brief.json", ROOT / "pl/index.html"),
    "en": (ROOT / "en/home_brief.json", ROOT / "en/index.html"),
}
START = "<!-- HOME_BRIEFS_START -->"
END = "<!-- HOME_BRIEFS_END -->"
CARD_RE = re.compile(
    r'(<a class="brief-card" href="([^"]+)"[\s\S]*?</a>)', re.IGNORECASE
)


def normalize(lang: str) -> int:
    feed_path, index_path = PATHS[lang]
    feed = json.loads(feed_path.read_text(encoding="utf-8"))
    source = index_path.read_text(encoding="utf-8")
    before, separator, remainder = source.partition(START)
    if not separator:
        raise AssertionError(f"{lang}: homepage start marker missing")
    block, separator, after = remainder.partition(END)
    if not separator:
        raise AssertionError(f"{lang}: homepage end marker missing")

    cards = CARD_RE.findall(block)
    if len(cards) < 8:
        raise AssertionError(f"{lang}: only {len(cards)} rendered homepage cards")
    target = 10 if len(cards) >= 10 else 8
    selected_cards = cards[:target]
    selected_links = [href for _, href in selected_cards]

    latest = feed.get("latest")
    if not isinstance(latest, list):
        raise AssertionError(f"{lang}: homepage feed latest is not a list")
    by_link = {str(item.get("permalink", "")): item for item in latest}
    selected_items = []
    for link in selected_links:
        item = by_link.get(link)
        if item is None:
            raise AssertionError(f"{lang}: rendered card missing from feed: {link}")
        selected_items.append(item)

    feed["latest"] = selected_items
    feed["count"] = target
    feed_path.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    new_block = "\n" + "\n".join(card for card, _ in selected_cards) + "\n"
    index_path.write_text(before + START + new_block + END + after, encoding="utf-8")
    print(f"OK {lang}: normalized rendered homepage and feed to {target} briefs")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", choices=sorted(PATHS), required=True)
    args = parser.parse_args()
    normalize(args.lang)


if __name__ == "__main__":
    main()
