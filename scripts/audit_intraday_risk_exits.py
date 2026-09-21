#!/usr/bin/env python3
"""Canonical fast SL/TP monitor for BriefRooms Weekly/WES paper positions.

The monitor is intentionally independent of the strategy/entry lifecycle:
- it only reads already-open positions with an already-frozen risk plan;
- it never creates entries, changes direction, or moves SL/TP;
- BTC/USD uses Coinbase Exchange candles/ticker as primary execution evidence
  and Yahoo 5-minute bars only as a fallback;
- EUR/USD and S&P 500 futures use Yahoo 5-minute bars;
- delayed scheduler runs are safe because every run replays the full frozen-risk
  interval from risk activation, so a transient threshold touch is not lost;
- if a bar contains both SL and TP, the frozen conservative rule executes SL first;
- missing data never means "no hit": the position stays open and the next run retries.
"""
from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
AUDIT_PATH = ROOT / "data" / "investments" / "intraday_risk_audit.json"
WARSAW = ZoneInfo("Europe/Warsaw")
UTC = timezone.utc
BAR = timedelta(minutes=5)
REQUEST_TIMEOUT = 8
REQUEST_ATTEMPTS = 3
COINBASE_CHUNK = timedelta(hours=20)


@dataclass(frozen=True)
class PriceBar:
    ts: datetime
    high: float
    low: float
    source: str


@dataclass(frozen=True)
class PricePoint:
    ts: datetime
    price: float
    source: str


def sf(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WARSAW)
    return parsed.astimezone(UTC)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def current_week_path() -> Optional[Path]:
    files = sorted(WEEKLY_DIR.glob("*.json"), reverse=True)
    return files[0] if files else None


def request_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(REQUEST_ATTEMPTS):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 BriefRoomsWeeklyRisk/2.0",
                    "Accept": "application/json,*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last = exc
            if attempt + 1 < REQUEST_ATTEMPTS:
                time.sleep(0.35 * (attempt + 1))
    raise RuntimeError(f"market_data_request_failed: {last}")


def _valid_bar(ts: datetime, high: Any, low: Any, source: str) -> Optional[PriceBar]:
    hi, lo = sf(high), sf(low)
    if hi is None or lo is None or hi <= 0 or lo <= 0 or hi < lo:
        return None
    return PriceBar(ts=ts.astimezone(UTC), high=hi, low=lo, source=source)


def fetch_yahoo_bars(symbol: str, start: datetime, end: datetime) -> list[PriceBar]:
    period1 = int((start - BAR).timestamp())
    period2 = int((end + BAR).timestamp())
    encoded = urllib.parse.quote(symbol, safe="")
    query = urllib.parse.urlencode(
        {
            "period1": period1,
            "period2": period2,
            "interval": "5m",
            "includePrePost": "true",
            "events": "div,splits",
        }
    )
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?{query}"
    payload = request_json(url)
    chart = ((payload or {}).get("chart", {}).get("result") or [None])[0]
    if not isinstance(chart, dict):
        raise RuntimeError(f"Yahoo {symbol}: empty chart")
    stamps = chart.get("timestamp") or []
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    source = f"Yahoo Finance:{symbol}:chart:5m"
    bars: list[PriceBar] = []
    for raw_ts, raw_high, raw_low in zip(stamps, highs, lows):
        try:
            ts = datetime.fromtimestamp(int(raw_ts), tz=UTC)
        except (TypeError, ValueError, OSError):
            continue
        bar = _valid_bar(ts, raw_high, raw_low, source)
        if bar and start - BAR <= bar.ts <= end + BAR:
            bars.append(bar)
    bars.sort(key=lambda row: row.ts)
    return bars


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_coinbase_bars(start: datetime, end: datetime) -> list[PriceBar]:
    cursor = start - BAR
    finish = end + BAR
    by_ts: dict[int, PriceBar] = {}
    while cursor < finish:
        chunk_end = min(cursor + COINBASE_CHUNK, finish)
        query = urllib.parse.urlencode(
            {
                "granularity": 300,
                "start": _iso_utc(cursor),
                "end": _iso_utc(chunk_end),
            }
        )
        url = f"https://api.exchange.coinbase.com/products/BTC-USD/candles?{query}"
        payload = request_json(url)
        if not isinstance(payload, list):
            raise RuntimeError("Coinbase BTC-USD candles: invalid payload")
        for row in payload:
            if not isinstance(row, list) or len(row) < 3:
                continue
            try:
                ts = datetime.fromtimestamp(int(row[0]), tz=UTC)
            except (TypeError, ValueError, OSError):
                continue
            bar = _valid_bar(ts, row[2], row[1], "Coinbase Exchange:BTC-USD:candles:5m")
            if bar and start - BAR <= bar.ts <= end + BAR:
                by_ts[int(bar.ts.timestamp())] = bar
        cursor = chunk_end
    return sorted(by_ts.values(), key=lambda row: row.ts)


