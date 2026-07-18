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
from pathlib import Path

from hot_x_items import INITIAL_VISIBLE_ITEMS, TOTAL_ITEMS, select_unique, valid_item as shared_valid_item

DATA = Path("data/hot_tweets.json")
BACKUP = Path(".cache/hot_tweets_last_good.json")
EMERGENCY = Path("data/hot_x_emergency.json")
MIN_VISIBLE_ITEMS = INITIAL_VISIBLE_ITEMS
TARGET_ITEMS = TOTAL_ITEMS


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def valid_item(item: object) -> bool:
    return shared_valid_item(item)


def valid_items(data: dict) -> list[dict]:
    return [dict(x) for x in (data.get("items") or []) if valid_item(x)]


def backup_current() -> None:
    current = load(DATA)
    items = select_unique([valid_items(current)], target=TARGET_ITEMS)
    if len(items) < MIN_VISIBLE_ITEMS:
        print(f"Hot X backup skipped: current feed has only {len(items)} valid unique cards")
        return
    current["items"] = items
    current["last_good_protection"] = "saved_before_update"
    save(BACKUP, current)
    print(f"Hot X last-good backup saved: {len(items)} cards")


def validate_current() -> None:
    current = load(DATA)
    previous = load(BACKUP)
    emergency = load(EMERGENCY)
    new_items = select_unique([valid_items(current)], target=TARGET_ITEMS)
    old_items = select_unique([valid_items(previous)], target=TARGET_ITEMS)
    emergency_items = select_unique([valid_items(emergency)], target=TARGET_ITEMS)
    merged = select_unique([new_items, old_items, emergency_items], target=TARGET_ITEMS)
    if len(merged) < MIN_VISIBLE_ITEMS:
        raise SystemExit("Hot X validation failed: fewer than two usable unique cards")

    if len(new_items) >= MIN_VISIBLE_ITEMS:
        output = dict(current)
        status = "validated"
    elif old_items:
        output = dict(previous)
        status = "completed_from_last_good"
    else:
        output = dict(emergency)
        status = "completed_from_emergency"
    output["items"] = merged
    output["initial_visible_items"] = MIN_VISIBLE_ITEMS
    output["target_items"] = TARGET_ITEMS
    output["last_good_protection"] = {
        "status": status,
        "new_valid_items": len(new_items),
        "previous_items_used": max(0, len(merged) - len(new_items)),
        "visible_items": len(merged),
        "rule": "At least two cards remain visible; the expanded set is completed with up to eight unique topics.",
    }
    save(DATA, output)
    save(BACKUP, output)
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
