#!/usr/bin/env python3
"""Stock Trading quote freshness watchdog (display data only).

Monitors *every currently OPEN position* plus currently eligible, unpurchased
Stock Trading v2 opportunity candidates on GPW and NYSE. During regular
trading, independently checks the source-observation time (not file updated_at
or provider received_at). Candidate quote evidence stays in read-only
watchdog diagnostics; it never becomes execution authority or a fabricated fill.

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
FRONTIER_ROOT = Path("/tmp/stock-trading-v2-quote-frontiers")
# Same prospect validity horizon as Stock v2 production (config: 360 min).
MAX_FRONTIER_AGE = timedelta(minutes=360)
MAX_CANDIDATES_PER_MARKET = 12
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


def candidate_symbol(candidate: Mapping[str, Any], market: str) -> str:
    symbol = str(candidate.get("market_data_symbol") or candidate.get("symbol") or "").strip().upper()
    if not symbol or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for char in symbol):
        return ""
    if market == "GPW":
        return symbol if symbol.endswith(".WA") else symbol + ".WA"
    return symbol if not symbol.endswith(".WA") else ""


def discover_candidates(frontier: Any, market: str, now: datetime) -> tuple[list[str], str]:
    """Derive watch-only universe dynamically from the *current* v2 frontier."""
    if not isinstance(frontier, Mapping):
        return [], "FRONTIER_UNAVAILABLE"
    if frontier.get("market") != market or frontier.get("schema_version") != "stock-trading-v2-portfolio-opportunity-v1":
        return [], "FRONTIER_INVALID"
    generated = parse_dt(frontier.get("generated_at"))
    if generated is None or generated > now + FUTURE_TOLERANCE:
        return [], "FRONTIER_TIMESTAMP_INVALID"
    if now - generated > MAX_FRONTIER_AGE:
        return [], "FRONTIER_STALE"
    candidates = frontier.get("evaluated_candidates")
    if not isinstance(candidates, list):
        return [], "FRONTIER_INVALID"
    candidate = (frontier.get("decision") or {}).get("candidate")
    # Include the active selection even when not present in evaluated_candidates;
    # only native eligible research candidates are allowed into this watchlist.
    rows = ([candidate] if isinstance(candidate, Mapping) else []) + candidates
    symbols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or row.get("eligible") is not True:
            continue
        symbol = candidate_symbol(row, market)
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
        if len(symbols) >= MAX_CANDIDATES_PER_MARKET:
            break
    return symbols, "OK"


def receipt(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Store only observation metadata; quote is never a paper execution fill."""
    return {
        "price": candidate["price"],
        "observed_at": candidate["observed_at"],
        "received_at": candidate.get("received_at"),
        "market_state": candidate["market_state"],
        "provider": candidate.get("provider"),
        "delay_status": candidate.get("delay_status"),
        "is_realtime": candidate.get("is_realtime") is True,
    }


def previous_receipts(health: Any) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(health, Mapping):
        return result
    for row in health.get("candidates") or []:
        if not isinstance(row, Mapping):
            continue
        market, symbol = row.get("market"), row.get("symbol")
        quote = row.get("quote")
        if market in MARKET_ZONE and isinstance(symbol, str) and isinstance(quote, Mapping):
            if candidate_symbol({"market_data_symbol": symbol}, market) == symbol:
                result[(market, symbol)] = dict(quote)
    return result


