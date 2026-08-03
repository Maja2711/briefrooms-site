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
    clean_x_url,
    duplicate_free,
    is_direct_post,
    select_unique,
    valid_item,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hot_tweets.json"
PINS = ROOT / "data" / "hot_x_editorial_pins.json"
LAST_GOOD = ROOT / ".cache" / "hot_tweets_comments_last_good.json"
EMERGENCY = ROOT / "data" / "hot_x_emergency.json"
INTERVAL_HOURS = 12
MAX_POST_AGE_HOURS = 7 * 24
MIN_VISIBLE_ITEMS = INITIAL_VISIBLE_ITEMS
TARGET_ITEMS = TOTAL_ITEMS
TRUSTED_ACCOUNTS = (
    "Reuters",
    "ReutersBiz",
    "business",
    "FT",
    "BBCWorld",
    "BBCBreaking",
    "OpenAI",
    "OpenAIDevs",
    "CoinDesk",
)


def rotation_slot() -> int:
    block = int(source.now_dt().timestamp() // (INTERVAL_HOURS * 3600))
    return block % len(source.TOPIC_SLOTS)


def trusted_x_recent_query(topic: dict[str, str]) -> str:
    """Search recent posts only from established editorial or primary accounts."""
    terms: list[str] = []
    for word in re.findall(r"[A-Za-z0-9+#.-]+", topic.get("query") or ""):
        if word.casefold() in source.SOURCE_WORDS or len(word) < 3:
            continue
        terms.append(word)
    alternatives = " OR ".join(dict.fromkeys(terms[:6])) or "news"
    accounts = " OR ".join(f"from:{account}" for account in TRUSTED_ACCOUNTS)
    return f"({alternatives}) ({accounts}) lang:en -is:retweet -is:reply"


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
    visible_pin_urls = [
        clean_x_url(item.get("search_url")) for item in items[: len(pins)]
    ]
    expected_pin_urls = [clean_x_url(item.get("search_url")) for item in pins]
    if pins and visible_pin_urls != expected_pin_urls:
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
    data["max_post_age_hours"] = MAX_POST_AGE_HOURS
    data["trusted_accounts"] = list(TRUSTED_ACCOUNTS)
    data["update_frequency"] = "2_times_daily"
    data["rotation_slot"] = rotation_slot()
    data["rotation_slots_total"] = len(source.TOPIC_SLOTS)
    data["initial_visible_items"] = MIN_VISIBLE_ITEMS
    data["target_items"] = TARGET_ITEMS
    data["editorial_pins_count"] = len(pins)
    data["method_pl"] = (
        "Cztery tematy redakcyjne pozostają przypięte na początku sekcji. Automatyczne odświeżenie dwa razy "
        "dziennie uzupełnia je bezpośrednimi postami z ostatnich siedmiu dni wyłącznie z zatwierdzonych kont X."
    )
    data["method_en"] = (
        "Four editorial topics remain pinned at the top. Twice-daily automation supplements them with direct "
        "posts from the last seven days, restricted to approved X accounts."
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
    source.MAX_X_POST_AGE_HOURS = MAX_POST_AGE_HOURS
    source.current_slot_index = rotation_slot
    source.x_recent_query = trusted_x_recent_query
    builder.hot.SLOT_HOURS = INTERVAL_HOURS
    builder.hot.MAX_X_POST_AGE_HOURS = MAX_POST_AGE_HOURS
    builder.hot.current_slot_index = rotation_slot
    builder.hot.x_recent_query = trusted_x_recent_query

    previous_bytes = OUT.read_bytes() if OUT.exists() else b""
    previous_direct_urls = {
        item.get("tweet_url")
        for item in load_items(OUT)
        if is_direct_post(item.get("tweet_url"))
    }
    builder.main()
    normalize_metadata()
    current_direct_urls = {
        item.get("tweet_url")
        for item in load_items(OUT)
        if is_direct_post(item.get("tweet_url"))
    }
    if not current_direct_urls - previous_direct_urls:
        if previous_bytes:
            OUT.write_bytes(previous_bytes)
        elif OUT.exists():
            OUT.unlink()
        print("No new verified direct X posts; preserved the previous feed and timestamp.")
        return
    print("Hot X updated automatically with recent posts from trusted accounts")


if __name__ == "__main__":
    main()
