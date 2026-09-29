#!/usr/bin/env python3
"""BriefRooms EPE — Execution Price Engine.

EPE is deliberately not a predictive model. It is a deterministic execution
integrity layer placed between a trading decision and creation of a paper
position.

Policies:
- Daily EUR/USD: a new position can open only from a fresh, cross-checked
  live mid-market quote. The analytical 30m model price is never a fill.
- WES: the existing frozen-limit target remains authoritative, but the observed
  target touch must satisfy chronology and OHLC containment before it is
  accepted as a fill.

EPE never invents bid/ask values and never substitutes a stale or historical
analytical price for a current market fill.
"""
from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from belief_market_data_adapter import YahooChartClient

SCHEMA_VERSION = "execution-price-engine-v1"
ENGINE_VERSION = "EPE-1.0.0"
EURUSD_PIP = 0.0001
EURUSD_MIN = 0.8
EURUSD_MAX = 1.5

# Current public infrastructure gives us a direct fxapi mid and an independent
# Yahoo 1m cross-check. These limits are intentionally strict enough to reject
# a several-pip ghost price while tolerating normal timestamp granularity.
DEFAULT_PRIMARY_MAX_AGE_SECONDS = 180.0
DEFAULT_SECONDARY_MAX_AGE_SECONDS = 180.0
DEFAULT_FUTURE_TOLERANCE_SECONDS = 30.0
DEFAULT_MAX_CROSS_FEED_PIPS = 1.5


@dataclass(frozen=True)
class Quote:
    price: float
    timestamp: datetime
    source: str

    def normalized(self) -> "Quote":
        stamp = self.timestamp
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return Quote(float(self.price), stamp.astimezone(timezone.utc), str(self.source))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    stamp = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        stamp = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw.replace(".", "", 1).isdigit():
            try:
                stamp = datetime.fromtimestamp(float(raw), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        else:
            try:
                stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _finite_price(value: Any, *, low: float = 0.0, high: float = math.inf) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= low or number >= high:
        return None
    return number


def _quote_age_seconds(quote: Quote, now: datetime) -> float:
    return (now - quote.timestamp).total_seconds()


def _quote_payload(quote: Quote, now: datetime) -> dict[str, Any]:
    return {
        "price": round(float(quote.price), 8),
        "timestamp": iso_z(quote.timestamp),
        "source": quote.source,
        "age_seconds": round(_quote_age_seconds(quote, now), 3),
    }


def blocked(reason: str, *, mode: str, instrument: str = "EUR/USD", details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "instrument": instrument,
        "mode": mode,
        "status": "NO_FILL",
        "verified": False,
        "reason": reason,
        "details": dict(details or {}),
    }


def verify_live_mid_fill(
    direction: str,
    primary: Quote,
    secondary: Quote,
    *,
    now: Optional[datetime] = None,
    primary_max_age_seconds: float = DEFAULT_PRIMARY_MAX_AGE_SECONDS,
    secondary_max_age_seconds: float = DEFAULT_SECONDARY_MAX_AGE_SECONDS,
    future_tolerance_seconds: float = DEFAULT_FUTURE_TOLERANCE_SECONDS,
    max_cross_feed_pips: float = DEFAULT_MAX_CROSS_FEED_PIPS,
) -> dict[str, Any]:
    """Verify a Daily EUR/USD market-now paper fill.

    The fill is the primary live MID itself. EPE does not synthesize an
    executable bid/ask because the currently configured providers do not expose
    one. The independent secondary MID exists only as an integrity cross-check.
    """
    side = str(direction or "").upper()
    if side not in {"LONG", "SHORT"}:
        return blocked("invalid_direction", mode="MARKET_NOW")

    current = (now or utc_now()).astimezone(timezone.utc)
    p = primary.normalized()
    s = secondary.normalized()
    p_price = _finite_price(p.price, low=EURUSD_MIN, high=EURUSD_MAX)
    s_price = _finite_price(s.price, low=EURUSD_MIN, high=EURUSD_MAX)
    if p_price is None:
        return blocked("primary_quote_invalid", mode="MARKET_NOW")
    if s_price is None:
        return blocked("secondary_quote_invalid", mode="MARKET_NOW")

    p_age = _quote_age_seconds(p, current)
    s_age = _quote_age_seconds(s, current)
    details = {
        "primary_quote": _quote_payload(p, current),
        "secondary_quote": _quote_payload(s, current),
        "primary_max_age_seconds": float(primary_max_age_seconds),
        "secondary_max_age_seconds": float(secondary_max_age_seconds),
        "max_cross_feed_pips": float(max_cross_feed_pips),
    }
    if p_age < -float(future_tolerance_seconds) or p_age > float(primary_max_age_seconds):
        return blocked("primary_quote_stale_or_future", mode="MARKET_NOW", details=details)
    if s_age < -float(future_tolerance_seconds) or s_age > float(secondary_max_age_seconds):
        return blocked("secondary_quote_stale_or_future", mode="MARKET_NOW", details=details)

    difference_pips = abs(p_price - s_price) / EURUSD_PIP
    details["cross_feed_difference_pips"] = round(difference_pips, 3)
    if difference_pips > float(max_cross_feed_pips):
        return blocked("cross_feed_divergence", mode="MARKET_NOW", details=details)

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "instrument": "EUR/USD",
        "mode": "MARKET_NOW",
        "status": "VERIFIED_FILL",
        "verified": True,
        "direction": side,
        "fill_price": round(p_price, 5),
        "price_type": "LIVE_MID_PAPER_FILL",
        "verified_at": iso_z(current),
        "primary_quote": _quote_payload(p, current),
        "secondary_quote": _quote_payload(s, current),
        "cross_feed_difference_pips": round(difference_pips, 3),
        "max_cross_feed_pips": float(max_cross_feed_pips),
        "executable_bid_ask_available": False,
        "paper_trading_only": True,
        "policy": "current_primary_mid_cross_checked_against_independent_1m_market_feed",
    }


