#!/usr/bin/env python3
"""Enrich the canonical Stock Trading portfolio with session-aware quote metadata.

This module deliberately keeps quote display state separate from the portfolio
risk mark. The portfolio engine owns SL/TP and position lifecycle; this module
adds the latest source observation for the public dashboard without pretending
that a delayed or old quote is live.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data/investments/stock_trading_portfolio.json"
USER_AGENT = "BriefRooms-Stock-Trading-Quotes/1.0"
UTC = ZoneInfo("UTC")
MARKET_TZ = {"GPW": ZoneInfo("Europe/Warsaw"), "US": ZoneInfo("America/New_York")}
VALID_STATES = {"PRE", "REGULAR", "POST", "CLOSED"}

EXECUTION_DEFAULT_MAX_AGE_SECONDS = 300


class ExecutionQuoteUnavailable(RuntimeError):
    """No independently observed quote is fresh enough for a new LIVE entry."""

    def __init__(self, message: str, diagnostics: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []



def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Cannot load {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid JSON object in {path}")
    return payload


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        name = handle.name
    Path(name).replace(path)


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _normalise_state(value: Any) -> str | None:
    raw = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    mapping = {
        "PRE": "PRE",
        "PREPRE": "PRE",
        "PRE_MARKET": "PRE",
        "PREMARKET": "PRE",
        "REGULAR": "REGULAR",
        "OPEN": "REGULAR",
        "REGULAR_MARKET": "REGULAR",
        "POST": "POST",
        "POSTPOST": "POST",
        "POST_MARKET": "POST",
        "POSTMARKET": "POST",
        "AFTER_HOURS": "POST",
        "CLOSED": "CLOSED",
        "CLOSE": "CLOSED",
    }
    return mapping.get(raw)


def _request_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)

def _request_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")



def _chart(symbol: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({
        "range": "1d",
        "interval": "1m",
        "events": "history",
        "includePrePost": "true",
    })
    encoded = urllib.parse.quote(symbol, safe="")
    failures: list[str] = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            payload = _request_json(f"https://{host}/v8/finance/chart/{encoded}?{params}")
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not isinstance(result, Mapping):
                raise ValueError("empty_chart")
            return dict(result)
        except Exception as exc:  # network/provider failure is handled at portfolio level
            failures.append(f"{host}:{type(exc).__name__}")
            time.sleep(0.2)
    raise RuntimeError(f"Yahoo chart unavailable for {symbol}: {'|'.join(failures)}")


def _periods(meta: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
    raw = meta.get("currentTradingPeriod") or {}
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, tuple[int, int]] = {}
    for provider_key, state in (("pre", "PRE"), ("regular", "REGULAR"), ("post", "POST")):
        row = raw.get(provider_key) or {}
        if not isinstance(row, Mapping):
            continue
        try:
            start, end = int(row["start"]), int(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < end:
            out[state] = (start, end)
    return out


def _clock_fallback_state(market: str, now_utc: datetime) -> str:
    local = now_utc.astimezone(MARKET_TZ[market])
    if local.weekday() >= 5:
        return "CLOSED"
    minute = local.hour * 60 + local.minute
    if market == "US":
        if 4 * 60 <= minute < 9 * 60 + 30:
            return "PRE"
        if 9 * 60 + 30 <= minute < 16 * 60:
            return "REGULAR"
        if 16 * 60 <= minute < 20 * 60:
            return "POST"
        return "CLOSED"
    return "REGULAR" if 9 * 60 <= minute < 17 * 60 else "CLOSED"


def _resolve_state(meta: Mapping[str, Any], now_utc: datetime, market: str) -> tuple[str, str]:
    explicit = _normalise_state(meta.get("marketState") or meta.get("market_state"))
    if explicit in VALID_STATES:
        return explicit, "provider.marketState"
    epoch = int(now_utc.timestamp())
    periods = _periods(meta)
    for state in ("PRE", "REGULAR", "POST"):
        bounds = periods.get(state)
        if bounds and bounds[0] <= epoch < bounds[1]:
            return state, "provider.currentTradingPeriod"
    if periods:
        return "CLOSED", "provider.currentTradingPeriod"
    return _clock_fallback_state(market, now_utc), "clock_fallback"


def _point_state(timestamp: int, periods: Mapping[str, tuple[int, int]]) -> str | None:
    for state in ("PRE", "REGULAR", "POST"):
        bounds = periods.get(state)
        if bounds and bounds[0] <= timestamp < bounds[1]:
            return state
    return None


def _observed_iso(timestamp: int | None, market: str) -> str | None:
    if timestamp is None:
        return None
    try:
        return _iso(datetime.fromtimestamp(int(timestamp), MARKET_TZ[market]))
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def _delay_metadata(meta: Mapping[str, Any]) -> tuple[str, int | None, bool]:
    raw = _float(meta.get("exchangeDataDelayedBy"))
    if raw is None or raw < 0:
        return "unverified", None, False
    minutes = int(round(raw))
    if minutes == 0:
        return "realtime", 0, True
    return "delayed", minutes, False


def quote_for_symbol(symbol: str, market: str, *, now_utc: datetime | None = None) -> dict[str, Any]:
    market = market.upper()
    if market not in MARKET_TZ:
        raise ValueError(f"Unsupported market: {market}")
    now_utc = (now_utc or datetime.now(UTC)).astimezone(UTC)
    chart = _chart(symbol)
    meta = chart.get("meta") or {}
    if not isinstance(meta, Mapping):
        meta = {}
    state, state_source = _resolve_state(meta, now_utc, market)
    periods = _periods(meta)
    stamps = chart.get("timestamp") or []
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or [] if isinstance(quote, Mapping) else []

    latest_by_state: dict[str, tuple[int, float]] = {}
    latest_any: tuple[int, float] | None = None
    for index, stamp in enumerate(stamps):
        try:
            ts = int(stamp)
            price = _float(closes[index])
        except (TypeError, ValueError, IndexError):
            continue
        if price is None or price <= 0:
            continue
        latest_any = (ts, price)
        point_state = _point_state(ts, periods)
        if point_state:
            latest_by_state[point_state] = (ts, price)

    selected: tuple[int | None, float | None, str]
    active = latest_by_state.get(state)
    if active:
        selected = (active[0], active[1], state.lower())
    elif state == "PRE":
        price = _float(meta.get("preMarketPrice"))
        selected = (None, price, "pre") if price else (None, _float(meta.get("regularMarketPrice")), "regular_reference")
    elif state == "POST":
        price = _float(meta.get("postMarketPrice"))
        selected = (None, price, "post") if price else (None, _float(meta.get("regularMarketPrice")), "regular_reference")
    elif state == "REGULAR":
        regular = latest_by_state.get("REGULAR") or latest_any
        selected = (regular[0], regular[1], "regular") if regular else (None, _float(meta.get("regularMarketPrice")), "regular")
    else:
        regular = latest_by_state.get("REGULAR") or latest_any
        close_price = _float(meta.get("regularMarketPrice")) or _float(meta.get("chartPreviousClose")) or _float(meta.get("previousClose"))
        selected = (regular[0], regular[1], "close") if regular else (None, close_price, "close_reference")

    observed_ts, price, price_kind = selected
    if price is None or price <= 0:
        raise RuntimeError(f"No usable quote for {symbol}")
    delay_status, delay_minutes, is_realtime = _delay_metadata(meta)
    observed_at = _observed_iso(observed_ts, market)
    capture_age = max(0, int(now_utc.timestamp()) - observed_ts) if observed_ts is not None else None
    return {
        "price": round(float(price), 8),
        "market_state": state,
        "state_source": state_source,
        "price_kind": price_kind,
        "observed_at": observed_at,
        "received_at": _iso(now_utc),
        "provider": "Yahoo Finance chart",
        "delay_status": delay_status,
        "delay_minutes": delay_minutes,
        "is_realtime": bool(is_realtime),
        "source_verified": True,
        "capture_age_seconds": capture_age,
    }



def _stooq_symbol(symbol: str, market: str) -> str:
    raw = str(symbol or "").strip().upper()
    if market == "GPW":
        base = raw[:-3] if raw.endswith(".WA") else raw
        return f"{base.lower()}.pl"
    if market == "US":
        return raw.lower() if raw.endswith(".us") else f"{raw.lower()}.us"
    raise ValueError(f"Unsupported market: {market}")


def _stooq_quote(symbol: str, market: str, *, now_utc: datetime | None = None) -> dict[str, Any]:
    """Independent last-quote fallback.

    Stooq uses bare symbols for GPW and .us for US.  We currently admit this
    fallback only for GPW because its timestamp can be interpreted in the GPW
    local clock without guessing another exchange's timestamp convention.
    """
    market = market.upper()
    if market != "GPW":
        raise RuntimeError("Stooq execution fallback is enabled only for GPW")
    now_utc = (now_utc or datetime.now(UTC)).astimezone(UTC)
    stooq_symbol = _stooq_symbol(symbol, market)
    params = urllib.parse.urlencode({
        "s": stooq_symbol,
        "f": "sd2t2ohlcv",
        "h": "",
        "e": "csv",
    })
    raw = _request_text(f"https://stooq.com/q/l/?{params}")
    rows = list(csv.DictReader(io.StringIO(raw)))
    if not rows:
        raise RuntimeError("Stooq quote returned no rows")
    row = rows[0]
    close = _float(row.get("Close") or row.get("close"))
    date_text = str(row.get("Date") or row.get("date") or "").strip()
    time_text = str(row.get("Time") or row.get("time") or "").strip()
    if close is None or close <= 0 or not date_text or not time_text or "N/D" in {date_text.upper(), time_text.upper()}:
        raise RuntimeError("Stooq quote is incomplete")
    observed_local = datetime.fromisoformat(f"{date_text}T{time_text}").replace(tzinfo=MARKET_TZ[market])
    observed_utc = observed_local.astimezone(UTC)
    age_seconds = max(0, int((now_utc - observed_utc).total_seconds()))
    if observed_utc > now_utc.replace(microsecond=0):
        raise RuntimeError("Stooq quote timestamp is in the future")
    state = _clock_fallback_state(market, now_utc)
    return {
        "price": round(float(close), 8),
        "market_state": state,
        "state_source": "clock_fallback_for_stooq",
        "price_kind": "last",
        "observed_at": _iso(observed_local),
        "received_at": _iso(now_utc),
        "provider": "Stooq current quote",
        "delay_status": "measured_from_observation",
        "delay_minutes": round(age_seconds / 60.0, 2),
        "is_realtime": age_seconds <= 120,
        "source_verified": True,
        "capture_age_seconds": age_seconds,
    }


def execution_quote_candidates(symbol: str, market: str, *, now_utc: datetime | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect executable quote candidates without hiding provider failures."""
    market = market.upper()
    now_utc = (now_utc or datetime.now(UTC)).astimezone(UTC)
    candidates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    providers = [("Yahoo Finance chart", quote_for_symbol)]
    if market == "GPW":
        providers.append(("Stooq current quote", _stooq_quote))

    for provider_name, provider in providers:
        try:
            quote = provider(symbol, market, now_utc=now_utc)
            candidates.append(quote)
            diagnostics.append({
                "provider": quote.get("provider") or provider_name,
                "status": "ok",
                "market_state": quote.get("market_state"),
                "observed_at": quote.get("observed_at"),
                "capture_age_seconds": quote.get("capture_age_seconds"),
                "delay_minutes": quote.get("delay_minutes"),
            })
        except Exception as exc:
            diagnostics.append({
                "provider": provider_name,
                "status": "error",
                "error": f"{type(exc).__name__}:{str(exc)[:180]}",
            })
    return candidates, diagnostics