def fetch_coinbase_ticker() -> PricePoint:
    payload = request_json("https://api.exchange.coinbase.com/products/BTC-USD/ticker")
    price = sf((payload or {}).get("price"))
    ts = parse_dt((payload or {}).get("time"))
    if price is None or price <= 0 or ts is None:
        raise RuntimeError("Coinbase BTC-USD ticker: invalid payload")
    return PricePoint(ts=ts, price=price, source="Coinbase Exchange:BTC-USD:ticker")


def risk_active_after(item: dict[str, Any], plan: dict[str, Any]) -> Optional[datetime]:
    candidates = [
        parse_dt(item.get("entry_captured_at")),
        parse_dt(plan.get("generated_at")),
        parse_dt(plan.get("created_at")),
    ]
    valid = [value for value in candidates if value is not None]
    return max(valid) if valid else None


def first_hit(
    bars: Iterable[PriceBar],
    side: str,
    sl: float,
    tp: float,
    after: datetime,
) -> Optional[tuple[str, float, datetime, str, str]]:
    for bar in sorted(bars, key=lambda row: row.ts):
        if bar.ts <= after:
            continue
        if side == "long":
            sl_hit = bar.low <= sl
            tp_hit = bar.high >= tp
        else:
            sl_hit = bar.high >= sl
            tp_hit = bar.low <= tp
        if sl_hit:
            return "stop_loss", sl, bar.ts, bar.source, "5m_ohlc"
        if tp_hit:
            return "take_profit", tp, bar.ts, bar.source, "5m_ohlc"
    return None


def ticker_hit(
    point: Optional[PricePoint],
    side: str,
    sl: float,
    tp: float,
    after: datetime,
) -> Optional[tuple[str, float, datetime, str, str]]:
    if point is None or point.ts <= after:
        return None
    if side == "long":
        if point.price <= sl:
            return "stop_loss", sl, point.ts, point.source, "live_ticker"
        if point.price >= tp:
            return "take_profit", tp, point.ts, point.source, "live_ticker"
    else:
        if point.price >= sl:
            return "stop_loss", sl, point.ts, point.source, "live_ticker"
        if point.price <= tp:
            return "take_profit", tp, point.ts, point.source, "live_ticker"
    return None


def set_result(item: dict[str, Any], exit_price: float) -> None:
    entry = sf(item.get("entry_price"))
    if entry is None or entry == 0:
        return
    side = str(item.get("direction") or "")
    if side not in {"long", "short"}:
        return
    move = exit_price - entry if side == "long" else entry - exit_price
    pct = move / entry * 100.0
    if str(item.get("instrument_id")) == "eurusd":
        notional = sf(item.get("notional_eur")) or 10000.0
        value = move * notional
        units = move / 0.0001
    else:
        notional = sf(item.get("notional_usd")) or 10000.0
        value = move / entry * notional
        units = pct if str(item.get("instrument_id")) == "btcusd" else move
    item["result"] = "profit" if value > 0 else "loss" if value < 0 else "flat"
    item["result_value"] = round(value, 8)
    item["result_percent"] = round(pct, 4)
    item["result_units"] = round(units, 8)
    item["result_currency"] = "USD"


def apply_exit(
    item: dict[str, Any],
    hit: tuple[str, float, datetime, str, str],
    detected_at: datetime,
) -> None:
    reason, level, observed_at, source, evidence_kind = hit
    item["exit_price"] = level
    item["exit_captured_at"] = observed_at.astimezone(WARSAW).isoformat(timespec="seconds")
    item["exit_source"] = source
    item["exit_reason"] = reason
    item["exit_execution_model"] = "frozen_threshold_first_observed_stop_first_v2"
    item["risk_status"] = "stop_loss_hit" if reason == "stop_loss" else "take_profit_hit"
    item["trade_status"] = "closed"
    item["continuous_exposure_active"] = False
    item["continuous_exposure_status"] = "closed_by_risk_exit"
    item["pending_entry_decision"] = None
    item["next_entry_status"] = "closed"
    item["risk_exit_detected_at"] = detected_at.astimezone(WARSAW).isoformat(timespec="seconds")
    item["risk_exit_evidence"] = {
        "schema_version": "2.0",
        "kind": evidence_kind,
        "observed_at": item["exit_captured_at"],
        "source": source,
        "frozen_level": level,
        "same_bar_rule": "stop_loss_first_conservative",
        "risk_plan_preexisted_outcome": True,
    }
    set_result(item, level)