def monitor_candidates(
    state: Mapping[str, Any], frontiers: Mapping[str, Any],
    previous_health: Mapping[str, Any] | None, *, now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, str], int, int, int]:
    """Independent research telemetry. Does not mutate portfolio or opportunities."""
    old = previous_receipts(previous_health)
    statuses: list[dict[str, Any]] = []
    frontier_health: dict[str, str] = {}
    checked = recovered = alerts = 0
    for market in ("GPW", "US"):
        session = trading_session(market, now)
        symbols, frontier_status = discover_candidates(frontiers.get(market), market, now)
        frontier_health[market] = frontier_status
        if session == "ACTIVE" and frontier_status != "OK":
            alerts += 1
        held = {
            position_symbol(pos) for pos in
            (((state.get("markets") or {}).get(market) or {}).get("open_positions") or [])
            if isinstance(pos, Mapping) and str(pos.get("status") or "OPEN").upper() == "OPEN"
        }
        for symbol in symbols:
            if symbol in held:
                continue  # monitored by the canonical open-position watchdog
            item: dict[str, Any] = {
                "market": market, "symbol": symbol, "role": "UNPURCHASED_CANDIDATE",
                "session": session, "authority": "MONITOR_ONLY",
                "frontier_generated_at": frontiers[market].get("generated_at"),
            }
            prior_quote = old.get((market, symbol))
            if session != "ACTIVE":
                item["status"] = "OUT_OF_SESSION" if session == "CLOSED" else "ALERT_UNVERIFIED_CALENDAR"
                if session == "UNVERIFIED":
                    alerts += 1
                if prior_quote:
                    item["quote"] = prior_quote
                statuses.append(item)
                continue
            checked += 1
            baseline, age = assess(prior_quote, market, now)
            item["precheck"] = baseline
            item["recovery_attempted"] = True
            failures: list[str] = []
            sources = [("Yahoo Finance chart", provider.quote_for_symbol)]
            if market == "GPW":
                sources.append(("Stooq.pl current quote", provider._stooq_quote))
            new_quote = None
            for name, fetch in sources:
                try:
                    sample = fetch(symbol, market, now_utc=now)
                    outcome, _ = assess(sample, market, now)
                    stamp = parse_dt(sample.get("observed_at"))
                    earlier = parse_dt(prior_quote.get("observed_at")) if prior_quote else None
                    if outcome != "FRESH" or stamp is None:
                        failures.append(f"{name}: {outcome}")
                        continue
                    if earlier is not None and stamp < earlier:
                        failures.append(f"{name}: observation_regression")
                        continue
                    new_quote = receipt(sample)
                    break
                except Exception as exc:
                    failures.append(f"{name}: {type(exc).__name__}:{str(exc)[:100]}")
            last = new_quote or prior_quote
            after, age = assess(last, market, now)
            if last:
                item["quote"] = last
            if after == "FRESH":
                item["status"] = "OBSERVED" if new_quote else "FRESH_CACHED"
                if new_quote:
                    recovered += 1
            else:
                item["status"] = "ALERT_" + after
                item["diagnostic"] = failures
                alerts += 1
            item["age_seconds"] = age
            statuses.append(item)
    return statuses, frontier_health, checked, recovered, alerts


def monitor(state: Mapping[str, Any], *, now: datetime, frontiers: Mapping[str, Any] | None = None,
            previous_health: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
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

    candidate_rows: list[dict[str, Any]] = []
    frontier_health: dict[str, str] = {}
    candidate_checked = candidate_recovered = candidate_alerts = 0
    if frontiers is not None:
        candidate_rows, frontier_health, candidate_checked, candidate_recovered, candidate_alerts = monitor_candidates(
            state, frontiers, previous_health, now=now,
        )
        alert += candidate_alerts

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
        "candidate_monitoring_enabled": frontiers is not None,
        "frontiers": frontier_health,
        "candidates_checked": candidate_checked,
        "candidate_quotes_observed": candidate_recovered,
        "candidate_alerts": candidate_alerts,
        "candidates": candidate_rows,
        "candidate_quote_authority": "READ_ONLY_MONITORING_NOT_EXECUTION",
    }
    return updated, report


def publish(now: datetime | None = None, *, portfolio_path: Path = PORTFOLIO_PATH,
            health_path: Path = HEALTH_PATH, frontier_root: Path | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    state = provider._load(portfolio_path)
    previous: dict[str, Any] | None = None
    if health_path.exists():
        try:
            previous = json.loads(health_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            previous = None
    frontiers = None
    if frontier_root is not None:
        frontiers = {}
        for market in ("GPW", "US"):
            path = frontier_root / (market.lower() + ".json")
            if path.is_file():
                try:
                    frontiers[market] = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    frontiers[market] = None
    updated, report = monitor(state, now=now, frontiers=frontiers, previous_health=previous)
    if updated != state:
        provider._atomic(portfolio_path, updated)
    if previous != report:
        provider._atomic(health_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio", type=Path, default=PORTFOLIO_PATH)
    parser.add_argument("--health", type=Path, default=HEALTH_PATH)
    parser.add_argument("--frontier-root", type=Path, default=None,
                        help="Directory with current v2 GPW/US opportunity JSON read from stock-trading-v2 branch")
    args = parser.parse_args()
    report = publish(portfolio_path=args.portfolio, health_path=args.health,
                     frontier_root=args.frontier_root)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    # Workflow commits the health report before marking the run failed.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
