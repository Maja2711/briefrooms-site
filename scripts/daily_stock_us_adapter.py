#!/usr/bin/env python3
"""US market adapter for the shared Daily Stock Core.

US-specific responsibilities stay outside the core: USD execution, NYSE/Nasdaq
calendar/session timing, Yahoo/Stooq market data and official SEC/company-release
evidence.  Quant scoring and bounded learning are shared with GPW.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
import urllib.parse
import xml.etree.ElementTree as ET

try:
    from scripts import daily_stock_core as core
    from scripts import us_daily_stock as us
except ModuleNotFoundError:
    import daily_stock_core as core
    import us_daily_stock as us

_INSTALLED = False
_ORIGINAL_NEWS_ITEMS = us.news_items
_ORIGINAL_BASE_PAYLOAD = us.base_payload


def _history() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not us.HISTORY_DIR.exists():
        return rows
    for path in sorted(us.HISTORY_DIR.glob("????-??-??.json")):
        try:
            value = us.load_json(path)
        except Exception:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _history_scorer(
    history: list[dict[str, Any]], sector: str, minimum_sample: int
) -> tuple[float, int]:
    config = us.load_config()
    learning = config.get("learning") or {}
    return core.bayesian_history_expectancy_score(
        history,
        sector,
        minimum_sample,
        recent_window=int(learning.get("recent_window", 20)),
        prior_strength=float(learning.get("prior_strength", 10.0)),
        max_adjustment=float(learning.get("max_historical_score_adjustment", 12.0)),
    )


def _build_candidate(company, bars, expected_day, config):
    return core.build_quant_candidate(
        company,
        bars,
        expected_day,
        config,
        core.US_PROFILE,
        history=_history(),
        history_scorer=_history_scorer,
    )


def _parse_sec_time(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=us.NEW_YORK)
    return parsed.astimezone(us.NEW_YORK)


def sec_items(company: dict[str, str], *, now: datetime, limit: int = 3) -> list[dict[str, Any]]:
    """Best-effort recent SEC 8-K evidence. Failure never weakens fail-closed gates."""
    ticker = urllib.parse.quote(str(company.get("symbol") or ""), safe="")
    if not ticker:
        return []
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar?"
        f"action=getcompany&CIK={ticker}&type=8-K&owner=exclude&count=20&output=atom"
    )
    try:
        root = ET.fromstring(us.request_bytes(url, timeout=15, attempts=1))
    except Exception:
        return []

    lookback = float((us.load_config().get("event_layer") or {}).get("official_report_lookback_hours", 120))
    items: list[dict[str, Any]] = []
    for entry in root.findall(".//{*}entry"):
        title = (entry.findtext("{*}title") or "").strip()
        updated = _parse_sec_time(entry.findtext("{*}updated") or entry.findtext("{*}published") or "")
        link_el = entry.find("{*}link")
        link = str(link_el.attrib.get("href") if link_el is not None else "").strip()
        if not title or not link or updated is None:
            continue
        age = (now - updated).total_seconds() / 3600.0
        if age < -1 or age > lookback:
            continue
        fingerprint = hashlib.sha1(f"SEC|{company.get('symbol')}|{title}|{updated.date()}".encode()).hexdigest()[:12]
        items.append(
            {
                "id": f"sec-{fingerprint}",
                "title": title[:240],
                "url": link,
                "publisher": "SEC",
                "published_at": updated.isoformat(timespec="minutes"),
                "age_hours": round(age, 1),
                "quality": "trusted",
                "source_kind": "official_filing",
                "channel": "SEC 8-K",
            }
        )
        if len(items) >= limit:
            break
    return items


def combined_news_items(company: dict[str, str], *, now: datetime, limit: int = 8) -> list[dict[str, Any]]:
    official = sec_items(company, now=now, limit=min(3, limit))
    try:
        general = _ORIGINAL_NEWS_ITEMS(company, now=now, limit=limit)
    except Exception:
        general = []
    release_tokens = ("business wire", "globenewswire", "pr newswire", "accesswire")
    for item in general:
        publisher = str(item.get("publisher") or "").casefold()
        item.setdefault("source_kind", "company_release" if any(token in publisher for token in release_tokens) else "independent_news")
        item.setdefault("channel", "company release" if item["source_kind"] == "company_release" else "news")

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for item in [*official, *general]:
        identity = f"{item.get('url')}|{item.get('title')}".casefold()
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def methodology(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or us.load_config()
    value = core.methodology_contract(core.US_PROFILE, config)
    value["adapter"] = {
        "calendar": "NYSE/Nasdaq / America-New_York",
        "session_confirmation": "09:35 ET",
        "currency": "USD",
        "official_channels": ["SEC 8-K", "company releases", "independent financial news"],
        "market_memory_isolated": True,
    }
    return value


def _base_payload(now, config, decision, reason):
    payload = _ORIGINAL_BASE_PAYLOAD(now, config, decision, reason)
    payload.setdefault("methodology", {})["daily_stock_core"] = methodology(config)
    return payload


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    us.clamp = core.clamp
    us.round2 = core.round2
    us.true_range = core.true_range
    us.return_over = core.return_over
    us.percentile = core.percentile_score
    us.build_candidate = _build_candidate
    us.normalize_cross_section = core.normalize_cross_section
    us.composite = core.composite_score
    us.news_items = combined_news_items
    us.base_payload = _base_payload
    _INSTALLED = True


def main() -> int:
    install()
    try:
        from scripts import us_daily_stock_runtime as runtime
    except ModuleNotFoundError:
        import us_daily_stock_runtime as runtime
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())
