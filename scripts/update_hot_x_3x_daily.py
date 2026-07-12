#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the Hot X pipeline three times per day.

The previous workflow was switched to manual/curated mode and therefore stopped
refreshing data. This wrapper restores automatic updates while keeping the PL
translation and editorial fields produced by build_hot_x_en.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import build_hot_x_en as builder
import update_hot_x_topics as source

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hot_tweets.json"
INTERVAL_HOURS = 8
MIN_ITEMS = 3


def rotation_slot() -> int:
    """Rotate through all topic sets across successive 8-hour update blocks."""
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
            raise RuntimeError(f"Hot X update rejected: item {index} is invalid")
        required = ("title_en", "title_pl", "summary_en", "summary_pl")
        missing = [key for key in required if not str(item.get(key) or "").strip()]
        if missing:
            raise RuntimeError(f"Hot X update rejected: item {index} missing {', '.join(missing)}")
        if not (item.get("tweet_url") or item.get("search_url")):
            raise RuntimeError(f"Hot X update rejected: item {index} has no X destination")
        key = normalized(item.get("title_en") or item.get("title_pl"))
        if key and key in seen:
            raise RuntimeError(f"Hot X update rejected: duplicate title in item {index}")
        seen.add(key)


def normalize_metadata() -> None:
    data = json.loads(OUT.read_text(encoding="utf-8"))
    data["refresh_interval_hours"] = INTERVAL_HOURS
    data["update_frequency"] = "3_times_daily"
    data["rotation_slot"] = rotation_slot()
    data["rotation_slots_total"] = len(source.TOPIC_SLOTS)
    data["method_pl"] = (
        "Hot X jest aktualizowany automatycznie trzy razy dziennie. "
        "Każdy przebieg pobiera nowe tematy lub konkretne posty z X, przygotowuje wersję EN "
        "i tłumaczy wszystkie widoczne tytuły oraz opisy w wersji PL na język polski."
    )
    data["method_en"] = (
        "Hot X is updated automatically three times per day. Each run fetches new topics or "
        "specific X posts and prepares separate English and Polish display fields."
    )
    for item in data.get("items", []):
        selected = str(item.get("selected_by") or "")
        item["selected_by"] = selected.replace("4h", "8h").replace("manual-curation", "automatic-3x-daily")
        item["refresh_interval_hours"] = INTERVAL_HOURS
    validate_payload(data)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    # build_hot_x_en.py imports the same source module, so patching it here changes
    # both topic rotation and metadata for the entire pipeline.
    source.SLOT_HOURS = INTERVAL_HOURS
    source.current_slot_index = rotation_slot
    builder.hot.SLOT_HOURS = INTERVAL_HOURS
    builder.hot.current_slot_index = rotation_slot

    builder.main()
    normalize_metadata()
    print("Hot X updated automatically: 3 times daily / every 8 hours")


if __name__ == "__main__":
    main()