def _btc_evidence(
    start: datetime,
    end: datetime,
    side: str,
    sl: float,
    tp: float,
) -> tuple[Optional[tuple[str, float, datetime, str, str]], list[str], str]:
    errors: list[str] = []
    coinbase_bars: list[PriceBar] = []
    ticker: Optional[PricePoint] = None
    try:
        coinbase_bars = fetch_coinbase_bars(start, end)
    except Exception as exc:
        errors.append(f"coinbase_candles:{exc}")
    try:
        ticker = fetch_coinbase_ticker()
    except Exception as exc:
        errors.append(f"coinbase_ticker:{exc}")

    hit = first_hit(coinbase_bars, side, sl, tp, start)
    if hit is None:
        hit = ticker_hit(ticker, side, sl, tp, start)
    if hit is not None:
        return hit, errors, "coinbase_primary"

    if coinbase_bars or ticker is not None:
        return None, errors, "coinbase_primary"

    try:
        yahoo = fetch_yahoo_bars("BTC-USD", start, end)
    except Exception as exc:
        errors.append(f"yahoo_fallback:{exc}")
        yahoo = []
    return first_hit(yahoo, side, sl, tp, start), errors, "yahoo_fallback"


def _yahoo_evidence(
    symbol: str,
    start: datetime,
    end: datetime,
    side: str,
    sl: float,
    tp: float,
) -> tuple[Optional[tuple[str, float, datetime, str, str]], list[str], str]:
    try:
        bars = fetch_yahoo_bars(symbol, start, end)
    except Exception as exc:
        return None, [f"yahoo:{exc}"], "yahoo_primary"
    return first_hit(bars, side, sl, tp, start), [], "yahoo_primary"


def audit(
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
    persist_report: str = "on_close",
) -> dict[str, Any]:
    checked_at = (now or datetime.now(UTC)).astimezone(UTC)
    report: dict[str, Any] = {
        "schema_version": "2.0",
        "checked_at": checked_at.astimezone(WARSAW).isoformat(timespec="seconds"),
        "status": "no_week_file",
        "closed": [],
        "kept": [],
        "errors": [],
    }
    path = path or current_week_path()
    if path is None:
        if persist_report == "always":
            write_json(AUDIT_PATH, report)
        return report

    week = read_json(path)
    changed = False
    for item in week.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        iid = str(item.get("instrument_id") or "")
        side = str(item.get("direction") or "")
        if (
            side not in {"long", "short"}
            or sf(item.get("entry_price")) is None
            or sf(item.get("exit_price")) is not None
        ):
            continue

        plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
        sl, tp = sf(plan.get("stop_loss_price")), sf(plan.get("take_profit_price"))
        active_after = risk_active_after(item, plan)
        if sl is None or tp is None:
            report["errors"].append({"instrument_id": iid, "reason": "missing_frozen_sl_tp"})
            continue
        if active_after is None:
            report["errors"].append({"instrument_id": iid, "reason": "missing_risk_activation_timestamp"})
            continue
        if checked_at <= active_after:
            report["kept"].append({"instrument_id": iid, "reason": "risk_plan_not_yet_active"})
            continue

        if iid == "btcusd":
            hit, errors, authority = _btc_evidence(active_after, checked_at, side, sl, tp)
        else:
            symbol = str(item.get("symbol") or "")
            if not symbol:
                report["errors"].append({"instrument_id": iid, "reason": "missing_symbol"})
                continue
            hit, errors, authority = _yahoo_evidence(symbol, active_after, checked_at, side, sl, tp)
        for error in errors:
            report["errors"].append({"instrument_id": iid, "reason": error})

        if hit is None:
            report["kept"].append({"instrument_id": iid, "authority": authority})
            continue

        apply_exit(item, hit, checked_at)
        changed = True
        report["closed"].append(
            {
                "instrument_id": iid,
                "reason": item.get("exit_reason"),
                "exit_price": item.get("exit_price"),
                "observed_at": item.get("exit_captured_at"),
                "detected_at": item.get("risk_exit_detected_at"),
                "source": item.get("exit_source"),
                "authority": authority,
            }
        )

    if changed:
        write_json(path, week)
    report["status"] = "completed"
    try:
        report["week_path"] = str(path.relative_to(ROOT))
    except ValueError:
        report["week_path"] = str(path)
    report["changed"] = changed

    if persist_report == "always" or (persist_report == "on_close" and changed):
        write_json(AUDIT_PATH, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--persist-report",
        choices=["never", "on_close", "always"],
        default="on_close",
    )
    args = parser.parse_args()
    print(json.dumps(audit(persist_report=args.persist_report), ensure_ascii=False))


if __name__ == "__main__":
    main()
