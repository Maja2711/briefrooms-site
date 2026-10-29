#!/usr/bin/env python3
"""Stock Trading quote freshness watchdog (display data only).

Monitors *every currently OPEN position* on GPW and NYSE, according to the
reviewed exchange calendars. During regular trading, independently checks the
source-observation time (not file updated_at or provider received_at). Recovers
stale marks without touching trading decisions, fills, last_mark, SL or TP.

NO RETROACTIVE EXECUTION. Quotes are not execution prices.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

try:
    from scripts import nyse_session_calendar as nyse
    from scripts import stock_trading_quote_enricher as provider
    from scripts.stock_trading_session_calendar import GPW_CLOSED, GPW_YEARS
except ModuleNotFoundError:  # direct invocation via scripts/ path
    import nyse_session_calendar as nyse
    import stock_trading_quote_enricher as provider
    from stock_trading_session_calendar import GPW_CLOSED, GPW_YEARS

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_PATH = ROOT / "data/investments/stock_trading_portfolio.json"
HEALTH_PATH = ROOT / "data/investments/stock_trading_quote_watchdog.json"
# Allow the measured Yahoo/GPW delayed feed its normal 15-minute delay.
MAX_AGE_SECONDS = {"GPW": 20 * 60, "US": 10 * 60}
MARKET_ZONE = {"GPW": ZoneInfo("Europe/Warsaw"), "US": ZoneInfo("America/New_York")}
START_GRACE = timedelta(minutes=5)
FUTURE_TOLERANCE = timedelta(seconds=120)


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(UTC) if dt.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def trading_session(market: str, now: datetime) -> str:
    """ACTIVE, CLOSED or UNVERIFIED; provider timestamps never define sessions."""
    if now.tzinfo is None:
        raise ValueError("Watchdog requires timezone-aware clock")
    local = now.astimezone(MARKET_ZONE[market])
    day = local.date()
    if market == "GPW":
        if day.year not in GPW_YEARS:
            return "UNVERIFIED"
        if day.weekday() >= 5 or day.isoformat() in GPW_CLOSED:
            return "CLOSED"
        opening, closing = time(9, 0), time(17, 5)
    else:
        calendar = nyse.session_for(day)
        if calendar["calendar_status"] != "VERIFIED":
            return "UNVERIFIED"
        if not calendar["session_open"]:
            return "CLOSED"
        opening, closing = time(9, 30), calendar["close_time"]
    start = datetime.combine(day, opening, tzinfo=MARKET_ZONE[market]) + START_GRACE
    end = datetime.combine(day, closing, tzinfo=MARKET_ZONE[market])
    return "ACTIVE" if start <= local < end else "CLOSED"


def position_symbol(position: Mapping[str, Any]) -> str:
    return str(position.get("symbol") or position.get("ticker") or "").strip().upper()


def assess(mark: Any, market: str, now: datetime) -> tuple[str, int | None]:
    if not isinstance(mark, Mapping):
        return "NO_VERIFIED_PRICE", None
    try:
        price = float(mark.get("price"))
    except (TypeError, ValueError):
        return "NO_VERIFIED_PRICE", None
    if not math.isfinite(price) or price <= 0:
        return "NO_VERIFIED_PRICE", None
    observed = parse_dt(mark.get("observed_at"))
    if observed is None:
        return "NO_OBSERVATION_TIMESTAMP", None
    age = (now - observed).total_seconds()
    if age < -FUTURE_TOLERANCE.total_seconds():
        return "FUTURE_OBSERVATION", round(age)
    if str(mark.get("market_state") or "").upper() != "REGULAR":
        return "NON_REGULAR_OBSERVATION", round(age)
    if age > MAX_AGE_SECONDS[market]:
        return "STALE", round(age)
    return "FRESH", max(0, round(age))


def recover(position: dict[str, Any], market: str, now: datetime) -> list[str]:
    """Only replace current_mark if a newer, actually fresh source quote exists."""
    symbol = position_symbol(position)
    failures: list[str] = []
    original = position.get("current_mark")
    original_time = parse_dt(original.get("observed_at")) if isinstance(original, Mapping) else None
    sources = [("Yahoo Finance chart", provider.quote_for_symbol)]
    if market == "GPW":
        sources.append(("Stooq.pl current quote", provider._stooq_quote))
    for source_name, fetch in sources:
        try:
            candidate = fetch(symbol, market, now_utc=now)
            status, _ = assess(candidate, market, now)
            timestamp = parse_dt(candidate.get("observed_at"))
            if status != "FRESH" or timestamp is None:
                failures.append(f"{source_name}: {status}")
                continue
            if original_time is not None and timestamp <= original_time:
                failures.append(f"{source_name}: observation_not_newer")
                continue
            position["current_mark"] = candidate
            if position.get("quote_refresh_error") is not None:
                position["quote_refresh_error"] = None
            return []
        except Exception as exc:  # never manufacture a price
            failures.append(f"{source_name}: {type(exc).__name__}:{str(exc)[:100]}")
    # No metadata-only timestamp refresh: the underlying stale quote is retained.
    return failures


def monitor(state: Mapping[str, Any], *, now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    if now.tzinfo is None:
        raise ValueError("Watchdog requires timezone-aware clock")
    now = now.astimezone(UTC)
    updated = copy.deepcopy(dict(state))
    statuses: list[dict[str, Any]] = []
    checked = recovered = alert = 0
    for market in ("GPW", "US"):
        session = trading_session(market, now)
        positions = ((updated.get("markets") or {}).get(market) or {}).get("open_positions") or []
        for position in positions:
            if not isinstance(position, dict) or str(position.get("status", "OPEN")).upper() != "OPEN":
                continue
            symbol = position_symbol(position)
            if not symbol:
                continue
            item: dict[str, Any] = {"market": market, "symbol": symbol, "session": session}
            if session == "CLOSED":
                item["status"] = "OUT_OF_SESSION"
            elif session == "UNVERIFIED":
                item["status"] = "ALERT_UNVERIFIED_CALENDAR"
                alert += 1
            else:
                checked += 1
                before, age = assess(position.get("current_mark"), market, now)
                item["precheck"] = before
                if before != "FRESH":
                    item["recovery_attempted"] = True
                    failures = recover(position, market, now)
                    after, age = assess(position.get("current_mark"), market, now)
                    if after == "FRESH":
                        item["status"] = "RECOVERED"
                        recovered += 1
                    else:
                        item["status"] = "ALERT_" + after
                        item["diagnostic"] = failures
                        alert += 1
                else:
                    item["status"] = "FRESH"
            current_mark = position.get("current_mark")
            item["observed_at"] = current_mark.get("observed_at") if isinstance(current_mark, Mapping) else None
            item["provider"] = current_mark.get("provider") if isinstance(current_mark, Mapping) else None
            statuses.append(item)

    # 30-minute heartbeat to reduce GitHub commits/Pages builds in steady state.
    heartbeat = now.replace(minute=(now.minute // 30) * 30, second=0, microsecond=0)
    report = {
        "schema_version": "stock-trading-quote-freshness-watchdog-v1",
        "checked_at_bucket": heartbeat.isoformat().replace("+00:00", "Z"),
        "max_age_seconds": MAX_AGE_SECONDS,
        "status": "ALERT" if alert else "OK",
        "active_positions_checked": checked,
        "positions_recovered": recovered,
        "alerts": alert,
        "positions": statuses,
    }
    return updated, report


def publish(now: datetime | None = None, *, portfolio_path: Path = PORTFOLIO_PATH,
            health_path: Path = HEALTH_PATH) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    state = provider._load(portfolio_path)
    updated, report = monitor(state, now=now)
    if updated != state:
        provider._atomic(portfolio_path, updated)
    previous = None
    if health_path.exists():
        try:
            previous = json.loads(health_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            previous = None
    if previous != report:
        provider._atomic(health_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio", type=Path, default=PORTFOLIO_PATH)
    parser.add_argument("--health", type=Path, default=HEALTH_PATH)
    args = parser.parse_args()
    report = publish(portfolio_path=args.portfolio, health_path=args.health)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    # Workflow commits the health report before marking the run failed.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
