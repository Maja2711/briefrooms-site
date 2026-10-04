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

EPE never substitutes a stale or historical analytical price for a current market fill.\nFor Daily EUR/USD paper execution, EPE explicitly models a fixed 1.5-pip spread\naround the verified market MID; these synthetic BID/ASK prices are always labeled\nas modeled, never as broker-verified executable quotes.
"""
from __future__ import annotations

import csv
import io
import json
import math
import statistics
from decimal import Decimal, ROUND_HALF_UP
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Callable, Mapping, Optional

from belief_market_data_adapter import YahooChartClient

SCHEMA_VERSION = "execution-price-engine-v1"
ENGINE_VERSION = "EPE-1.3.0"
EURUSD_PIP = 0.0001
EURUSD_MIN = 0.8
EURUSD_MAX = 1.5
SYNTHETIC_SPREAD_PIPS = 1.5
SYNTHETIC_HALF_SPREAD_PIPS = SYNTHETIC_SPREAD_PIPS / 2.0
SYNTHETIC_HALF_SPREAD_PRICE = SYNTHETIC_HALF_SPREAD_PIPS * EURUSD_PIP

# Current public infrastructure prioritizes Stooq live EUR/USD, with fxapi,
# Currency Exchange Tool and Yahoo as independent fallbacks/cross-checks. These limits are intentionally strict enough to reject
# a several-pip ghost price while tolerating normal timestamp granularity.
DEFAULT_PRIMARY_MAX_AGE_SECONDS = 180.0
DEFAULT_SECONDARY_MAX_AGE_SECONDS = 180.0
DEFAULT_FUTURE_TOLERANCE_SECONDS = 30.0
DEFAULT_MAX_CROSS_FEED_PIPS = 1.5
DEFAULT_QUOTE_MAX_AGE_SECONDS = 180.0

SOURCE_PRIORITY = (
    "Stooq:",
    "fxapi.app:",
    "Currency Exchange Tool:",
    "Yahoo Finance:",
)


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


FX_PRICE_QUANTUM = Decimal("0.00001")
SYNTHETIC_HALF_SPREAD_DECIMAL = Decimal("0.000075")


def fx_price_5(value: Any) -> float:
    return float(Decimal(str(value)).quantize(FX_PRICE_QUANTUM, rounding=ROUND_HALF_UP))


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


def synthetic_bid_ask(mid_price: float) -> dict[str, float]:
    mid_decimal = Decimal(str(mid_price))
    return {
        "mid": round(float(mid_decimal), 8),
        "bid": float((mid_decimal - SYNTHETIC_HALF_SPREAD_DECIMAL).quantize(FX_PRICE_QUANTUM, rounding=ROUND_HALF_UP)),
        "ask": float((mid_decimal + SYNTHETIC_HALF_SPREAD_DECIMAL).quantize(FX_PRICE_QUANTUM, rounding=ROUND_HALF_UP)),
        "spread_pips": SYNTHETIC_SPREAD_PIPS,
        "half_spread_pips": SYNTHETIC_HALF_SPREAD_PIPS,
    }


def synthetic_entry_price(direction: str, mid_price: float) -> tuple[float, str, dict[str, float]]:
    side = str(direction or "").upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    levels = synthetic_bid_ask(mid_price)
    if side == "LONG":
        return float(levels["ask"]), "ASK", levels
    return float(levels["bid"]), "BID", levels


def synthetic_exit_price(direction: str, mid_price: float) -> tuple[float, str, dict[str, float]]:
    side = str(direction or "").upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    levels = synthetic_bid_ask(mid_price)
    if side == "LONG":
        return float(levels["bid"]), "BID", levels
    return float(levels["ask"]), "ASK", levels


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

    fill_price, fill_side, levels = synthetic_entry_price(side, p_price)
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "instrument": "EUR/USD",
        "mode": "MARKET_NOW",
        "status": "VERIFIED_FILL",
        "verified": True,
        "direction": side,
        "selected_mid_price": round(p_price, 8),
        "synthetic_bid": levels["bid"],
        "synthetic_ask": levels["ask"],
        "synthetic_spread_pips": SYNTHETIC_SPREAD_PIPS,
        "synthetic_half_spread_pips": SYNTHETIC_HALF_SPREAD_PIPS,
        "fill_price": round(fill_price, 5),
        "fill_side": fill_side,
        "price_type": "MID_VERIFIED_SYNTHETIC_SPREAD",
        "verified_at": iso_z(current),
        "primary_quote": _quote_payload(p, current),
        "secondary_quote": _quote_payload(s, current),
        "cross_feed_difference_pips": round(difference_pips, 3),
        "max_cross_feed_pips": float(max_cross_feed_pips),
        "executable_bid_ask_available": False,
        "paper_trading_only": True,
        "policy": "verified_mid_plus_fixed_1_5_pip_synthetic_spread",
    }


def _source_rank(source: str) -> int:
    text = str(source or "")
    for index, prefix in enumerate(SOURCE_PRIORITY):
        if text.startswith(prefix):
            return index
    return len(SOURCE_PRIORITY)


def _source_identity(source: str) -> str:
    text = str(source or "").strip()
    for prefix in SOURCE_PRIORITY:
        if text.startswith(prefix):
            return prefix.rstrip(":").casefold()
    return (text.split(":", 1)[0] or text).strip().casefold()


def _fresh_quote_candidates(
    quotes: list[Quote],
    *,
    now: datetime,
    max_age_seconds: float = DEFAULT_QUOTE_MAX_AGE_SECONDS,
    future_tolerance_seconds: float = DEFAULT_FUTURE_TOLERANCE_SECONDS,
) -> tuple[list[Quote], list[dict[str, Any]]]:
    valid_by_source: dict[str, Quote] = {}
    rejected: list[dict[str, Any]] = []
    for raw in quotes:
        try:
            quote = raw.normalized()
        except Exception:
            rejected.append({"reason": "normalization_failed"})
            continue
        price = _finite_price(quote.price, low=EURUSD_MIN, high=EURUSD_MAX)
        age = _quote_age_seconds(quote, now)
        if price is None:
            rejected.append({"source": quote.source, "reason": "invalid_price"})
            continue
        if age < -float(future_tolerance_seconds) or age > float(max_age_seconds):
            rejected.append({
                "source": quote.source,
                "reason": "stale_or_future",
                "age_seconds": round(age, 3),
            })
            continue
        normalized = Quote(price=price, timestamp=quote.timestamp, source=quote.source)
        identity = _source_identity(normalized.source)
        prior = valid_by_source.get(identity)
        if prior is None:
            valid_by_source[identity] = normalized
            continue
        # Multiple records from one provider are one independent source.
        # Keep the freshest observation and explicitly audit the duplicate.
        if normalized.timestamp > prior.timestamp:
            rejected.append({
                "source": prior.source,
                "reason": "duplicate_provider_quote",
                "provider_identity": identity,
            })
            valid_by_source[identity] = normalized
        else:
            rejected.append({
                "source": normalized.source,
                "reason": "duplicate_provider_quote",
                "provider_identity": identity,
            })
    valid = list(valid_by_source.values())
    return valid, rejected


def verify_live_mid_quotes(
    direction: str,
    quotes: list[Quote],
    *,
    now: Optional[datetime] = None,
    max_age_seconds: float = DEFAULT_QUOTE_MAX_AGE_SECONDS,
    max_cross_feed_pips: float = DEFAULT_MAX_CROSS_FEED_PIPS,
) -> dict[str, Any]:
    """Fail-closed Daily EUR/USD execution verification.

    A new paper fill requires at least two independent fresh quotes that agree
    within the configured cross-feed tolerance. One source is insufficient for
    a verified fill, and contradictory fresh sources block the entry.
    """
    side = str(direction or "").upper()
    if side not in {"LONG", "SHORT"}:
        return blocked("invalid_direction", mode="MARKET_NOW")

    current = (now or utc_now()).astimezone(timezone.utc)
    valid, rejected = _fresh_quote_candidates(
        list(quotes or []),
        now=current,
        max_age_seconds=max_age_seconds,
    )
    if not valid:
        return blocked(
            "no_fresh_eurusd_quote",
            mode="MARKET_NOW",
            details={
                "max_age_seconds": float(max_age_seconds),
                "rejected_quotes": rejected,
            },
        )
    if len(valid) < 2:
        return blocked(
            "insufficient_independent_quotes",
            mode="MARKET_NOW",
            details={
                "fresh_quotes": [_quote_payload(q, current) for q in valid],
                "rejected_quotes": rejected,
                "fresh_source_count": len(valid),
                "required_fresh_sources": 2,
                "max_age_seconds": float(max_age_seconds),
            },
        )

    prices = [float(q.price) for q in valid]
    median_price = statistics.median(prices)
    max_difference_pips = (
        (max(prices) - min(prices)) / EURUSD_PIP
        if len(prices) >= 2
        else 0.0
    )

    if len(valid) >= 3:
        inliers = [
            q for q in valid
            if abs(float(q.price) - median_price) / EURUSD_PIP <= float(max_cross_feed_pips)
        ]
        if len(inliers) < 2:
            return blocked(
                "cross_feed_divergence",
                mode="MARKET_NOW",
                details={
                    "fresh_quotes": [_quote_payload(q, current) for q in valid],
                    "rejected_quotes": rejected,
                    "fresh_source_count": len(valid),
                    "cross_feed_range_pips": round(float(max_difference_pips), 3),
                    "max_cross_feed_pips": float(max_cross_feed_pips),
                },
            )
        pool = inliers
        quality = "CONSENSUS"
    else:
        if max_difference_pips > float(max_cross_feed_pips):
            return blocked(
                "cross_feed_divergence",
                mode="MARKET_NOW",
                details={
                    "fresh_quotes": [_quote_payload(q, current) for q in valid],
                    "rejected_quotes": rejected,
                    "fresh_source_count": len(valid),
                    "cross_feed_range_pips": round(float(max_difference_pips), 3),
                    "max_cross_feed_pips": float(max_cross_feed_pips),
                },
            )
        pool = valid
        quality = "CONSENSUS"

    # Choose a real observed quote, never an invented average. Source priority
    # breaks ties; freshness is the secondary criterion.
    selected = sorted(
        pool,
        key=lambda q: (_source_rank(q.source), _quote_age_seconds(q, current)),
    )[0]

    selected_mid = float(selected.price)
    fill_price, fill_side, levels = synthetic_entry_price(side, selected_mid)
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "instrument": "EUR/USD",
        "mode": "MARKET_NOW",
        "status": "VERIFIED_FILL",
        "verified": True,
        "direction": side,
        "selected_mid_price": round(selected_mid, 8),
        "synthetic_bid": levels["bid"],
        "synthetic_ask": levels["ask"],
        "synthetic_spread_pips": SYNTHETIC_SPREAD_PIPS,
        "synthetic_half_spread_pips": SYNTHETIC_HALF_SPREAD_PIPS,
        "fill_price": round(fill_price, 5),
        "fill_side": fill_side,
        "price_type": "MID_VERIFIED_SYNTHETIC_SPREAD",
        "verified_at": iso_z(current),
        "verification_quality": quality,
        "selected_quote": _quote_payload(selected, current),
        "fresh_quotes": [_quote_payload(q, current) for q in valid],
        "rejected_quotes": rejected,
        "fresh_source_count": len(valid),
        "median_price": round(float(median_price), 8),
        "cross_feed_range_pips": round(float(max_difference_pips), 3),
        "max_cross_feed_pips": float(max_cross_feed_pips),
        "executable_bid_ask_available": False,
        "paper_trading_only": True,
        "policy": "two_source_consensus_verified_mid_plus_fixed_1_5_pip_synthetic_spread",
    }


def _http_text(url: str, timeout: int = 8) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BriefRooms-EPE/1.0",
            "Accept": "text/csv,text/plain,*/*",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_stooq_eurusd_quote(timeout: int = 8) -> Quote:
    """Fetch current Stooq EUR/USD, preferring Bid/Ask-capable CSV."""
    urls = (
        "https://stooq.com/q/l/?s=eurusd&f=sd2t2ohlcvba&h&e=csv",
        "https://stooq.com/q/l/?s=eurusd&f=sd2t2ohlcv&h&e=csv",
        "https://stooq.pl/q/l/?s=eurusd&f=sd2t2ohlcvba&h&e=csv",
        "https://stooq.pl/q/l/?s=eurusd&f=sd2t2ohlcv&h&e=csv",
    )
    errors: list[str] = []
    for url in urls:
        try:
            text = _http_text(url, timeout=timeout)
            rows = list(csv.DictReader(io.StringIO(text)))
            if not rows:
                raise RuntimeError("empty")
            row = rows[0]
            normalized = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
            if any(v.upper() in {"N/D", "N/A"} for v in normalized.values()):
                raise RuntimeError("unavailable")

            bid = _finite_price(normalized.get("bid"), low=EURUSD_MIN, high=EURUSD_MAX)
            ask = _finite_price(normalized.get("ask"), low=EURUSD_MIN, high=EURUSD_MAX)
            last = _finite_price(
                normalized.get("close") or normalized.get("last") or normalized.get("kurs"),
                low=EURUSD_MIN,
                high=EURUSD_MAX,
            )
            if bid is not None and ask is not None and ask >= bid:
                price = (bid + ask) / 2.0
                source = "Stooq:EURUSD:bid-ask-mid"
            elif last is not None:
                price = last
                source = "Stooq:EURUSD:live"
            else:
                raise RuntimeError("no_valid_price")

            date_text = normalized.get("date") or normalized.get("data")
            time_text = normalized.get("time") or normalized.get("czas")
            if not date_text or not time_text:
                raise RuntimeError("missing_timestamp")
            parsed = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d %H%M%S", "%Y%m%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(f"{date_text} {time_text}", fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise RuntimeError("invalid_timestamp")
            stamp = parsed.replace(tzinfo=ZoneInfo("Europe/Warsaw")).astimezone(timezone.utc)
            return Quote(price=price, timestamp=stamp, source=source)
        except Exception as exc:
            errors.append(f"{url}:{type(exc).__name__}")
    raise RuntimeError(f"Stooq EUR/USD quote unavailable: {'|'.join(errors)}")


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


def fetch_currency_exchange_tool_eurusd_quote(timeout: int = 8) -> Quote:
    url = "https://www.currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD"
    payload = _http_json(url, timeout=timeout)
    if payload.get("success") is False:
        raise RuntimeError("Currency Exchange Tool EUR/USD API error")
    price = _finite_price(
        payload.get("rate") if payload.get("rate") is not None else payload.get("result"),
        low=EURUSD_MIN,
        high=EURUSD_MAX,
    )
    stamp = parse_time(
        payload.get("updatedAt")
        or payload.get("updated_at")
        or payload.get("timestamp")
        or payload.get("time")
    )
    if price is None or stamp is None:
        raise RuntimeError("Currency Exchange Tool EUR/USD quote incomplete")
    return Quote(price=price, timestamp=stamp, source="Currency Exchange Tool:EUR/USD:mid")


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
    fetchers: Optional[list[Callable[[], Quote]]] = None,
) -> dict[str, Any]:
    current = (now or utc_now()).astimezone(timezone.utc)
    providers = fetchers or [
        fetch_stooq_eurusd_quote,
        fetch_fxapi_eurusd_quote,
        fetch_currency_exchange_tool_eurusd_quote,
        fetch_yahoo_eurusd_quote,
    ]
    quotes: list[Quote] = []
    provider_errors: list[dict[str, str]] = []
    for provider in providers:
        try:
            quotes.append(provider())
        except Exception as exc:
            provider_errors.append({
                "provider": getattr(provider, "__name__", "quote_provider"),
                "error_type": type(exc).__name__,
            })

    result = verify_live_mid_quotes(direction, quotes, now=current)
    if provider_errors:
        result = dict(result)
        result["provider_errors"] = provider_errors
    return result


def recenter_geometry(
    direction: str,
    analytical_entry: float,
    stop: float,
    target: float,
    fill_price: float,
    *,
    market_mid: Optional[float] = None,
) -> dict[str, float]:
    """Recenter model SL/TP around the verified MID, not around BID/ASK.

    This keeps the model's market-distance thesis intact while the synthetic
    spread remains an explicit execution cost. Therefore actual risk/reward
    measured from the fill changes by the half-spread, as it should.
    """
    side = str(direction or "").upper()
    analytical = float(analytical_entry)
    stop_value = float(stop)
    target_value = float(target)
    fill = float(fill_price)
    center = float(market_mid if market_mid is not None else fill)

    if side == "LONG":
        model_risk = analytical - stop_value
        model_reward = target_value - analytical
        if model_risk <= 0 or model_reward <= 0:
            raise ValueError("invalid LONG analytical geometry")
        new_stop = center - model_risk
        new_target = center + model_reward
        actual_risk = fill - new_stop
        actual_reward = new_target - fill
    elif side == "SHORT":
        model_risk = stop_value - analytical
        model_reward = analytical - target_value
        if model_risk <= 0 or model_reward <= 0:
            raise ValueError("invalid SHORT analytical geometry")
        new_stop = center + model_risk
        new_target = center - model_reward
        actual_risk = new_stop - fill
        actual_reward = fill - new_target
    else:
        raise ValueError("direction must be LONG or SHORT")

    if actual_risk <= 0 or actual_reward <= 0:
        raise ValueError("synthetic spread produced invalid execution geometry")

    return {
        "entry": fx_price_5(fill),
        "market_mid": round(center, 8),
        "stop": fx_price_5(new_stop),
        "target": fx_price_5(new_target),
        "risk_distance": round(actual_risk, 8),
        "reward_distance": round(actual_reward, 8),
        "model_mid_risk_distance": round(model_risk, 8),
        "model_mid_reward_distance": round(model_reward, 8),
    }


def verify_market_bar_fill(
    point: Mapping[str, Any],
    *,
    direction: str,
    entry_not_before: datetime,
    expires_at: datetime,
    checked_at: datetime,
    max_age_seconds: float = 15 * 60,
) -> dict[str, Any]:
    """Verify a WES MARKET entry from a fresh completed 5m bar."""
    side = str(direction or "").lower()
    if side not in {"long", "short"}:
        return blocked("invalid_direction", mode="MARKET_NOW", instrument="WES")

    point_price = _finite_price(point.get("price"))
    stamp = parse_time(point.get("timestamp"))
    high = _finite_price(point.get("observed_high"))
    low = _finite_price(point.get("observed_low"))
    source = str(point.get("source") or "")
    if point_price is None or stamp is None or high is None or low is None or not source:
        return blocked("incomplete_market_bar_evidence", mode="MARKET_NOW", instrument="WES")

    start = entry_not_before.astimezone(timezone.utc)
    expiry = expires_at.astimezone(timezone.utc)
    checked = checked_at.astimezone(timezone.utc)
    if stamp < start:
        return blocked("market_bar_before_authorization", mode="MARKET_NOW", instrument="WES")
    if stamp > min(expiry, checked):
        return blocked("market_bar_outside_execution_window", mode="MARKET_NOW", instrument="WES")
    age_seconds = (checked - stamp).total_seconds()
    if age_seconds < -DEFAULT_FUTURE_TOLERANCE_SECONDS or age_seconds > float(max_age_seconds):
        return blocked(
            "market_bar_stale_or_future",
            mode="MARKET_NOW",
            instrument="WES",
            details={"age_seconds": round(age_seconds, 3), "max_age_seconds": float(max_age_seconds)},
        )
    if not (low <= point_price <= high):
        return blocked(
            "market_fill_not_contained_in_observed_bar",
            mode="MARKET_NOW",
            instrument="WES",
            details={"observed_low": low, "observed_high": high, "fill_price": point_price},
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "instrument": "WES",
        "mode": "MARKET_NOW",
        "status": "VERIFIED_FILL",
        "verified": True,
        "direction": side.upper(),
        "fill_price": round(point_price, 8),
        "price_type": "FRESH_COMPLETED_5M_CLOSE",
        "verified_at": iso_z(checked),
        "market_bar_timestamp": iso_z(stamp),
        "age_seconds": round(age_seconds, 3),
        "source": source,
        "observed_high": float(high),
        "observed_low": float(low),
        "paper_trading_only": True,
        "policy": "strong_trend_market_entry_requires_fresh_post_authorization_completed_5m_bar",
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