def execution_quote_for_symbol(
    symbol: str,
    market: str,
    *,
    now_utc: datetime | None = None,
    maximum_age_seconds: int = EXECUTION_DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Return the freshest independently observed quote eligible for a new entry.

    Research/reference prices are never promoted to fills.  A candidate remains
    READY when all available execution quotes are stale, closed-session, or lack
    an observation timestamp.
    """
    market = market.upper()
    now_utc = (now_utc or datetime.now(UTC)).astimezone(UTC)
    candidates, diagnostics = execution_quote_candidates(symbol, market, now_utc=now_utc)
    eligible: list[dict[str, Any]] = []
    for quote in candidates:
        age = _float(quote.get("capture_age_seconds"))
        if quote.get("market_state") != "REGULAR":
            continue
        if age is None or age < 0 or age > float(maximum_age_seconds):
            continue
        delay_minutes = _float(quote.get("delay_minutes"))
        if delay_minutes is not None and delay_minutes * 60.0 > float(maximum_age_seconds):
            continue
        eligible.append(quote)
    if not eligible:
        raise ExecutionQuoteUnavailable(
            f"No execution quote <= {maximum_age_seconds}s for {symbol}",
            diagnostics=diagnostics,
        )
    eligible.sort(key=lambda q: float(q.get("capture_age_seconds") or 0.0))
    chosen = dict(eligible[0])
    chosen["execution_quote_policy"] = {
        "maximum_age_seconds": int(maximum_age_seconds),
        "provider_candidates": diagnostics,
        "selected_provider": chosen.get("provider"),
    }
    return chosen


def enrich(state: Mapping[str, Any], *, markets: list[str] | None = None, now_utc: datetime | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    now_utc = (now_utc or datetime.now(UTC)).astimezone(UTC)
    selected_markets = [m.upper() for m in (markets or ["GPW", "US"])]
    payload = json.loads(json.dumps(state))
    audits: list[dict[str, Any]] = []
    market_rows = payload.setdefault("markets", {})

    for market in selected_markets:
        if market not in MARKET_TZ:
            continue
        row = market_rows.setdefault(market, {})
        positions = row.get("open_positions") or []
        provider_session: dict[str, Any] | None = None
        for position in positions:
            if not isinstance(position, dict) or str(position.get("status") or "OPEN").upper() != "OPEN":
                continue
            symbol = str(position.get("symbol") or position.get("ticker") or "").strip().upper()
            if not symbol:
                continue
            try:
                current_mark = quote_for_symbol(symbol, market, now_utc=now_utc)
                position["current_mark"] = current_mark
                position["quote_refresh_error"] = None
                provider_session = {
                    "market_state": current_mark["market_state"],
                    "state_source": current_mark["state_source"],
                    "as_of": current_mark["observed_at"] or current_mark["received_at"],
                    "received_at": current_mark["received_at"],
                    "provider": current_mark["provider"],
                    "delay_status": current_mark["delay_status"],
                    "delay_minutes": current_mark["delay_minutes"],
                    "is_realtime": current_mark["is_realtime"],
                }
                audits.append({"market": market, "symbol": symbol, "action": "quote_enriched", "market_state": current_mark["market_state"], "price_kind": current_mark["price_kind"]})
            except Exception as exc:
                position["quote_refresh_error"] = {
                    "at": _iso(now_utc),
                    "error": f"{type(exc).__name__}:{str(exc)[:180]}",
                }
                audits.append({"market": market, "symbol": symbol, "action": "quote_error", "error": f"{type(exc).__name__}:{str(exc)[:120]}"})

        row["quote_session"] = provider_session or {
            "market_state": _clock_fallback_state(market, now_utc),
            "state_source": "clock_fallback",
            "as_of": _iso(now_utc),
            "received_at": _iso(now_utc),
            "provider": None,
            "delay_status": "unverified",
            "delay_minutes": None,
            "is_realtime": False,
        }
    payload["quote_enriched_at"] = _iso(now_utc)
    return payload, audits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["all", "GPW", "US"], default="all")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    state = _load(STATE_PATH)
    markets = ["GPW", "US"] if args.market == "all" else [args.market]
    enriched, audits = enrich(state, markets=markets)
    if args.verify:
        for market in markets:
            session = ((enriched.get("markets") or {}).get(market) or {}).get("quote_session") or {}
            if _normalise_state(session.get("market_state")) not in VALID_STATES:
                raise SystemExit(f"Invalid quote session for {market}")
    _atomic(STATE_PATH, enriched)
    print(json.dumps({"status": "OK", "audits": audits}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