def _http_json(url: str, timeout: int = 8) -> Mapping[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BriefRooms-EPE/1.0",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, Mapping):
        raise RuntimeError("quote provider returned non-object JSON")
    return payload


def fetch_fxapi_eurusd_quote(timeout: int = 8) -> Quote:
    payload = _http_json("https://fxapi.app/api/EUR/USD.json", timeout=timeout)
    price = _finite_price(payload.get("rate"), low=EURUSD_MIN, high=EURUSD_MAX)
    stamp = parse_time(payload.get("timestamp"))
    if price is None or stamp is None:
        raise RuntimeError("fxapi EUR/USD quote incomplete")
    return Quote(price=price, timestamp=stamp, source="fxapi.app:EUR/USD:mid")


def fetch_yahoo_eurusd_quote(client: YahooChartClient | None = None) -> Quote:
    market = client or YahooChartClient(timeout=8)
    bars = market.bars("EURUSD=X", "1d", "1m")
    if not bars:
        raise RuntimeError("Yahoo EUR/USD 1m quote unavailable")
    last = bars[-1]
    return Quote(price=float(last.close), timestamp=last.timestamp, source="Yahoo Finance:EURUSD=X:1m:mid-proxy")


def eurusd_market_fill(
    direction: str,
    *,
    now: Optional[datetime] = None,
    primary_fetcher: Callable[[], Quote] = fetch_fxapi_eurusd_quote,
    secondary_fetcher: Callable[[], Quote] = fetch_yahoo_eurusd_quote,
) -> dict[str, Any]:
    current = (now or utc_now()).astimezone(timezone.utc)
    try:
        primary = primary_fetcher()
    except Exception as exc:
        return blocked(
            "primary_quote_unavailable",
            mode="MARKET_NOW",
            details={"error_type": type(exc).__name__},
        )
    try:
        secondary = secondary_fetcher()
    except Exception as exc:
        return blocked(
            "secondary_quote_unavailable",
            mode="MARKET_NOW",
            details={
                "primary_quote": _quote_payload(primary.normalized(), current),
                "error_type": type(exc).__name__,
            },
        )
    return verify_live_mid_fill(direction, primary, secondary, now=current)


def recenter_geometry(
    direction: str,
    analytical_entry: float,
    stop: float,
    target: float,
    fill_price: float,
) -> dict[str, float]:
    """Move SL/TP geometry to the verified fill without changing risk distances."""
    side = str(direction or "").upper()
    entry = float(analytical_entry)
    stop_value = float(stop)
    target_value = float(target)
    fill = float(fill_price)
    if side == "LONG":
        risk_distance = entry - stop_value
        reward_distance = target_value - entry
        if risk_distance <= 0 or reward_distance <= 0:
            raise ValueError("invalid LONG analytical geometry")
        new_stop = fill - risk_distance
        new_target = fill + reward_distance
    elif side == "SHORT":
        risk_distance = stop_value - entry
        reward_distance = entry - target_value
        if risk_distance <= 0 or reward_distance <= 0:
            raise ValueError("invalid SHORT analytical geometry")
        new_stop = fill + risk_distance
        new_target = fill - reward_distance
    else:
        raise ValueError("direction must be LONG or SHORT")
    return {
        "entry": round(fill, 5),
        "stop": round(new_stop, 5),
        "target": round(new_target, 5),
        "risk_distance": round(risk_distance, 8),
        "reward_distance": round(reward_distance, 8),
    }


def verify_frozen_limit_touch(
    point: Mapping[str, Any],
    *,
    direction: str,
    target_price: float,
    entry_not_before: datetime,
    expires_at: datetime,
    checked_at: datetime,
) -> dict[str, Any]:
    """Verify WES frozen-limit execution evidence without changing its target."""
    side = str(direction or "").lower()
    if side not in {"long", "short"}:
        return blocked("invalid_direction", mode="FROZEN_LIMIT_TOUCH", instrument="WES")

    target = _finite_price(target_price)
    point_price = _finite_price(point.get("price"))
    stamp = parse_time(point.get("timestamp"))
    high = _finite_price(point.get("observed_high"))
    low = _finite_price(point.get("observed_low"))
    if target is None or point_price is None or stamp is None or high is None or low is None:
        return blocked("incomplete_touch_evidence", mode="FROZEN_LIMIT_TOUCH", instrument="WES")

    start = entry_not_before.astimezone(timezone.utc)
    expiry = expires_at.astimezone(timezone.utc)
    checked = checked_at.astimezone(timezone.utc)
    if stamp < start:
        return blocked("touch_before_authorization", mode="FROZEN_LIMIT_TOUCH", instrument="WES")
    if stamp > min(expiry, checked):
        return blocked("touch_outside_execution_window", mode="FROZEN_LIMIT_TOUCH", instrument="WES")

    price_error = abs(point_price - target)
    if price_error > 1e-8:
        return blocked(
            "fill_price_not_frozen_target",
            mode="FROZEN_LIMIT_TOUCH",
            instrument="WES",
            details={"point_price": point_price, "target_price": target},
        )

    touched = low <= target if side == "long" else high >= target
    if not touched:
        return blocked(
            "target_not_contained_in_observed_bar",
            mode="FROZEN_LIMIT_TOUCH",
            instrument="WES",
            details={"observed_low": low, "observed_high": high, "target_price": target},
        )

    source = str(point.get("source") or "")
    if not source:
        return blocked("missing_execution_source", mode="FROZEN_LIMIT_TOUCH", instrument="WES")

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "instrument": "WES",
        "mode": "FROZEN_LIMIT_TOUCH",
        "status": "VERIFIED_FILL",
        "verified": True,
        "direction": side.upper(),
        "fill_price": round(target, 8),
        "price_type": "FROZEN_LIMIT_TARGET_TOUCH",
        "verified_at": iso_z(checked),
        "touch_timestamp": iso_z(stamp),
        "source": source,
        "observed_high": float(high),
        "observed_low": float(low),
        "paper_trading_only": True,
        "policy": "frozen_target_must_be_touched_after_authorization_and_before_expiry",
    }
