#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the Hot X pipeline twice daily while preserving editorial pins."""
from __future__ import annotations

import json
import os
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
PINS = ROOT / "data" / "hot_x_editorial_pins.json"
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
            raise RuntimeError(f"Hot X item {index} lacks an approved X destination or bilingual content")
    pins = load_items(PINS)
    if pins and [item.get("search_url") for item in items[: len(pins)]] != [item.get("search_url") for item in pins]:
        raise RuntimeError("Hot X update rejected: editorial pins were not preserved at the top")


def normalize_metadata() -> None:
    data = json.loads(OUT.read_text(encoding="utf-8"))
    pins = load_items(PINS)
    generated = data.get("items") or []
    data["items"] = select_unique(
        [pins, generated, load_items(LAST_GOOD), load_items(EMERGENCY)],
        target=TARGET_ITEMS,
    )
    data["refresh_interval_hours"] = INTERVAL_HOURS
    data["update_frequency"] = "2_times_daily"
    data["rotation_slot"] = rotation_slot()
    data["rotation_slots_total"] = len(source.TOPIC_SLOTS)
    data["initial_visible_items"] = MIN_VISIBLE_ITEMS
    data["target_items"] = TARGET_ITEMS
    data["editorial_pins_count"] = len(pins)
    data["method_pl"] = (
        "Cztery tematy redakcyjne pozostają przypięte na początku sekcji. Automatyczne odświeżenie dwa razy "
        "dziennie uzupełnia je zweryfikowanymi, bezpośrednimi postami z X i nie może zastąpić przypiętych linków."
    )
    data["method_en"] = (
        "Four editorial topics remain pinned at the top. Twice-daily automation supplements them with verified "
        "direct X posts and cannot replace the pinned links."
    )
    for item in data.get("items", []):
        selected = str(item.get("selected_by") or "")
        item["selected_by"] = re.sub(r"(?:4h|8h)", "12h", selected)
        item["refresh_interval_hours"] = INTERVAL_HOURS
    validate_payload(data)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    if not os.environ.get("X_BEARER_TOKEN"):
        raise RuntimeError("X_BEARER_TOKEN is missing; refusing to replace the verified Hot X feed")
    source.SLOT_HOURS = INTERVAL_HOURS
    source.current_slot_index = rotation_slot
    builder.hot.SLOT_HOURS = INTERVAL_HOURS
    builder.hot.current_slot_index = rotation_slot
    builder.main()
    normalize_metadata()
    print("Hot X updated automatically with four preserved editorial pins")


if __name__ == "__main__":
    main()
