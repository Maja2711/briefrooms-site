#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the Hot X pipeline twice per day with a 12-hour rotation."""
from __future__ import annotations

import json
import re
from pathlib import Path

import build_hot_x_en as builder
import update_hot_x_topics as source
from hot_x_items import (
    INITIAL_VISIBLE_ITEMS,
    TOTAL_ITEMS,
    duplicate_free,
    select_unique,
    valid_item,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hot_tweets.json"
LAST_GOOD = ROOT / ".cache" / "hot_tweets_comments_last_good.json"
EMERGENCY = ROOT / "data" / "hot_x_emergency.json"
INTERVAL_HOURS = 12
MIN_VISIBLE_ITEMS = INITIAL_VISIBLE_ITEMS
TARGET_ITEMS = TOTAL_ITEMS


def rotation_slot() -> int:
    block = int(source.now_dt().timestamp() // (INTERVAL_HOURS * 3600))
    return block % len(source.TOPIC_SLOTS)


def load_items(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [dict(item) for item in data.get("items") or [] if isinstance(item, dict)]
    except Exception:
        return []


def validate_payload(data: dict) -> None:
    items = data.get("items")
    if not isinstance(items, list) or len(items) < MIN_VISIBLE_ITEMS:
        raise RuntimeError(f"Hot X update rejected: expected at least {MIN_VISIBLE_ITEMS} items")
    if len(items) > TARGET_ITEMS:
        raise RuntimeError(f"Hot X update rejected: expected at most {TARGET_ITEMS} items")
    if not duplicate_free(items):
        raise RuntimeError("Hot X update rejected: duplicate URL, title or category overflow")
    for index, item in enumerate(items, start=1):
        if not valid_item(item):
            raise RuntimeError(f"Hot X item {index} lacks a valid X URL or bilingual content")


def normalize_metadata() -> None:
    data = json.loads(OUT.read_text(encoding="utf-8"))
    data["items"] = select_unique(
        [data.get("items") or [], load_items(LAST_GOOD), load_items(EMERGENCY)],
        target=TARGET_ITEMS,
    )
    data["refresh_interval_hours"] = INTERVAL_HOURS
    data["update_frequency"] = "2_times_daily"
    data["rotation_slot"] = rotation_slot()
    data["rotation_slots_total"] = len(source.TOPIC_SLOTS)
    data["initial_visible_items"] = MIN_VISIBLE_ITEMS
    data["target_items"] = TARGET_ITEMS
    data["method_pl"] = (
        "Hot X jest odświeżany dwa razy dziennie. Sekcja zachowuje co najmniej dwie widoczne karty "
        "i uzupełnia rozwijany zestaw do maksymalnie ośmiu unikalnych tematów. Pusty lub ogólnikowy "
        "wynik nie usuwa ostatnich poprawnych kart."
    )
    data["method_en"] = (
        "Hot X refreshes twice daily. A new card may replace the previous one only when it has "
        "a valid X destination and a substantive comment. The section keeps at least two visible cards and "
        "completes the expanded set with up to eight unique topics."
    )
    for item in data.get("items", []):
        selected = str(item.get("selected_by") or "")
        item["selected_by"] = re.sub(r"(?:4h|8h)", "12h", selected)
        item["refresh_interval_hours"] = INTERVAL_HOURS
    validate_payload(data)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    source.SLOT_HOURS = INTERVAL_HOURS
    source.current_slot_index = rotation_slot
    builder.hot.SLOT_HOURS = INTERVAL_HOURS
    builder.hot.current_slot_index = rotation_slot
    builder.main()
    normalize_metadata()
    print("Hot X updated automatically: twice daily / every 12 hours")


if __name__ == "__main__":
    main()
