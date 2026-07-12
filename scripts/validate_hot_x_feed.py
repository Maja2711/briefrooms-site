#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Protect Hot X from being replaced by an empty or unusable update.

Usage:
  python scripts/validate_hot_x_feed.py --backup
  python scripts/validate_hot_x_feed.py --validate

Before the update we save the current valid feed. After the update we validate
new cards and supplement missing cards from the last-good feed. A failed update
must never blank the Hot X section.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DATA = Path("data/hot_tweets.json")
BACKUP = Path(".cache/hot_tweets_last_good.json")
MIN_ITEMS = 3
X_URL = re.compile(r"^https?://(?:x\.com|twitter\.com)/(?:[^/\s]+/status/\d+|search(?:\?|/)|explore(?:\?|/|$))", re.I)


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def item_url(item: dict) -> str:
    for key in ("tweet_url", "search_url"):
        value = str(item.get(key) or "").strip()
        if X_URL.search(value):
            return value
    return ""


def valid_item(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    title_pl = str(item.get("title_pl") or "").strip()
    title_en = str(item.get("title_en") or "").strip()
    summary_pl = str(item.get("summary_pl") or "").strip()
    summary_en = str(item.get("summary_en") or "").strip()
    return bool(item_url(item) and (title_pl or title_en) and (summary_pl or summary_en))


def valid_items(data: dict) -> list[dict]:
    return [dict(x) for x in (data.get("items") or []) if valid_item(x)]


def identity(item: dict) -> str:
    return item_url(item) or re.sub(r"\W+", "", str(item.get("title_en") or item.get("title_pl") or "").lower())[:120]


def backup_current() -> None:
    current = load(DATA)
    items = valid_items(current)
    if not items:
        print("Hot X backup skipped: current feed has no valid cards")
        return
    current["items"] = items
    current["last_good_protection"] = "saved_before_update"
    save(BACKUP, current)
    print(f"Hot X last-good backup saved: {len(items)} cards")


def validate_current() -> None:
    current = load(DATA)
    previous = load(BACKUP)
    new_items = valid_items(current)
    old_items = valid_items(previous)

    if not new_items and old_items:
        restored = dict(previous)
        restored["last_good_protection"] = {
            "status": "restored_previous_feed",
            "reason": "new_update_had_no_usable_x_cards",
            "visible_items": len(old_items),
        }
        save(DATA, restored)
        print(f"Hot X update rejected; restored {len(old_items)} last-good cards")
        return

    merged: list[dict] = []
    seen: set[str] = set()
    for item in new_items + old_items:
        key = identity(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= max(MIN_ITEMS, len(new_items)):
            break

    if not merged:
        raise SystemExit("Hot X validation failed: no usable new or previous cards")

    current["items"] = merged
    current["min_items"] = MIN_ITEMS
    current["last_good_protection"] = {
        "status": "validated",
        "new_valid_items": len(new_items),
        "previous_items_used": max(0, len(merged) - len(new_items)),
        "visible_items": len(merged),
        "rule": "A failed or partial update cannot blank Hot X; last-good cards remain visible.",
    }
    save(DATA, current)
    save(BACKUP, current)
    print(f"Hot X validated: {len(merged)} visible cards")


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backup", action="store_true")
    group.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    if args.backup:
        backup_current()
    else:
        validate_current()


if __name__ == "__main__":
    main()
