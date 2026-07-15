#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Protect homepage/news feeds from empty, partial or broken updates.

The updater may fail at any intermediate stage, but the public JSON must never be
replaced by an empty or unusable payload. Before each run we save the current
last-known-good feeds. After the run we validate both languages, merge usable new
cards with the saved cards, or restore the previous feed when necessary.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from comment_quality import QUALITY_STATUS, QUALITY_VERSION, validate_comment

FILES = {
    "pl": (Path("pl/home_brief.json"), Path(".cache/home_brief_pl_last_good.json")),
    "en": (Path("en/home_brief.json"), Path(".cache/home_brief_en_last_good.json")),
}
MIN_VISIBLE_ITEMS = 6
URL_RE = re.compile(r"^https?://", re.I)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def comment(item: dict[str, Any]) -> str:
    return str(item.get("full_brief") or "").strip()


def valid_card(value: object, lang: str) -> bool:
    if not isinstance(value, dict):
        return False
    title = str(value.get("title") or "").strip()
    link = str(value.get("link") or "").strip()
    source = str(value.get("source") or "").strip()
    text = comment(value)
    status = str(value.get("comment_quality_status") or "")
    if not (
        title
        and source
        and URL_RE.match(link)
        and status == QUALITY_STATUS
        and value.get("comment_quality_version") == QUALITY_VERSION
        and value.get("summary_basis") == "article_text_ai_reviewed"
        and value.get("comment_generation_status") == "ai_review_approved"
    ):
        return False
    return validate_comment(text, lang).valid


def identity(item: dict[str, Any]) -> str:
    link = str(item.get("link") or "").strip().lower()
    if link:
        return link
    return re.sub(r"\W+", "", str(item.get("title") or "").lower())[:160]


def cards(data: dict[str, Any], section: str, lang: str) -> list[dict[str, Any]]:
    return [dict(x) for x in (data.get(section) or []) if valid_card(x, lang)]


def total_valid(data: dict[str, Any], lang: str) -> int:
    return len(cards(data, "latest", lang)) + len(cards(data, "radar", lang))


def merge(new: dict[str, Any], old: dict[str, Any], lang: str) -> dict[str, Any]:
    merged_latest: list[dict[str, Any]] = []
    merged_radar: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append(target: list[dict[str, Any]], item: dict[str, Any]) -> None:
        key = identity(item)
        if not key or key in seen:
            return
        seen.add(key)
        target.append(item)

    for item in cards(new, "latest", lang):
        append(merged_latest, item)
    for item in cards(new, "radar", lang):
        append(merged_radar, item)
    for item in cards(old, "latest", lang):
        if len(merged_latest) + len(merged_radar) >= MIN_VISIBLE_ITEMS:
            break
        append(merged_latest, item)
    for item in cards(old, "radar", lang):
        if len(merged_latest) + len(merged_radar) >= MIN_VISIBLE_ITEMS:
            break
        append(merged_radar, item)

    out = dict(new or old)
    out["latest"] = merged_latest
    out["radar"] = merged_radar
    out["count"] = len(merged_latest) + len(merged_radar)
    out["refresh_interval_hours"] = 4
    out["update_frequency"] = "every_4_hours"
    out["last_update_attempt_at"] = now_iso()
    return out


def backup_current() -> None:
    for lang, (data_path, backup_path) in FILES.items():
        current = load(data_path)
        count = total_valid(current, lang)
        if count < MIN_VISIBLE_ITEMS:
            print(f"{lang}: backup skipped; only {count} valid cards")
            continue
        current["homepage_last_good_protection"] = {
            "status": "saved_before_update",
            "visible_items": count,
            "rule": "Homepage/news updates run every four hours and cannot erase the last valid feed.",
        }
        save(backup_path, current)
        print(f"{lang}: saved {count} last-good homepage cards")


def validate_current() -> None:
    for lang, (data_path, backup_path) in FILES.items():
        current = load(data_path)
        previous = load(backup_path)
        new_count = total_valid(current, lang)
        old_count = total_valid(previous, lang)

        if new_count >= MIN_VISIBLE_ITEMS:
            protected = merge(current, previous, lang)
            protected["homepage_last_good_protection"] = {
                "status": "new_feed_validated",
                "new_valid_items": new_count,
                "previous_items_used": max(0, protected["count"] - new_count),
                "visible_items": protected["count"],
            }
            save(data_path, protected)
            save(backup_path, protected)
            print(f"{lang}: validated {protected['count']} homepage cards")
            continue

        if old_count:
            restored = merge(current, previous, lang)
            if restored["count"] < MIN_VISIBLE_ITEMS:
                restored = dict(previous)
                restored["last_update_attempt_at"] = now_iso()
                restored["refresh_interval_hours"] = 4
                restored["update_frequency"] = "every_4_hours"
            restored["homepage_last_good_protection"] = {
                "status": "restored_or_completed_from_last_good",
                "reason": "new_update_failed_or_had_too_few_approved_comments",
                "new_valid_items": new_count,
                "previous_valid_items": old_count,
                "visible_items": total_valid(restored, lang),
            }
            save(data_path, restored)
            save(backup_path, restored)
            print(f"{lang}: rejected incomplete update and kept {total_valid(restored, lang)} valid cards")
            continue

        # First-run safety: keep any usable cards instead of replacing the feed with nothing.
        degraded = merge(current, {}, lang)
        degraded["homepage_last_good_protection"] = {
            "status": "degraded_no_backup_available",
            "new_valid_items": new_count,
            "visible_items": degraded["count"],
        }
        save(data_path, degraded)
        print(f"{lang}: no backup available; preserved {degraded['count']} usable cards")


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backup", action="store_true")
    group.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    backup_current() if args.backup else validate_current()


if __name__ == "__main__":
    main()
