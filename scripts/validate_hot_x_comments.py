#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("data/hot_tweets.json")
BACKUP = Path(".cache/hot_tweets_comments_last_good.json")
EMERGENCY = Path("data/hot_x_emergency.json")
MIN_ITEMS = 3
X_URL = re.compile(r"^https?://(?:x\.com|twitter\.com)/", re.I)
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


def url(item: dict) -> str:
    for key in ("tweet_url", "search_url"):
        value = str(item.get(key) or "").strip()
        if X_URL.match(value):
            return value
    return ""


def text(item: dict, lang: str) -> str:
    exact = str(item.get("x_post_text_raw") or item.get("x_post_text") or "").strip()
    if exact:
        return exact
    return str(item.get(f"comment_{lang}") or item.get(f"summary_{lang}") or "").strip()


def substantive(value: str) -> bool:
    value = re.sub(r"\s+", " ", value).strip()
    return len(value) >= 100 and len(value.split()) >= 14 and not GENERIC_TEXT.search(value)


def valid(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    title_pl = str(item.get("title_pl") or "").strip()
    title_en = str(item.get("title_en") or "").strip()
    return bool(url(item) and title_pl and title_en and not GENERIC_TITLE.search(title_pl) and not GENERIC_TITLE.search(title_en) and substantive(text(item, "pl")) and substantive(text(item, "en")))


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


def key(item: dict) -> str:
    return url(item) or re.sub(r"\W+", "", str(item.get("title_pl") or "").lower())[:140]


def backup() -> None:
    data = load(DATA)
    good = items(data)
    if len(good) < MIN_ITEMS:
        print(f"Hot X comment backup skipped: {len(good)} valid cards")
        return
    data["items"] = good
    save(BACKUP, data)
    print(f"Hot X comment backup saved: {len(good)} cards")


def validate() -> None:
    current = load(DATA)
    previous = load(BACKUP)
    emergency = load(EMERGENCY)
    new = items(current)
    old = items(previous)
    reserve = old + items(emergency)
    merged = []
    seen = set()
    for item in new + reserve:
        identity = key(item)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        merged.append(item)
        if len(merged) >= max(MIN_ITEMS, len(new)):
            break
    if len(merged) < MIN_ITEMS:
        raise SystemExit("Hot X has no protected set of three substantive comments")
    if len(new) >= MIN_ITEMS:
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
    out["last_update_attempt_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["last_good_protection"] = {
        "status": status,
        "new_substantive_items": len(new),
        "visible_items": len(merged),
        "rule": "Hot X keeps at least three cards with substantive comments. Empty, placeholder and generic output is rejected."
    }
    save(DATA, out)
    save(BACKUP, out)
    print(f"Hot X comments protected: {len(merged)} cards / {status}")


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backup", action="store_true")
    group.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    backup() if args.backup else validate()


if __name__ == "__main__":
    main()
