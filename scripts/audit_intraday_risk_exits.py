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

import wes_notification_outbox as wes_outbox
import wes_eurusd_profit_protection as eurusd_profit

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
AUDIT_PATH = ROOT / "data" / "investments" / "intraday_risk_audit.json"
LIVE_PRICES_PATH = ROOT / "data" / "investments" / "live_prices.json"
WES_POLICY_PATH = ROOT / "data" / "investments" / "multi_instrument_exposure_policy.json"
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
    close: Optional[float] = None  # optional for legacy SL/TP callers


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


def _valid_bar(ts: datetime, high: Any, low: Any, source: str,
               close: Any = None) -> Optional[PriceBar]:
    hi, lo = sf(high), sf(low)
    if hi is None or lo is None or hi <= 0 or lo <= 0 or hi < lo:
        return None
    cp = sf(close)
    if close is not None and (cp is None or cp <= 0):
        return None
    return PriceBar(ts=ts.astimezone(UTC), high=hi, low=lo, source=source, close=cp)


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
    closes = quote.get("close") or []
    source = f"Yahoo Finance:{symbol}:chart:5m"
    bars: list[PriceBar] = []
    for k, (raw_ts, raw_high, raw_low) in enumerate(zip(stamps, highs, lows)):
        try:
            ts = datetime.fromtimestamp(int(raw_ts), tz=UTC)
        except (TypeError, ValueError, OSError):
            continue
        bar = _valid_bar(ts, raw_high, raw_low, source,
                         closes[k] if k < len(closes) else None)
        if bar and start - BAR <= bar.ts <= end + BAR:
            bars.append(bar)
    bars.sort(key=lambda row: row.ts)
    return bars


