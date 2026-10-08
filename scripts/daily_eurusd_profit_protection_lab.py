#!/usr/bin/env python3
"""EURUSD Daily profit-protection challengers versus canonical R_PACE_V1.

Research-only, single canonical trade stream. Each challenger starts from the
SAME verified paper entry, stop, target and as-of 1m OHLC timeline. It observes
only completed bars and simulates any market exit at the NEXT bar's open with
per-trade EPE half-spread, not at the retrospectively optimal peak. Hard SL/TP
takes precedence, stop wins a same-bar tie. No missing bars may be skipped.

Comparison is against the immutable *actual* R_PACE/lifecycle exit, not a
fabricated baseline rerun. Historical replay is never represented as a signal
actually observed by the production trader.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
import json

SCHEMA = "eurusd-profit-protection-challenger-v1"
PIP = 0.0001
STRATEGIES = ("MOMENTUM_5_15", "GIVEBACK_MOMENTUM", "CLOSE_TRAIL_035R")
MAX_REPLAY_TRADES = 150
MAX_GAP_SECONDS = 61
REQUIRED_MIN_COMPARE_TRADES = 40
MIN_PERSISTENCE_DAYS = 15


def parse(value: Any) -> datetime | None:
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return out.astimezone(timezone.utc) if out.tzinfo else out.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def spread(trade: Mapping[str, Any]) -> tuple[float, bool]:
    epe = trade.get("execution_price_engine") or {}
    if not isinstance(epe, Mapping):
        epe = {}
    value = epe.get("synthetic_half_spread_pips")
    if value is None and epe.get("synthetic_spread_pips") is not None:
        value = float(epe["synthetic_spread_pips"]) / 2
    try:
        half = float(value)
        if 0 <= half <= 10:
            return half, True
    except (ValueError, TypeError):
        pass
    return 0.75, False


def sign(direction: str) -> float:
    return 1.0 if direction == "LONG" else -1.0


def result_r(trade: Mapping[str, Any], mid: float, half_spread: float) -> tuple[float, float]:
    direction = str(trade["direction"])
    entry, stop = float(trade["entry"]), float(trade["stop"])
    risk_pips = abs(entry - stop) / PIP
    if risk_pips <= 0:
        raise ValueError("invalid original stop")
    pips = (float(mid) - entry) / PIP * sign(direction) - half_spread
    return round(pips, 4), round(pips / risk_pips, 6)


def _contiguous(bars: Sequence[dict]) -> bool:
    return all(0 < (bars[i]["time"] - bars[i-1]["time"]).total_seconds() <= MAX_GAP_SECONDS
               for i in range(1, len(bars)))


def _intrabar_terminal(trade: Mapping[str, Any], bar: Mapping[str, Any],
                       half: float) -> tuple[str, float] | None:
    """Same-bar uncertainty resolves against the trade (STOP before TP)."""
    direction = str(trade["direction"])
    stop, target = float(trade["stop"]), float(trade["target"])
    high, low = float(bar["high"]), float(bar["low"])
    if direction == "SHORT":
        stop_hit = high + half * PIP >= stop
        target_hit = low + half * PIP <= target
    else:
        stop_hit = low - half * PIP <= stop
        target_hit = high - half * PIP >= target
    if stop_hit:
        return "STOP_LOSS", stop
    if target_hit:
        return "TAKE_PROFIT", target
    return None


def _asof_signal(history: Sequence[dict], trade: Mapping[str, Any], policy: str) -> dict | None:
    """A decision uses CLOSES no later than the last COMPLETED candle."""
    if len(history) < 16:
        return None
    signum = sign(str(trade["direction"]))
    entry, stop = float(trade["entry"]), float(trade["stop"])
    risk_pips = abs(entry - stop) / PIP
    closes = [float(b["close"]) for b in history]
    best = max((x-entry)/PIP*signum for x in closes)
    now = (closes[-1]-entry)/PIP*signum
    giveback = max(0.0, best-now)
    m5 = (closes[-1] - closes[-6])/PIP*signum
    m15 = (closes[-1] - closes[-16])/PIP*signum
    if policy == "MOMENTUM_5_15":
        passed = now >= .20*risk_pips and m5 <= -1.5 and m15 <= -2.5
    elif policy == "GIVEBACK_MOMENTUM":
        passed = best >= .30*risk_pips and giveback >= .15*risk_pips and m5 <= -1.5
    elif policy == "CLOSE_TRAIL_035R":
        passed = best >= .35*risk_pips and giveback >= .20*risk_pips
    else:
        raise ValueError("unknown shadow strategy")
    return ({
        "policy": policy, "signal_at_bar_close": ts(history[-1]["time"] + timedelta(minutes=1)),
        "best_close_pips": round(best, 3), "now_close_pips": round(now, 3),
        "giveback_close_pips": round(giveback, 3),
        "momentum_5m_signed_pips": round(m5, 3),
        "momentum_15m_signed_pips": round(m15, 3),
        "exit_decision": bool(passed), "future_used": False,
    } if passed else None)


def simulate(trade: Mapping[str, Any], bars: Sequence[dict], policy: str) -> dict:
    """Compare an alternative early exit ONLY within actual open-life window.

    If the proposed exit didn't happen before actual baseline close, record
    NOT_TRIGGERED_BEFORE_BASELINE; no imaginary post-close position is opened.
    """
    opened, closed = parse(trade.get("opened_at")), parse(trade.get("closed_at"))
    direction = str(trade.get("direction") or "")
    if not opened or not closed or direction not in ("SHORT", "LONG"):
        return {"status": "INVALID_TRADE"}
    if not (float(trade.get("entry") or 0) and float(trade.get("stop") or 0)
            and float(trade.get("target") or 0)):
        return {"status": "INVALID_GEOMETRY"}
    # Only fully completed bars with start at/after entry, before actual exit.
    # The opening-minute partial candle is rejected as entry/exit ambiguous.
    path = sorted((b for b in bars if b["time"] >= opened and
                   b["time"] + timedelta(minutes=1) <= closed), key=lambda b: b["time"])
    if len(path) < 17:
        return {"status": "CENSORED_NO_FULL_ENTRY_TO_EXIT_PATH"}
    if (path[0]["time"] - opened).total_seconds() > 120 or not _contiguous(path):
        return {"status": "CENSORED_MARKET_DATA_GAP"}
    half, epe_recorded = spread(trade)
    baseline_r = trade.get("r_multiple")
    if baseline_r is None:
        return {"status": "NO_CANONICAL_BASELINE"}
    pending = None
    for index, bar in enumerate(path):
        # Deliberate latency: after a bar CLOSE makes a signal available,
        # one entire bar must elapse before the simulated entry-side OPEN.
        # This prevents an impossible zero-latency fill at the exact close.
        # Terminal STOP/TP precedence remains conservative.
        terminal = _intrabar_terminal(trade, bar, half)
        if terminal:
            reason, level = terminal
            # Exact stop/target is already an executable-side price; do NOT
            # deduct half spread a second time from this level.
            pips = (level-float(trade["entry"]))/PIP*sign(direction)
            r = pips/(abs(float(trade["entry"])-float(trade["stop"]))/PIP)
            return {
                "status": "INTRABAR_RISK_BARRIER",
                "policy": policy, "exit_reason": reason,
                "signal": pending, "simulated_exit_at": ts(bar["time"]),
                "simulated_pips": round(pips,4), "simulated_r": round(r,6),
                "vs_actual_r": round(r-float(baseline_r),6),
                "conservative_same_bar": True, "epe_half_spread_recorded": epe_recorded,
                "execution_proven": False,
            }
        if pending is not None:
            signal_at = parse(pending["signal_at_bar_close"])
            if signal_at is None or bar["time"] <= signal_at:
                # Even if market opened at exactly the signal timestamp, it
                # was not available early enough to guarantee a market fill.
                continue
            # Strictly later OPEN, with >=1m latency; NOT executable quote.
            fill = float(bar.get("open") if bar.get("open") is not None else bar["close"])
            pips, r = result_r(trade, fill, half)
            return {
                "status": "EARLIER_RESEARCH_EXIT", "policy": policy,
                "exit_reason": "SHADOW_" + policy, "signal": pending,
                "simulated_exit_at": ts(bar["time"]), "simulated_mid_open": round(fill,8),
                "simulated_pips": pips, "simulated_r": r,
                "vs_actual_r": round(r-float(baseline_r),6),
                "cost_half_spread_pips": half, "epe_half_spread_recorded": epe_recorded,
                "execution_proven": False, "signal_available_before_fill": True,
                "latency_since_signal_seconds": round((bar["time"]-signal_at).total_seconds(),2),
            }
        pending = _asof_signal(path[:index+1], trade, policy)
    return {
        "status": "NOT_TRIGGERED_BEFORE_BASELINE",
        "policy": policy, "baseline_r": baseline_r,
        "complete_until": ts(path[-1]["time"] + timedelta(minutes=1)),
        "epe_half_spread_recorded": epe_recorded,
    }


def coverage_for_trade(trade: Mapping[str, Any], snapshots: Sequence[Mapping[str, Any]]) -> dict:
    opened, closed = parse(trade.get("opened_at")), parse(trade.get("closed_at"))
    samples = [s for s in snapshots if s.get("trade_id") == trade.get("trade_id")]
    captured = sorted((
        s for s in samples if parse(s.get("first_seen_at") or s.get("captured_at"))
        and parse(s.get("market_bar_closed_at") or s.get("market_bar_at"))
        and (opened is None or parse(s.get("market_bar_closed_at") or s.get("market_bar_at")) >= opened)
        and (closed is None or parse(s.get("first_seen_at") or s.get("captured_at")) <= closed)
    ), key=lambda s: s["market_bar_at"])
    timely = [s for s in captured if s.get("timely_observation") is True]
    gap = [
        round((parse(captured[i]["market_bar_at"])-parse(captured[i-1]["market_bar_at"])).total_seconds()/60,2)
        for i in range(1,len(captured))
    ]
    return {
        "total_preclose_observed": len(captured), "timely_1m_observed": len(timely),
        "delayed_backfill_count": len(captured)-len(timely),
        "max_market_bar_gap_minutes": max(gap) if gap else None,
        "first_evidence_at": min((s.get("first_seen_at") or s.get("captured_at") for s in captured), default=None),
        "last_evidence_at": max((s.get("first_seen_at") or s.get("captured_at") for s in captured), default=None),
        "evidence_status": "TIMELY_CAPTURE_PRESENT" if timely else "UNKNOWN_NO_TIMELY_SIGNALS",
        "no_hindsight_promotion": True,
    }


def step(previous: Mapping[str, Any], history: Mapping[str, Any],
         journal: Mapping[str, Any], bars: Sequence[dict], now: datetime) -> dict:
    """Immutable separate research artifact; NEVER edits canonical history."""
    trades = (history.get("trades") or [])[-MAX_REPLAY_TRADES:]
    snapshots = journal.get("snapshots") or []
    existing = {r["trade_id"]: r for r in (previous.get("comparisons") or [])}
    for trade in trades:
        tid = str(trade.get("trade_id") or "")
        if not tid or not trade.get("closed_at"):
            continue
        old = existing.get(tid) or {}
        new = {
            "trade_id": tid,
            "opened_at": trade.get("opened_at"), "closed_at": trade.get("closed_at"),
            "direction": trade.get("direction"),
            "canonical_engine_version": trade.get("engine_version"),
            "baseline": {
                "name": "R_PACE_V1_CANONICAL_ACTUAL_LIFECYCLE",
                "exit_reason": trade.get("exit_reason"), "actual_exit_price": trade.get("exit_price"),
                "actual_r": trade.get("r_multiple"), "actual_pips": (
                    round((float(trade["exit_price"])-float(trade["entry"]))/PIP*sign(trade["direction"]),4)
                    if trade.get("exit_price") and trade.get("entry") else None
                ),
                "not_a_replay": True,
            },
            "asof_observation_audit": coverage_for_trade(trade,snapshots),
            "challengers": {},
            "authority": "SHADOW_ONLY_NO_EXIT_WRITEBACK",
        }
        for policy in STRATEGIES:
            val = simulate(trade,bars,policy)
            prior = (old.get("challengers") or {}).get(policy)
            # Do not erase settled 1m path evidence after Yahoo retention.
            if prior and prior.get("status") in ("EARLIER_RESEARCH_EXIT","INTRABAR_RISK_BARRIER","NOT_TRIGGERED_BEFORE_BASELINE") and val.get("status","").startswith("CENSORED"):
                val = prior
            new["challengers"][policy] = val
        existing[tid] = new
    rows = list(existing.values())[-MAX_REPLAY_TRADES:]
    # Include "no earlier trigger" as an intentional no-op with delta=0;
    # otherwise the evaluator selects only trades where the challenger fired.
    # STOP/TP path conflicts and incomplete observations remain excluded.
    summary = {}
    for policy in STRATEGIES:
        paired = [
            (r, r["challengers"][policy]) for r in rows
            if r["challengers"][policy].get("status")
               in ("EARLIER_RESEARCH_EXIT", "NOT_TRIGGERED_BEFORE_BASELINE")
            and r["challengers"][policy].get("epe_half_spread_recorded") is True
        ]
        values = [
            float(v["vs_actual_r"]) if v["status"] == "EARLIER_RESEARCH_EXIT" else 0.0
            for _, v in paired
        ]
        days = len({str(r["opened_at"])[:10] for r, _ in paired})
        summary[policy] = {
            "n_paired_comparable_trades": len(values),
            "distinct_trade_days": days,
            "earlier_exit_trades": sum(v["status"] == "EARLIER_RESEARCH_EXIT" for _, v in paired),
            "no_early_exit_trades": sum(v["status"] == "NOT_TRIGGERED_BEFORE_BASELINE" for _, v in paired),
            "mean_incremental_r": round(sum(values)/len(values),6) if values else None,
            "positive_delta_trades": sum(1 for v in values if v>0),
            "status": ("ELIGIBLE_FOR_RESEARCH_REVIEW" if len(values) >= REQUIRED_MIN_COMPARE_TRADES and
                       days >= MIN_PERSISTENCE_DAYS else
                       "DESCRIPTIVE_INSUFFICIENT_INDEPENDENT_EVIDENCE" if values else "NO_PAIRED_EVIDENCE"),
            "promotion_allowed": False,
        }
    value = {
        "schema_version":SCHEMA,"instrument":"EUR/USD","authority":{
            "execution":False,"daily_exit_mutation":False,"belief_core_writeback":False,
            "automatic_promotion":False,"production_timing_influence":False,
        },
        "candidate_policies":list(STRATEGIES),
        "canonical_baseline":"ACTUAL_R_PACE_V1_AND_HARD_LIFECYCLE",
        "cost_model":"recorded EPE half-spread on next 1m open; stop/target are side-specific",
        "comparison_contract":"causal_asof_1m_replay_vs_actual_canonical_exit; no oracle exits; no data gaps",
        "retrospective_replay_not_a_live_observation":True,
        "min_comparable_trades_for_policy_review":REQUIRED_MIN_COMPARE_TRADES,
        "min_independent_days_for_policy_review":MIN_PERSISTENCE_DAYS,
        "paired_comparable_trade_count":min((v["n_paired_comparable_trades"] for v in summary.values()), default=0),
        "scoreboard":summary,"comparisons":rows,
        "promotion_allowed":False,
    }
    prior=dict(previous)
    prior.pop("updated_at",None)
    value["updated_at"]=(previous.get("updated_at",ts(now))
                         if value==prior else ts(now))
    return value
