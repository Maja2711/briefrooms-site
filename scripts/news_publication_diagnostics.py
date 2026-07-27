#!/usr/bin/env python3
"""Append privacy-safe source diagnostics for the atomic news publisher."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _events_path() -> Path | None:
    value = os.environ.get("BR_NEWS_DIAGNOSTIC_EVENTS", "").strip()
    return Path(value) if value else None


def _append(payload: dict[str, Any]) -> None:
    path = _events_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **payload,
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def record_feed(
    lang: str,
    url: str,
    *,
    parsed: int = 0,
    pipeline: str = "news",
    error: str = "",
) -> None:
    _append(
        {
            "kind": "feed",
            "lang": lang,
            "pipeline": pipeline,
            "url": str(url),
            "fetched": not bool(error),
            "parsed": max(0, int(parsed)),
            "error": str(error)[:300],
        }
    )


def record_item(
    lang: str,
    url: str,
    result: str,
    *,
    published_at: str = "",
    pipeline: str = "news",
) -> None:
    if result not in {
        "accepted",
        "rejected_stale",
        "rejected_duplicate",
        "rejected_invalid",
    }:
        raise ValueError(f"unsupported diagnostic result: {result}")
    _append(
        {
            "kind": "item",
            "lang": lang,
            "pipeline": pipeline,
            "url": str(url),
            "result": result,
            "published_at": str(published_at),
        }
    )


def parsed_time_iso(value: Any) -> str:
    """Return an RSS struct_time as UTC ISO-8601 without inventing a date."""
    if not value:
        return ""
    try:
        return datetime(*tuple(value)[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError):
        return ""