def fetch_yahoo_completed_daily_closes(symbol: str, now: datetime) -> list[tuple[datetime, float]]:
    """Past completed D1 closes only; exclude today's forming session.

    Yahoo D1 timestamps mark the OPEN of a daily bucket. Label the close
    conservatively as open+1 day, never at the bucket start. The existing
    Yahoo EURUSD feed remains the sole source for this optional indicator.
    """
    t0 = int((now - timedelta(days=95)).timestamp())
    t1 = int((now + timedelta(days=1)).timestamp())
    query = urllib.parse.urlencode({
        "period1": t0, "period2": t1, "interval": "1d",
        "includePrePost": "true",
    })
    symbol_safe = urllib.parse.quote(symbol, safe="")
    payload = request_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_safe}?{query}")
    chart = ((payload or {}).get("chart", {}).get("result") or [None])[0]
    if not isinstance(chart, dict):
        raise RuntimeError("missing_eurusd_d1_evidence")
    stamps = chart.get("timestamp") or []
    closes = (((chart.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
    result = []
    for stamp, raw_close in zip(stamps, closes):
        try:
            start = datetime.fromtimestamp(int(stamp),tz=UTC)
        except (TypeError, ValueError, OSError):
            continue
        closed_at = start + timedelta(days=1)
        value = sf(raw_close)
        if value is not None and value > 0 and closed_at <= now:
            result.append((closed_at, value))
    return result


def _eurusd_policy_cost() -> Optional[float]:
    cfg = read_json(WES_POLICY_PATH)
    for row in cfg.get("instruments") or []:
        if (isinstance(row, dict) and row.get("instrument_id") == "eurusd"
                and row.get("cost_unit") == "pips"):
            cost = sf(row.get("round_trip_cost"))
            return cost if cost is not None and cost >= 0 else None
    return None


def apply_eurusd_profit_exit(item: dict[str, Any], review: dict[str, Any],
                             detected_at: datetime) -> None:
    """Canonical EURUSD-only close on THIS completed 5m price, never old peak."""
    price = float(review["exit_price"])
    close_at = parse_dt(review["exit_captured_at"])
    if close_at is None or close_at <= (parse_dt(item.get("entry_captured_at")) or close_at):
        raise ValueError("profit_exit_missing_post_entry_evidence")
    if item.get("exit_price") is not None:
        raise ValueError("profit_exit_position_not_open")
    item.update(
        exit_price=price,
        exit_captured_at=close_at.astimezone(WARSAW).isoformat(timespec="seconds"),
        exit_source=str(review.get("source") or "") + ":completed_5m_profit_review",
        exit_reason=review["exit_reason"],
        exit_execution_model="prospective_completed_5m_close_mid_proxy_cost_reported",
        close_quality_status="verified_current_5m_not_historical_peak",
        trade_status="closed", risk_status="closed_by_wes_eurusd_profit_protection",
        continuous_exposure_active=False,
        continuous_exposure_status="closed_by_profit_exit",
        pending_entry_decision=None, next_entry_status="closed",
        risk_exit_detected_at=detected_at.astimezone(WARSAW).isoformat(timespec="seconds"),
        profit_exit_evidence=review,
    )
    set_result(item,price)


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


def canonical_live_risk_hit(
    instrument_id: str,
    side: str,
    sl: float,
    tp: float,
    after: datetime,
    checked_at: datetime,
) -> Optional[tuple[str, float, datetime, str, str]]:
    """Primary SL/TP trigger from the exact public WES 'Cena teraz' snapshot."""
    live = read_json(LIVE_PRICES_PATH)
    rec = (live.get("prices") or {}).get(instrument_id) if isinstance(live.get("prices"), dict) else None
    if not isinstance(rec, dict):
        return None
    price = sf(rec.get("price"))
    stamp = parse_dt(rec.get("current_price_updated_at") or rec.get("timestamp"))
    if price is None or price <= 0 or stamp is None or stamp <= after:
        return None
    if stamp > checked_at + timedelta(seconds=30) or checked_at - stamp > timedelta(minutes=6):
        return None
    source = str(rec.get("source") or "WES canonical live price") + ":canonical_live_risk"
    return ticker_hit(PricePoint(ts=stamp, price=price, source=source), side, sl, tp, after)


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
    instrument_id: Optional[str] = None,
) -> dict[str, Any]:
    # Optional EURUSD-only preflight preserves the universal default monitor.
    # It grants no new authority or changed execution semantics to other assets.
    if instrument_id not in (None, "eurusd"):
        raise ValueError("only EURUSD scoped preflight is supported")
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
        if instrument_id is not None and iid != instrument_id:
            continue
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

        # EURUSD carries its verified 5m chart only once per audit, for
        # BOTH frozen SL/TP priority and the later optional profit review.
        # BTC/SPX retain their original independent source semantics.
        eurusd_bars = None
        symbol = str(item.get("symbol") or "")
        hit = canonical_live_risk_hit(iid, side, sl, tp, active_after, checked_at)
        if hit is not None:
            errors, authority = [], "canonical_wes_live_price_primary"
        elif iid == "btcusd":
            hit, errors, authority = _btc_evidence(active_after, checked_at, side, sl, tp)
        elif iid == "eurusd":
            if not symbol:
                report["errors"].append({"instrument_id": iid, "reason": "missing_symbol"})
                continue
            try:
                eurusd_bars = fetch_yahoo_bars(symbol, active_after, checked_at)
                hit, errors, authority = first_hit(eurusd_bars, side, sl, tp, active_after), [], "yahoo_primary"
            except Exception as exc:
                hit, errors, authority = None, [f"yahoo:{exc}"], "yahoo_primary"
        else:
            if not symbol:
                report["errors"].append({"instrument_id": iid, "reason": "missing_symbol"})
                continue
            hit, errors, authority = _yahoo_evidence(symbol, active_after, checked_at, side, sl, tp)
        for error in errors:
            report["errors"].append({"instrument_id": iid, "reason": error})

        if hit is None:
            # EURUSD profit-taking belongs to the SAME independent 5-minute
            # risk writer, strictly AFTER verification that no frozen SL/TP
            # has been hit. No other instrument receives this capability.
            is_governed_wes = bool(
                item.get("wes_entry_authorization")
                or item.get("wes_methodology") == "WES-1.3.1"
                or item.get("wes_status") == "open_wes_continuous_position"
                or plan.get("holding_policy") == "wes_continuous_7_calendar_days"
            )
            if iid == "eurusd" and is_governed_wes and not errors and symbol:
                try:
                    # When a separate verified live quote was the hard-risk
                    # source, obtain its full 5m history here too; otherwise
                    # reuse the exact same 5m observations checked above.
                    five_min = (eurusd_bars if eurusd_bars is not None
                                else fetch_yahoo_bars(symbol, active_after, checked_at))
                    # Never profit-close a leg whose original hard SL/TP was
                    # already touched in the source OHLC history.
                    missed = first_hit(five_min, side, sl, tp, active_after)
                    if missed is not None:
                        apply_exit(item, missed, checked_at)
                        if wes_outbox.is_wes_week(week):
                            wes_outbox.emit_weekly_event(str(week.get("week_id") or path.stem), item, "CLOSE")
                        changed = True
                        report["closed"].append({"instrument_id": iid, "reason": item["exit_reason"],
                            "exit_price": item["exit_price"], "observed_at": item["exit_captured_at"],
                            "authority": "yahoo_replayed_frozen_hard_risk_priority"})
                        continue
                    try:
                        daily = fetch_yahoo_completed_daily_closes(symbol, checked_at)
                    except (OSError, RuntimeError, ValueError, TypeError):
                        # Missing optional D1 context must not disable the
                        # independently proven 5m MFE-giveback risk guard.
                        daily = []
                    cost = _eurusd_policy_cost()
                    review = eurusd_profit.assess(
                        item, five_min, daily, checked_at,
                        round_trip_cost_pips=cost if cost is not None else float("nan"),
                    )
                    report.setdefault("profit_reviews", []).append({
                        "instrument_id": iid, "status": review.get("reason"),
                        "peak_net_pips": review.get("peak_net_pips")
                                         or review.get("peak_net_proxy_pips"),
                        "giveback_pips": review.get("giveback_pips"),
                        "closed_5m_at": review.get("closed_5m_at"),
                        "daily_band_touch_now": review.get("daily_band_touch_now"),
                    })
                    if review.get("close") is True:
                        apply_eurusd_profit_exit(item, review, checked_at)
                        if wes_outbox.is_wes_week(week):
                            wes_outbox.emit_weekly_event(str(week.get("week_id") or path.stem), item, "CLOSE")
                        changed = True
                        report["closed"].append({
                            "instrument_id": iid, "reason": item["exit_reason"],
                            "exit_price": item["exit_price"],
                            "observed_at": item["exit_captured_at"],
                            "detected_at": item["risk_exit_detected_at"],
                            "source": item["exit_source"],
                            "authority": "eurusd_completed_5m_profit_protection",
                        })
                        continue
                except Exception as exc:
                    # Optional technical profit-taking must never become a
                    # reason to skip hard SL/TP, or invent a price on failure.
                    report["errors"].append({
                        "instrument_id": iid,
                        "reason": "eurusd_profit_review:" + type(exc).__name__,
                    })
            report["kept"].append({"instrument_id": iid, "authority": authority})
            continue

        apply_exit(item, hit, checked_at)
        if wes_outbox.is_wes_week(week):
            wes_outbox.emit_weekly_event(str(week.get("week_id") or path.stem), item, "CLOSE")
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
    # Audit all carried open WES ledgers: a new Monday must never end
    # the five-minute hard-risk coverage of last Friday's still-open leg.
    open_paths = []
    for path in sorted(WEEKLY_DIR.glob("????-W??.json"))[-8:]:
        week = read_json(path)
        if any(isinstance(item, dict)
               and str(item.get("direction") or "") in {"long", "short"}
               and sf(item.get("entry_price")) is not None
               and item.get("exit_price") is None
               for item in week.get("instruments") or []):
            open_paths.append(path)
    reports = ([audit(path=path, persist_report=args.persist_report)
                for path in open_paths] if open_paths
               else [audit(persist_report=args.persist_report)])
    print(json.dumps({"status": "completed", "reports": reports,
                      "monitored_week_ledgers": [p.stem for p in open_paths]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
