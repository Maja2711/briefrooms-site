#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the Hot X pipeline twice per day with a 12-hour rotation."""
from __future__ import annotations

import json
import re
from pathlib import Path

import build_hot_x_en as builder
import update_hot_x_topics as source

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hot_tweets.json"
INTERVAL_HOURS = 12
MIN_ITEMS = 3


def rotation_slot() -> int:
    block = int(source.now_dt().timestamp() // (INTERVAL_HOURS * 3600))
    return block % len(source.TOPIC_SLOTS)


def normalized(value: str) -> str:
    return re.sub(r"\W+", "", str(value or "").lower())


def validate_payload(data: dict) -> None:
    items = data.get("items")
    if not isinstance(items, list) or len(items) < MIN_ITEMS:
        raise RuntimeError(f"Hot X update rejected: expected at least {MIN_ITEMS} items")
    seen = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"Hot X item {index} is invalid")
        if not (item.get("tweet_url") or item.get("search_url")):
            raise RuntimeError(f"Hot X item {index} has no X destination")
        if not str(item.get("title_pl") or "").strip() or not str(item.get("summary_pl") or "").strip():
            raise RuntimeError(f"Hot X item {index} has no Polish title/comment")
        key = normalized(item.get("title_en") or item.get("title_pl"))
        if key and key in seen:
            raise RuntimeError(f"Hot X item {index} duplicates another topic")
        seen.add(key)


def normalize_metadata() -> None:
    data = json.loads(OUT.read_text(encoding="utf-8"))
    data["refresh_interval_hours"] = INTERVAL_HOURS
    data["update_frequency"] = "2_times_daily"
    data["rotation_slot"] = rotation_slot()
    data["rotation_slots_total"] = len(source.TOPIC_SLOTS)
    data["method_pl"] = (
        "Hot X jest odświeżany dwa razy dziennie. Nowy przebieg może zastąpić kartę tylko wtedy, "
        "gdy ma poprawny link do X oraz konkretny komentarz po polsku. Pusty lub ogólnikowy wynik "
        "nie usuwa ostatnich poprawnych kart."
    )
    data["method_en"] = (
        "Hot X refreshes twice daily. A new card may replace the previous one only when it has "
        "a valid X destination and a substantive comment. Empty or generic output keeps the last valid cards."
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
