#!/usr/bin/env python3
"""US market adapter for the shared Daily Stock Core.

US-specific responsibilities stay outside the core: USD execution, NYSE/Nasdaq
calendar/session timing, Yahoo/Stooq market data and official SEC/company-release
evidence. Quant scoring and bounded learning are shared with GPW.

P0.2 also wraps the existing US execution quote in the shared immutable
CanonicalMarketSnapshot contract without changing ranking or risk geometry.

PR-C installs the US Daily persistence-boundary DecisionEnvelope/RiskPolicy
guard. Risk limits remain owned by the US engine config.
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
    from scripts import decision_envelope_adapters as decision_contract
    from scripts import market_snapshot_adapters as market_snapshot
    from scripts import us_daily_stock as us
except ModuleNotFoundError:
    import daily_stock_core as core
    import decision_envelope_adapters as decision_contract
    import market_snapshot_adapters as market_snapshot
    import us_daily_stock as us

_INSTALLED = False
_ORIGINAL_NEWS_ITEMS = us.news_items
_ORIGINAL_BASE_PAYLOAD = us.base_payload
_ORIGINAL_PUBLISH = us.publish
_ORIGINAL_OPENING_SNAPSHOT = us.opening_snapshot


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


def _canonical_opening_snapshot(symbol: str, *, now: datetime) -> dict[str, Any]:
    """Fail closed unless the US execution quote satisfies the P0.2 contract."""
    raw = _ORIGINAL_OPENING_SNAPSHOT(symbol, now=now)
    config = us.load_config()
    settings = config.get("data_gates") or {}
    policy = market_snapshot.equity_execution_policy(
        "us",
        max_age_seconds=float(settings.get("maximum_execution_quote_age_minutes", 20)) * 60.0,
        max_future_skew_seconds=float(settings.get("maximum_future_clock_skew_minutes", 2)) * 60.0,
    )
    return market_snapshot.attach_equity_canonical_snapshot(
        raw,
        market="us",
        received_at=now,
        created_at=now,
        decision_at=now,
        policy=policy,
    )


def methodology(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or us.load_config()
    value = core.methodology_contract(core.US_PROFILE, config)
    value["adapter"] = {
        "calendar": "NYSE/Nasdaq / America-New_York",
        "session_confirmation": "09:35 ET",
        "currency": "USD",
        "official_channels": ["SEC 8-K", "company releases", "independent financial news"],
        "market_memory_isolated": True,
        "canonical_market_snapshot": "briefrooms-market-snapshot-v1",
        "canonical_decision_envelope": "briefrooms-decision-envelope-v1",
        "risk_policy_ownership": "US_ENGINE",
    }
    return value


def _base_payload(now, config, decision, reason):
    payload = _ORIGINAL_BASE_PAYLOAD(now, config, decision, reason)
    payload.setdefault("methodology", {})["daily_stock_core"] = methodology(config)
    return payload


def _existing_polish_localization(selection: dict[str, Any]) -> dict[str, Any] | None:
    localized = (selection.get("localized") or {}).get("pl")
    if not isinstance(localized, dict):
        return None
    if not str(localized.get("thesis") or "").strip() or not str(localized.get("why_now") or "").strip():
        return None
    sources = selection.get("sources") or []
    source_ids = {str(row.get("id") or "") for row in sources if isinstance(row, dict) and row.get("id")}
    summaries = localized.get("source_summaries") or {}
    if source_ids and not source_ids.issubset(set(summaries)):
        return None
    return localized


def _polish_localization(selection: dict[str, Any]) -> dict[str, Any] | None:
    """Create a display-only Polish translation without changing ranking or trade gates."""
    existing = _existing_polish_localization(selection)
    if existing is not None:
        return existing

    runtime = us.get_ai_runtime()
    if runtime.provider != "gemini" or not runtime.available:
        return None

    sources = [row for row in (selection.get("sources") or []) if isinstance(row, dict)][:8]
    source_payload = [
        {
            "id": str(row.get("id") or ""),
            "publisher": str(row.get("publisher") or ""),
            "title": str(row.get("title") or ""),
        }
        for row in sources
        if row.get("id") and row.get("title")
    ]
    if not source_payload:
        return None

    prompt = {
        "task": "Prepare the Polish-language presentation of an already selected US Daily Stock trade for BriefRooms. Translate faithfully; do not change the investment conclusion, score, facts or numbers.",
        "rules": [
            "Write natural, concise Polish for a financially literate Polish reader.",
            "Do not add facts, opinions, forecasts or investment advice that are absent from the supplied English text.",
            "Keep company names, product names, trial phases, tickers and source publisher names unchanged where appropriate.",
            "Translate thesis and why_now faithfully, preserving the original meaning.",
            "Translate the activation instruction into concise Polish.",
            "For every supplied source id return one short Polish news summary based only on that source title; do not infer beyond the title.",
            "Each source summary should normally be one sentence and no longer than about 180 characters.",
        ],
        "selection": {
            "symbol": selection.get("symbol"),
            "name": selection.get("name"),
            "thesis": selection.get("thesis"),
            "why_now": selection.get("why_now"),
            "activation": selection.get("activation"),
            "sources": source_payload,
        },
        "output_schema": {
            "thesis": "Polish translation",
            "why_now": "Polish translation",
            "activation": "Polish translation",
            "sources": [{"id": "source-id", "summary": "short Polish summary"}],
        },
    }

    import requests

    result = us.request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=[
            {"role": "system", "content": "You are a precise financial translator. Preserve meaning and never invent facts."},
            {"role": "user", "content": us.json.dumps(prompt, ensure_ascii=False)},
        ],
        max_tokens=1800,
        temperature=0.1,
        timeout=45,
    )

    thesis = str(result.get("thesis") or "").strip()[:700]
    why_now = str(result.get("why_now") or "").strip()[:500]
    activation = str(result.get("activation") or "").strip()[:320]
    if not thesis or not why_now:
        return None

    allowed_ids = {row["id"] for row in source_payload}
    summaries: dict[str, str] = {}
    for row in result.get("sources") or []:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("id") or "")
        summary = str(row.get("summary") or "").strip()[:320]
        if source_id in allowed_ids and summary:
            summaries[source_id] = summary

    if allowed_ids and not allowed_ids.issubset(set(summaries)):
        return None

    return {
        "language": "pl",
        "source_language": "en",
        "thesis": thesis,
        "why_now": why_now,
        "activation": activation,
        "source_summaries": summaries,
    }


def _publish(payload: dict[str, Any]) -> None:
    if payload.get("decision") == "TRADE":
        selection = payload.get("selection") or {}
        snapshot = selection.get("market_snapshot") or {}
        if not snapshot.get("market_snapshot_id"):
            raise us.PublicationError("DATA_QUALITY_BLOCKED: US trade lacks canonical MarketSnapshot")
        selection["market_snapshot_id"] = snapshot["market_snapshot_id"]
        payload.setdefault("data_quality", {})["canonical_market_snapshot"] = (
            snapshot.get("canonical_data_quality") or {}
        ).get("status")
        try:
            localized = _polish_localization(selection)
        except Exception as exc:
            localized = None
            payload.setdefault("data_quality", {})["pl_localization"] = {
                "status": "unavailable",
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            }
        if localized:
            selection.setdefault("localized", {})["pl"] = localized
            payload["selection"] = selection
            payload.setdefault("data_quality", {})["pl_localization"] = {"status": "ready"}
    _ORIGINAL_PUBLISH(payload)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    us.clamp = core.clamp
    us.round2 = core.round2
    us.true_range = core.true_range
    us.return_over = core.return_over
    us.percentile_score = core.percentile_score
    us.build_candidate = _build_candidate
    us.normalize_cross_section = core.normalize_cross_section
    us.composite = core.composite_score
    us.news_items = combined_news_items
    us.opening_snapshot = _canonical_opening_snapshot
    us.base_payload = _base_payload
    us.publish = _publish
    decision_contract.install_us_persistence_guard(us)
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
