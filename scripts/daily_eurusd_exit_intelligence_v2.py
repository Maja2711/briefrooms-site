#!/usr/bin/env python3
"""Daily EURUSD exit intelligence v2 (research only, NO execution authority).

Capture time-stamped evidence while a position is open; examine closed trades
without rewriting canonical history or claiming hindsight signals were live.
All future-path counterfactuals are explicitly marked retrospective.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import daily_eurusd_correction_research as corrections
import daily_eurusd_profit_protection_lab as protection

SCHEMA = "eurusd-exit-intelligence-v2"
PIP = 0.0001
MAX_BAR_AGE_SECONDS = 240
MAX_TIME_MATCH_SECONDS = 180
MAX_SNAPSHOTS = 180  # Keep GitHub Contents API journal safely below 1 MB
MAX_REVIEWS = 250
FUTURE_HOURS = (1, 3, 6, 24)
ENTRY_DELAYS_MINUTES = (5, 15, 30)


def parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load(path: Path, fallback: dict) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else fallback
    except (OSError, ValueError):
        return fallback


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.with_suffix(path.suffix + ".tmp")
    target.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    target.replace(path)


def normalize_bars(raw: Sequence[Any], cutoff: datetime) -> list[dict]:
    result = []
    for b in raw:
        if isinstance(b, Mapping):
            time = parse_time(b.get("timestamp") or b.get("time"))
            close, high, low = b.get("close"), b.get("high"), b.get("low")
            opened = b.get("open")
        else:
            time = parse_time(getattr(b, "timestamp", None))
            close, high, low = getattr(b, "close", None), getattr(b, "high", None), getattr(b, "low", None)
            opened = getattr(b, "open", None)
        if time is None or time > cutoff or close is None:
            continue
        try:
            close = float(close)
            high = float(high if high is not None else close)
            low = float(low if low is not None else close)
            if close <= 0 or low > high:
                continue
            result.append({"time": time, "close": close, "open": float(opened if opened is not None else close), "high": high, "low": low})
        except (ValueError, TypeError):
            pass
    return sorted({b["time"]: b for b in result}.values(), key=lambda b: b["time"])


def at(bars: Sequence[dict], target: datetime, tolerance_seconds: int = MAX_TIME_MATCH_SECONDS) -> dict | None:
    match = next((b for b in reversed(bars) if b["time"] <= target), None)
    if match and 0 <= (target - match["time"]).total_seconds() <= tolerance_seconds:
        return match
    return None


def pnl_pips(direction: str, entry: float, exit_mid: float, half_spread_pips: float = 0.75) -> float:
    """Indicative exit using synthetic half spread; entry is already a fill."""
    signed = 1.0 if direction == "LONG" else -1.0
    return round((exit_mid - entry) * signed / PIP - half_spread_pips, 3)


def _window_move(bars: Sequence[dict], target: datetime, minutes: int, direction: str) -> float | None:
    now = at(bars, target)
    earlier = at(bars, target - timedelta(minutes=minutes))
    if not now or not earlier:
        return None
    signed = 1.0 if direction == "LONG" else -1.0
    return round((now["close"] - earlier["close"]) * signed / PIP, 3)


def capture(position: Mapping[str, Any], fx: Sequence[dict], rates: Sequence[dict], now: datetime,
            historic_corrections: Sequence[Mapping[str, Any]] = ()) -> dict | None:
    opened = parse_time(position.get("opened_at"))
    if opened is None or not fx:
        return None
    latest = at(fx, now, MAX_BAR_AGE_SECONDS)
    if latest is None or latest["time"] < opened:
        return None
    direction = str(position.get("direction") or "")
    if direction not in {"LONG", "SHORT"}:
        return None
    entry = float(position["entry"])
    # Use this exact trade's synthetic EPE spread; never assume the old
    # 1.5p total spread when the actual entry used 3p total.
    epe = position.get("execution_price_engine") or {}
    if not isinstance(epe, Mapping):
        epe = {}
    half_value = epe.get("synthetic_half_spread_pips")
    if half_value is None and epe.get("synthetic_spread_pips") is not None:
        half_value = float(epe["synthetic_spread_pips"]) / 2.0
    try:
        half = float(half_value)
        recorded_spread = 0 <= half <= 10
    except (ValueError,TypeError):
        half = 0.75
        recorded_spread = False
    if not recorded_spread:
        half = 0.75
    prior = [b for b in fx if opened <= b["time"] <= latest["time"]]
    if not prior:
        return None
    best = max(b["high"] for b in prior) if direction == "LONG" else min(b["low"] for b in prior)
    best_pips_mid = round((best - entry) * (1 if direction == "LONG" else -1) / PIP, 3)
    current_pips = pnl_pips(direction, entry, latest["close"], half)
    best_indicative_pips = round(best_pips_mid-half,3)
    giveback = round(max(0.0, best_indicative_pips-current_pips), 3)
    m5 = _window_move(fx, latest["time"], 5, direction)
    m15 = _window_move(fx, latest["time"], 15, direction)
    m30 = _window_move(fx, latest["time"], 30, direction)

    # ^TNX is an INDEX PROXY: its points are ten times the annual yield in percent.
    # A 0.1-index-point move ~= 1bp of 10Y yield, NOT a bond-price move.
    rate = at(rates, now, MAX_BAR_AGE_SECONDS)
    rate_past = at(rates, now - timedelta(minutes=15)) if rate else None
    rate_delta_bp = round((rate["close"] - rate_past["close"]) * 10, 3) if rate and rate_past else None
    signals = {
        "profit_reached_8p": best_indicative_pips >= 8,
        "profit_reached_11p": best_indicative_pips >= 11,
        "momentum_5m_reversal": m5 is not None and m5 <= -1.5,
        "momentum_5m_exhaustion": m5 is not None and m5 <= 0,
        "momentum_15m_reversal": m15 is not None and m15 <= -2.5,
        "peak_giveback_35pct_or_3p": best_indicative_pips >= 5 and giveback >= max(3.0, 0.35 * best_indicative_pips),
        "us10y_yield_falling_15m": rate_delta_bp is not None and rate_delta_bp <= -1.0,
        "us10y_yield_rising_15m": rate_delta_bp is not None and rate_delta_bp >= 1.0,
    }
    # Only already COMPLETED 1m bars, never an as-yet unclosed candle.
    correction_as_of = min(latest["time"], now - timedelta(minutes=1))
    correction_context = corrections.current_context(fx, correction_as_of, historic_corrections)
    # Hypotheses apply specifically to the short/downtrend risk, not LONG exits.
    if direction == "SHORT":
        signals.update({"correction_" + key: value for key, value in
                        correction_context["research_warning_flags"].items()})
    # Flags are observations / hypotheses; there is intentionally NO live exit.
    return {
        "trade_id": str(position.get("trade_id")),
        "captured_at": iso(now),
        "market_bar_at": iso(latest["time"]),
        "market_bar_age_seconds": round((now-latest["time"]).total_seconds(), 2),
        "mode": "LIVE_OBSERVATION_RESEARCH_ONLY",
        "entry": entry, "direction": direction, "observed_mid": latest["close"],
        "current_indicative_pips": current_pips,
        "best_favorable_mid_pips": best_pips_mid,
        "best_favorable_indicative_net_pips": best_indicative_pips,
        "epe_synthetic_half_spread_pips": half,
        "epe_spread_from_recorded_entry": recorded_spread,
        "giveback_pips": giveback, "signed_momentum_5m_pips": m5,
        "signed_momentum_15m_pips": m15, "signed_momentum_30m_pips": m30,
        "yield_10y_proxy": {
            "status": "OBSERVED" if rate_delta_bp is not None else "UNAVAILABLE",
            "symbol": "^TNX", "source": "Yahoo Finance 1m INDEX_PROXY",
            "observed_at": iso(rate["time"]) if rate else None,
            "delta_15m_bp": rate_delta_bp,
            "directional_inference": "NOT_CAUSAL_NOT_ALONE_ACTIONABLE",
        },
        "downswing_correction": correction_context,
        "signals": signals, "exit_authority": False,
        "prices_are_executable_bid_ask": False,
    }



def hold_counterfactual(trade: Mapping[str, Any], fx: Sequence[dict], end: datetime) -> dict:
    """Hold AFTER the actual exit with ORIGINAL SL/TP and conservative bar ordering.

    Without a continuous enough bar path we refuse to infer a historical fill.
    This is a counterfactual research simulation, never an executable quote.
    """
    closed = parse_time(trade.get("closed_at"))
    if closed is None:
        return {"status": "MISSING_EXIT_TIMESTAMP"}
    path = [b for b in fx if closed < b["time"] <= end]
    if not path or (path[0]["time"] - closed).total_seconds() > 600:
        return {"status": "INSUFFICIENT_CONTIGUOUS_PATH"}
    direction = str(trade.get("direction") or "")
    entry, stop, target = (float(trade.get(k) or 0) for k in ("entry", "stop", "target"))
    if direction not in ("LONG", "SHORT") or min(entry, stop, target) <= 0:
        return {"status": "INVALID_RISK_GEOMETRY"}
    last_at = closed
    spread_half = 0.75 * PIP
    for candle in path:
        if (candle["time"] - last_at).total_seconds() > 600:
            return {"status": "INSUFFICIENT_CONTIGUOUS_PATH"}
        last_at = candle["time"]
        if direction == "SHORT":
            hit_stop = candle["high"] + spread_half >= stop
            hit_tp = candle["low"] + spread_half <= target
        else:
            hit_stop = candle["low"] - spread_half <= stop
            hit_tp = candle["high"] - spread_half >= target
        if hit_stop or hit_tp:
            # STOP wins whenever both thresholds touched within one 1m bar.
            reason = "STOP_LOSS" if hit_stop else "TAKE_PROFIT"
            exit_price = stop if hit_stop else target
            return {"status": "SIMULATED_RISK_EXIT", "reason": reason,
                    "at": iso(candle["time"]), "simulated_exit": exit_price,
                    "indicative_pips": round((exit_price-entry) * (1 if direction=="LONG" else -1)/PIP, 3),
                    "same_bar_conservative": True, "execution_proven": False}
    last = at(fx, end)
    if last is None or last["time"] <= closed:
        return {"status": "INSUFFICIENT_CONTIGUOUS_PATH"}
    return {"status": "SIMULATED_HORIZON_EXIT", "at": iso(last["time"]),
            "exit_mid": last["close"],
            "indicative_pips": pnl_pips(direction, entry, last["close"]),
            "execution_proven": False}

def review(trade: Mapping[str, Any], snapshots: list[dict], fx: Sequence[dict], now: datetime) -> dict:
    trade_id = str(trade["trade_id"])
    opened = parse_time(trade.get("opened_at"))
    closed = parse_time(trade.get("closed_at"))
    direction = str(trade.get("direction") or "")
    entry = float(trade.get("entry") or 0)
    exit_at = float(trade.get("exit_price") or 0)
    related = sorted((
        s for s in snapshots
        if s.get("trade_id") == trade_id
        and (t := parse_time(s.get("captured_at"))) is not None
        and opened is not None and closed is not None and opened <= t <= closed
        and parse_time(s.get("market_bar_at")) is not None
        and parse_time(s.get("market_bar_at")) <= t
    ), key=lambda x: x["captured_at"])
    profit_snapshots = [s for s in related if s.get("current_indicative_pips", -1e9) >= 8]
    alert_at_profit = [{
        "captured_at": s["captured_at"],
        "observed_profit_pips": s.get("current_indicative_pips"),
        "best_favorable_mid_pips": s.get("best_favorable_mid_pips"),
        "triggered": sorted(k for k, v in s.get("signals", {}).items() if v),
        "rate_data_status": (s.get("yield_10y_proxy") or {}).get("status"),
    } for s in profit_snapshots]
    correction_live = [
        {"captured_at": s["captured_at"], "signals": sorted(
            k for k, v in (s.get("signals") or {}).items()
            if k.startswith("correction_") and v),
         "current_downswing": (s.get("downswing_correction") or {}).get("current_downswing")}
        for s in profit_snapshots
    ]
    observed_alerts = [s for s in alert_at_profit if any(
        k in s["triggered"] for k in ("momentum_5m_reversal", "momentum_15m_reversal", "peak_giveback_35pct_or_3p")
    )]
    recorded_dynamic = ((trade.get("monitor") or {}).get("dynamic_exit") or {})
    out = {
        "trade_id": trade_id, "opened_at": trade.get("opened_at"), "closed_at": trade.get("closed_at"),
        "direction": direction, "entry": entry, "exit_price": exit_at,
        "actual_r": trade.get("r_multiple"), "actual_exit_reason": trade.get("exit_reason"),
        "outcome": trade.get("outcome"), "research_only": True, "decision_mutation_allowed": False,
        "data_audit": {
            "snapshots_before_exit": len(related),
            "profit_snapshots_at_least_8p": len(profit_snapshots),
            "evidence_of_actionable_profit_alert": (
                "OBSERVED_PRE_EXIT_HYPOTHESIS" if observed_alerts else
                "NOT_OBSERVED_IN_CAPTURED_SNAPSHOTS" if profit_snapshots else
                "UNKNOWN_NO_PRE_EXIT_PROFIT_SNAPSHOTS"
            ),
            "do_not_infer_no_signal_from_missing_data": True,
        },
        "profit_protection": {
            "historic_max_favorable_pips": trade.get("mfe_pips"),
            "historic_max_mfe_is_an_oracle_not_a_tradeable_exit": True,
            "pre_exit_profit_alerts": alert_at_profit[-50:],
            "dynamic_exit_at_close": recorded_dynamic,
            "early_exit_proven": False,
            "downswing_correction_live_signals": correction_live[-50:],
            "correction_signals_not_exit_proof": True,
        },
        "entry_timing": {}, "post_exit_path": {}, "risk_bounded_hold": {},
        "counterfactual_status": "RESEARCH_ONLY_NOT_POLICY_AUTHORITY",
    }
    if not opened or not closed or direction not in {"LONG", "SHORT"} or entry <= 0:
        out["counterfactual_status"] = "INCOMPLETE_TRADE_RECORD"
        return out
    for minutes in ENTRY_DELAYS_MINUTES:
        target = opened + timedelta(minutes=minutes)
        price = at(fx, target) if now >= target else None
        if price and target < closed:
            # Exit remains fixed to the actual close: diagnostic timing only,
            # NOT proof a delayed order could have filled at this mid quote.
            difference = (exit_at-price["close"]) * (1 if direction=="LONG" else -1) / PIP
            out["entry_timing"][f"delay_{minutes}m"] = {
                "status": "RETROSPECTIVE_MID_PROXY", "quote_at": iso(price["time"]),
                "entry_mid": price["close"], "indicative_pips_to_actual_exit": round(difference, 3),
                "warning": "not_a_fill_not_same_risk_geometry_or_execution_cost",
            }
        else:
            out["entry_timing"][f"delay_{minutes}m"] = {"status": "NOT_OBSERVED"}
    for hours in FUTURE_HOURS:
        target = closed + timedelta(hours=hours)
        price = at(fx, target) if now >= target else None
        key = f"plus_{hours}h"
        if price:
            out["risk_bounded_hold"][key] = hold_counterfactual(trade, fx, target)
        else:
            out["risk_bounded_hold"][key] = {"status": "PENDING" if now < target else "DATA_UNAVAILABLE"}
        if price:
            pnl = pnl_pips(direction, entry, price["close"])
            actual_pnl = (exit_at-entry) * (1 if direction=="LONG" else -1) / PIP
            out["post_exit_path"][key] = {
                "status": "RETROSPECTIVE_MID_PROXY", "price_at": iso(price["time"]),
                "exit_mid": price["close"], "indicative_hold_pips": pnl,
                "indicative_hold_minus_actual_pips": round(pnl-actual_pnl, 3),
                "warning": "hypothetical_uninterrupted_hold_ignores_intervening_SL_TP_and_slippage",
            }
        else:
            out["post_exit_path"][key] = {"status": "PENDING" if now < target else "DATA_UNAVAILABLE"}
    return out


def step(spot: dict, history: dict, journal: dict, reviews: dict,
         fx_raw: Sequence[Any], rates_raw: Sequence[Any], now: datetime,
         archived_snapshots: Sequence[Mapping[str, Any]] = ()) -> tuple[dict, dict]:
    fx = normalize_bars(fx_raw, now)
    rates = normalize_bars(rates_raw, now)
    snapshots = list(journal.get("snapshots") or [])
    study = corrections.journal_study(
        journal.get("correction_study") or {}, fx, now - timedelta(minutes=1)
    )
    position = ((spot.get("metadata") or {}).get("position") or {})
    monitor_health = {"status": "NO_OPEN_POSITION", "active_trade_id": None}
    if position.get("status") == "OPEN":
        tid = str(position.get("trade_id") or "")
        # Reconstruct at most the latest 10 complete one-minute market bars.
        # Crucially, signal availability is FIRST-SEEN-AT-THE-RUN, not the old
        # bar timestamp. No prior signal is invented as a timely live alert.
        complete = [b for b in fx if b["time"] + timedelta(minutes=1) <= now
                    and b["time"] >= (parse_time(position.get("opened_at")) or now)]
        already = {(s.get("trade_id"),s.get("market_bar_at")) for s in snapshots}
        added = 0
        for row in complete[-10:]:
            if (tid,iso(row["time"])) in already:
                continue
            asof = row["time"] + timedelta(minutes=1)
            point_bars = [b for b in fx if b["time"] <= row["time"]]
            sample = capture(position, point_bars, rates, asof, study["events"])
            if not sample:
                continue
            latency = max(0.0,(now-asof).total_seconds())
            sample["captured_at"] = iso(now)
            sample["first_seen_at"] = iso(now)
            sample["market_bar_closed_at"] = iso(asof)
            sample["availability_latency_seconds"] = round(latency,2)
            sample["timely_observation"] = latency <= 180
            sample["mode"] = ("TIMELY_ASOF_OBSERVATION" if latency <= 180
                              else "DELAYED_BATCH_RECONSTRUCTION")
            sample["retrospective_bar_not_a_live_alert"] = latency > 180
            sample["prices_are_executable_bid_ask"] = False
            snapshots.append(sample)
            already.add((tid,sample["market_bar_at"]))
            added += 1
        newest_close = complete[-1]["time"] + timedelta(minutes=1) if complete else None
        market_age = (now-newest_close).total_seconds() if newest_close else None
        monitor_health = {
            "status": ("OK" if market_age is not None and 0 <= market_age <= 240
                       else "STALE_OR_MISSING_FX_1M_BAR"),
            "active_trade_id": tid, "new_1m_observations":added,
            "latest_complete_bar_closed_at":iso(newest_close) if newest_close else None,
            "age_of_latest_complete_bar_seconds":round(market_age,2) if market_age is not None else None,
            "late_bars_do_not_count_as_timed_exit_alerts":True,
        }
    journal_changed = (
        snapshots[-MAX_SNAPSHOTS:] != list(journal.get("snapshots") or [])
        or study != journal.get("correction_study")
    )
    old_realtime_health = journal.get("realtime_monitor_health")
    old_realtime_heartbeats = journal.get("realtime_heartbeats")
    journal = {"schema_version": SCHEMA,
               "generated_at": iso(now) if journal_changed else journal.get("generated_at", iso(now)),
               "authority": "SHADOW_OBSERVATION_ONLY",
               "monitor_health": monitor_health,
               "snapshots": snapshots[-MAX_SNAPSHOTS:],
               "correction_study": study}
    # The scheduled legacy collector must NEVER erase status/heartbeats
    # published by the independent 1m realtime watcher. Separate clocks.
    if isinstance(old_realtime_health, dict):
        journal["realtime_monitor_health"] = old_realtime_health
    if isinstance(old_realtime_heartbeats, list):
        journal["realtime_heartbeats"] = old_realtime_heartbeats[-50:]
    known = {r["trade_id"]: r for r in (reviews.get("reviews") or [])}
    closed_trades = [t for t in (history.get("trades") or []) if t.get("closed_at")]
    for t in closed_trades[-MAX_REVIEWS:]:
        closed = parse_time(t.get("closed_at"))
        if closed is None or closed > now:
            continue
        # Never retroactively create evidence of a live alert. Existing reviews
        # may progress only by adding retrospective observations after maturity.
        key = str(t["trade_id"])
        # Full trading-life research context includes immutable older shards.
        # Only the last 180 observations remain in the hot journal.
        review_snapshots = combine_complete_snapshots(archived_snapshots, snapshots)
        fresh = review(t, review_snapshots, fx, now)
        old = known.get(key)
        if old:
            # Retain previously measured prices after the provider's 5-day
            # minute-history retention window has elapsed. No evidence erasure.
            for section in ("post_exit_path", "entry_timing", "risk_bounded_hold"):
                for point, value in (old.get(section) or {}).items():
                    if value.get("status") == "RETROSPECTIVE_MID_PROXY" and fresh[section].get(point, {}).get("status") != "RETROSPECTIVE_MID_PROXY":
                        fresh[section][point] = value
            if old.get("data_audit", {}).get("snapshots_before_exit", 0) > fresh["data_audit"]["snapshots_before_exit"]:
                fresh["data_audit"] = old["data_audit"]
                fresh["profit_protection"]["pre_exit_profit_alerts"] = old.get("profit_protection", {}).get("pre_exit_profit_alerts", [])
        known[key] = fresh
    values = list(known.values())[-MAX_REVIEWS:]
    changed = values != list(reviews.get("reviews") or [])
    reviews = {"schema_version": SCHEMA,
               "generated_at": iso(now) if changed else reviews.get("generated_at", iso(now)),
               "authority": "SHADOW_RESEARCH_ONLY", "automatic_promotion": False,
               "trading_decision_influence": False, "reviews": values}
    return journal, reviews


def combine_complete_snapshots(archived: Sequence[Mapping[str, Any]],
                               hot: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Deduplicate using the earliest actual first_seen_at, never arrival order."""
    merged: dict[tuple[str,str],dict] = {}
    for row in list(archived)+list(hot):
        if not isinstance(row,Mapping):
            continue
        key=(str(row.get("trade_id") or ""),str(row.get("market_bar_at") or ""))
        if not all(key):
            continue
        seen=parse_time(row.get("first_seen_at") or row.get("captured_at"))
        if seen is None:
            continue
        old=merged.get(key)
        former=parse_time(old.get("first_seen_at") or old.get("captured_at")) if old else None
        if old is None or former is None or seen < former:
            merged[key]=dict(row)
    return sorted(merged.values(),key=lambda x:(x["market_bar_at"],x["trade_id"]))


