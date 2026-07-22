#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from hot_x_items import (
    INITIAL_VISIBLE_ITEMS,
    TOTAL_ITEMS,
    comment_text,
    is_direct_post,
    select_unique,
    valid_item,
)

DATA = Path("data/hot_tweets.json")
BACKUP = Path(".cache/hot_tweets_comments_last_good.json")
EMERGENCY = Path("data/hot_x_emergency.json")
MIN_VISIBLE_ITEMS = INITIAL_VISIBLE_ITEMS
TARGET_ITEMS = TOTAL_ITEMS
MIN_DAILY_DIRECT_POSTS = 3
MAX_DIRECT_POST_AGE_HOURS = 36
GENERIC_TITLE = re.compile(r"^(temat z x|topic from x|hot x topic)", re.I)
GENERIC_TEXT = re.compile(r"^(na x monitorowany jest|pełny wątek otwiera link|rotating hot x topic|temat dotyczy)", re.I)


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def text(item: dict, lang: str) -> str:
    return comment_text(item, lang)


def substantive(value: str) -> bool:
    value = re.sub(r"\s+", " ", value).strip()
    return len(value) >= 100 and len(value.split()) >= 14 and not GENERIC_TEXT.search(value)


def valid(item: object) -> bool:
    if not valid_item(item, substantive=True):
        return False
    assert isinstance(item, dict)
    title_pl = str(item.get("title_pl") or "").strip()
    title_en = str(item.get("title_en") or "").strip()
    return bool(
        not GENERIC_TITLE.search(title_pl)
        and not GENERIC_TITLE.search(title_en)
        and substantive(text(item, "pl"))
        and substantive(text(item, "en"))
    )


def items(data: dict) -> list[dict]:
    out = []
    for value in data.get("items") or []:
        if not valid(value):
            continue
        item = dict(value)
        item["comment_pl"] = text(item, "pl")
        item["comment_en"] = text(item, "en")
        out.append(item)
    return out


def fresh_direct_posts(values: list[dict], now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - MAX_DIRECT_POST_AGE_HOURS * 3600
    fresh = []
    for item in values:
        if not is_direct_post(item.get("tweet_url")):
            continue
        stamp = item.get("x_post_created_at") or item.get("selected_at")
        try:
            created = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if created.timestamp() >= cutoff:
            fresh.append(item)
    return fresh


def backup() -> None:
    data = load(DATA)
    good = select_unique([items(data)], target=TARGET_ITEMS, substantive=True)
    if len(good) < MIN_VISIBLE_ITEMS:
        print(f"Hot X comment backup skipped: {len(good)} valid cards")
        return
    data["items"] = good
    save(BACKUP, data)
    print(f"Hot X comment backup saved: {len(good)} cards")


def validate(*, require_fresh_posts: bool = False) -> None:
    current = load(DATA)
    previous = load(BACKUP)
    emergency = load(EMERGENCY)
    new = select_unique([items(current)], target=TARGET_ITEMS, substantive=True)
    old = select_unique([items(previous)], target=TARGET_ITEMS, substantive=True)
    emergency_items = select_unique([items(emergency)], target=TARGET_ITEMS, substantive=True)
    merged = select_unique([new, old, emergency_items], target=TARGET_ITEMS, substantive=True)
    direct = fresh_direct_posts(new)
    daily_gate_passed = len(direct) >= MIN_DAILY_DIRECT_POSTS
    if require_fresh_posts and not daily_gate_passed:
        if old:
            out = dict(previous)
            out["items"] = old
            out["last_update_attempt_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            out["last_good_protection"] = {
                "status": "daily_direct_post_gate_failed_last_good_restored",
                "fresh_direct_posts": len(direct),
                "required_fresh_direct_posts": MIN_DAILY_DIRECT_POSTS,
            }
            save(DATA, out)
        raise SystemExit(f"Hot X daily publication rejected: {len(direct)}/{MIN_DAILY_DIRECT_POSTS} fresh direct X posts")
    if len(merged) < MIN_VISIBLE_ITEMS:
        raise SystemExit("Hot X has no protected set of two substantive, unique comments")
    if len(new) >= MIN_VISIBLE_ITEMS:
        out = dict(current)
        status = "new_comments_validated"
    elif old:
        out = dict(previous)
        status = "generic_or_partial_update_completed_from_last_good"
    else:
        out = dict(emergency)
        status = "generic_or_partial_update_completed_from_emergency"
    out["items"] = merged
    out["refresh_interval_hours"] = 12
    out["update_frequency"] = "2_times_daily"
    out["initial_visible_items"] = MIN_VISIBLE_ITEMS
    out["target_items"] = TARGET_ITEMS
    out["last_update_attempt_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if daily_gate_passed:
        out["last_successful_direct_posts_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["last_good_protection"] = {
        "status": status,
        "new_substantive_items": len(new),
        "fresh_direct_posts": len(direct),
        "required_fresh_direct_posts": MIN_DAILY_DIRECT_POSTS,
        "visible_items": len(merged),
        "rule_pl": "Sekcja zachowuje co najmniej dwie widoczne karty i uzupełnia rozwijany zestaw do maksymalnie ośmiu unikalnych tematów.",
        "rule_en": "The section keeps at least two visible cards and completes the expanded set with up to eight unique topics."
    }
    save(DATA, out)
    save(BACKUP, out)
    print(f"Hot X comments protected: {len(merged)} cards / {status}")


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backup", action="store_true")
    group.add_argument("--validate", action="store_true")
    parser.add_argument("--require-fresh-posts", action="store_true")
    args = parser.parse_args()
    backup() if args.backup else validate(require_fresh_posts=args.require_fresh_posts)


if __name__ == "__main__":
    main()