def read_archived_snapshots(root: Path) -> list[dict]:
    """Rehydrate older REAL first-seen observations from bounded 2h shards.

    Archives are copied from the repo checkout; NEVER reconstruct a missing
    live signal from historical OHLC or replace its first_seen_at.
    """
    if not root.is_dir():
        return []
    shards = sorted(root.glob("*/*.json"))
    dedup: dict[tuple[str,str],dict] = {}
    for path in shards:
        payload = load(path,{})
        if payload.get("authority") != "SHADOW_OBSERVATION_ONLY":
            continue
        for row in payload.get("snapshots") or []:
            if not isinstance(row,dict) or not row.get("first_seen_at"):
                continue
            key = (str(row.get("trade_id") or ""),str(row.get("market_bar_at") or ""))
            if not all(key):
                continue
            prior = dedup.get(key)
            if not prior or str(row["first_seen_at"]) < str(prior["first_seen_at"]):
                dedup[key] = row
    return sorted(dedup.values(),key=lambda v:(v["market_bar_at"],v["trade_id"]))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--spot", default="data/investments/eurusd_daily_spot.json")
    p.add_argument("--history", default="data/investments/eurusd_daily_history.json")
    p.add_argument("--journal", default="data/investments/eurusd_exit_signal_journal.json")
    p.add_argument("--reviews", default="data/investments/eurusd_exit_intelligence_reviews.json")
    p.add_argument("--protection", default="data/investments/eurusd_profit_protection_lab.json")
    p.add_argument("--archive-dir",default="data/investments/eurusd_live_signal_archive")
    args = p.parse_args()
    now = datetime.now(timezone.utc)
    spot, history = load(Path(args.spot), {}), load(Path(args.history), {})
    journal = load(Path(args.journal), {})
    reviews = load(Path(args.reviews), {})
    fx, rates = [], []
    try:
        from belief_market_data_adapter import YahooChartClient
        client = YahooChartClient(timeout=12)
        fx = client.bars("EURUSD=X", "5d", "1m")
    except Exception as exc:
        print("FX_BARS_UNAVAILABLE", type(exc).__name__)
    try:
        from belief_market_data_adapter import YahooChartClient
        rates = YahooChartClient(timeout=12).bars("^TNX", "5d", "1m")
    except Exception as exc:
        print("US10Y_INDEX_PROXY_UNAVAILABLE", type(exc).__name__)
    archived = read_archived_snapshots(Path(args.archive_dir))
    journal, reviews = step(spot, history, journal, reviews, fx, rates, now,
                            archived_snapshots=archived)
    lab_journal = dict(journal)
    # This is an IN-MEMORY rehydrated view for research; do not inflate the
    # persistently published <=180-snapshot live journal.
    lab_journal["snapshots"] = combine_complete_snapshots(archived, journal.get("snapshots",[]))
    baseline_lab = protection.step(load(Path(args.protection), {}), history, lab_journal, normalize_bars(fx, now), now)
    save(Path(args.protection), baseline_lab)
    save(Path(args.journal), journal)
    save(Path(args.reviews), reviews)
    print("EURUSD_EXIT_INTELLIGENCE", json.dumps({
        "snapshots": len(journal["snapshots"]), "reviews": len(reviews["reviews"]),
        "protection_comparisons": len(baseline_lab["comparisons"]),
        "monitor_health": journal.get("monitor_health",{}).get("status"),
        "latest_trade": (reviews["reviews"][-1]["trade_id"] if reviews["reviews"] else None),
        "execution_authority": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
