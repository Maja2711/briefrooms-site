#!/usr/bin/env python3
"""EUR/USD X adaptive research-shadow engine for BriefRooms LAB.

EURUSD X keeps a fixed technical core (MA30/60/100/200 on H1/D1/W1/M1,
classic daily Pivot, Bollinger(20, 2.5 sigma) on H1/D1) and a read-only
BriefRooms Belief Core input. Around that immutable core it evaluates bounded
supplementary technical strategies prospectively, recalibrates after every
resolved 4h outcome, and can promote/rollback only inside X shadow state.

No production execution, Daily EUR/USD writeback, A/B/C mutation, Belief
writeback, or historical signal backfill is permitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar, YahooChartClient
import daily_eurusd_experiment as abc

SCHEMA_VERSION = "eurusd-x-shadow-v1"
PUBLIC_SCHEMA_VERSION = "eurusd-x-public-pl-v1"
ENGINE_VERSION = "eurusd-x-v1.0.0"
MODE = "research_shadow"
SYMBOL = "EURUSD=X"
STATE_FILENAME = "EURUSD_X_SHADOW_STATE.json"
REPORT_FILENAME = "EURUSD_X_SHADOW_REPORT.json"
PRIMARY_HORIZON_HOURS = 4
SECONDARY_HORIZON_HOURS = 24
MAX_H1_STALENESS_MINUTES = 180
MIN_COMMON_EVAL = 8
ROLLBACK_WINDOW = 8
ANOMALY_MIN_SAMPLE = 24
ANOMALY_STEP = 16
MAX_AUTO_SETUPS = 8

FIXED_CORE = {
    "ma_windows": [30, 60, 100, 200],
    "ma_timeframes": ["H1", "D1", "W1", "M1"],
    "pivot": "classic_daily_floor_P_R1_R2_R3_S1_S2_S3",
    "bollinger": {"window": 20, "stddevs": 2.5, "timeframes": ["H1", "D1"]},
    "core_component_weights": {
        "ma_h1": 0.18, "ma_d1": 0.18, "ma_w1": 0.14, "ma_m1": 0.10,
        "pivot": 0.18, "boll_h1": 0.12, "boll_d1": 0.10,
    },
}

SUPPLEMENT_FEATURES = (
    "ma_slope_consensus",
    "ma_compression_breakout",
    "momentum_acceleration",
    "macd_mtf",
    "rsi_mtf",
    "bb_trend",
    "bb_mean_reversion",
    "timeframe_conflict_reversal",
)

DEFAULT_SETUPS: dict[str, dict[str, Any]] = {
    "X-BASE": {
        "label": "Balanced Core + Belief",
        "core_weight": 0.70, "belief_weight": 0.20, "supplement_weight": 0.10,
        "supplement_weights": {
            "ma_slope_consensus": 0.28, "ma_compression_breakout": 0.12,
            "momentum_acceleration": 0.20, "macd_mtf": 0.16, "rsi_mtf": 0.08,
            "bb_trend": 0.10, "bb_mean_reversion": 0.03,
            "timeframe_conflict_reversal": 0.03,
        },
        "origin": "frozen_baseline",
    },
    "X-TREND": {
        "label": "Trend Continuation",
        "core_weight": 0.64, "belief_weight": 0.16, "supplement_weight": 0.20,
        "supplement_weights": {
            "ma_slope_consensus": 0.30, "ma_compression_breakout": 0.18,
            "momentum_acceleration": 0.20, "macd_mtf": 0.16, "rsi_mtf": 0.04,
            "bb_trend": 0.10, "bb_mean_reversion": 0.00,
            "timeframe_conflict_reversal": 0.02,
        },
        "origin": "frozen_challenger",
    },
    "X-REV": {
        "label": "Stretch / Mean Reversion",
        "core_weight": 0.58, "belief_weight": 0.18, "supplement_weight": 0.24,
        "supplement_weights": {
            "ma_slope_consensus": 0.10, "ma_compression_breakout": 0.04,
            "momentum_acceleration": 0.06, "macd_mtf": 0.06, "rsi_mtf": 0.18,
            "bb_trend": 0.02, "bb_mean_reversion": 0.40,
            "timeframe_conflict_reversal": 0.14,
        },
        "origin": "frozen_challenger",
    },
    "X-BELIEF": {
        "label": "Belief-weighted",
        "core_weight": 0.58, "belief_weight": 0.30, "supplement_weight": 0.12,
        "supplement_weights": {
            "ma_slope_consensus": 0.20, "ma_compression_breakout": 0.10,
            "momentum_acceleration": 0.18, "macd_mtf": 0.16, "rsi_mtf": 0.08,
            "bb_trend": 0.10, "bb_mean_reversion": 0.10,
            "timeframe_conflict_reversal": 0.08,
        },
        "origin": "frozen_challenger",
    },
}


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _iso(value: datetime | None = None) -> str:
    dt = (value or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any, digits: int = 6) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _resample(rows: Sequence[Bar], mode: str) -> list[Bar]:
    groups: dict[tuple[int, ...], list[Bar]] = {}
    for row in sorted(rows, key=lambda x: x.timestamp):
        dt = row.timestamp.astimezone(timezone.utc)
        if mode == "W1":
            iso = dt.isocalendar()
            key = (iso.year, iso.week)
        elif mode == "M1":
            key = (dt.year, dt.month)
        else:
            raise ValueError(mode)
        groups.setdefault(key, []).append(row)
    out: list[Bar] = []
    for group in groups.values():
        first, last = group[0], group[-1]
        highs = [float(x.high if x.high is not None else x.close) for x in group]
        lows = [float(x.low if x.low is not None else x.close) for x in group]
        out.append(Bar(
            timestamp=last.timestamp,
            open=float(first.open if first.open is not None else first.close),
            high=max(highs), low=min(lows), close=float(last.close), volume=None,
        ))
    return out


def _as_of(rows: Sequence[Bar], observed_at: datetime) -> list[Bar]:
    return [x for x in sorted(rows, key=lambda b: b.timestamp) if x.timestamp <= observed_at]


def _ma_slope(rows: Sequence[Bar], window: int = 30, lag: int = 5) -> float:
    if len(rows) < window + lag + 2:
        return 0.0
    current = float(abc._sma(rows, window))
    previous = float(abc._sma(rows[:-lag], window))
    atr = float(abc._atr(rows, 14) or 0.0)
    if atr <= 0:
        return 0.0
    return _clamp((current - previous) / (atr * max(1.0, lag) * 0.08))


def _ma_dispersion(rows: Sequence[Bar]) -> float:
    if len(rows) < 200:
        return 1.0
    atr = float(abc._atr(rows, 14) or 0.0)
    if atr <= 0:
        return 1.0
    vals = [float(abc._sma(rows, w)) for w in (30, 60, 100, 200)]
    return min(1.0, (max(vals) - min(vals)) / (2.5 * atr))


def _fixed_core(frames: Mapping[str, Sequence[Bar]], observed_at: datetime, reference: float) -> dict[str, Any]:
    scoped = {tf: _as_of(frames[tf], observed_at) for tf in ("H1", "D1", "W1", "M1")}
    for tf, rows in scoped.items():
        if len(rows) < 205:
            raise ValueError(f"EURUSD X requires >=205 {tf} bars")
    ma = {tf: abc._ma_structure(scoped[tf]) for tf in scoped}
    pivot = abc._classic_pivots(scoped["D1"], observed_at, reference)
    boll_h1 = abc._bollinger(scoped["H1"], window=20, stddevs=2.5)
    boll_d1 = abc._bollinger(scoped["D1"], window=20, stddevs=2.5)
    components = {
        "ma_h1": float(ma["H1"]["score"]), "ma_d1": float(ma["D1"]["score"]),
        "ma_w1": float(ma["W1"]["score"]), "ma_m1": float(ma["M1"]["score"]),
        "pivot": float(pivot["score"]),
        "boll_h1": float(boll_h1["score"]), "boll_d1": float(boll_d1["score"]),
    }
    weights = FIXED_CORE["core_component_weights"]
    signed = _clamp(sum(float(weights[k]) * components[k] for k in components))
    return {
        "signed_score": round(signed, 6),
        "components": {k: round(v, 6) for k, v in components.items()},
        "ma": ma, "pivot": pivot,
        "bollinger": {"H1": boll_h1, "D1": boll_d1},
        "contract": FIXED_CORE,
    }


def _supplementary(frames: Mapping[str, Sequence[Bar]], observed_at: datetime, core: Mapping[str, Any]) -> dict[str, float]:
    rows = {tf: _as_of(frames[tf], observed_at) for tf in ("H1", "D1", "W1", "M1")}
    slopes = {tf: _ma_slope(rows[tf]) for tf in rows}
    slope_consensus = _clamp(0.38 * slopes["H1"] + 0.30 * slopes["D1"] + 0.20 * slopes["W1"] + 0.12 * slopes["M1"])
    dispersion = 0.55 * _ma_dispersion(rows["H1"]) + 0.45 * _ma_dispersion(rows["D1"])
    ma_direction = _clamp((float(core["ma"]["H1"]["score"]) + float(core["ma"]["D1"]["score"])) / 2.0)
    compression_breakout = _clamp(ma_direction * (1.0 - dispersion))

    r3 = abc._return(rows["H1"], 3) or 0.0
    r12 = abc._return(rows["H1"], 12) or 0.0
    r24 = abc._return(rows["H1"], 24) or 0.0
    momentum_acceleration = _clamp((float(r3) - float(r12) / 4.0) / 0.0015 + (float(r12) - float(r24) / 2.0) / 0.0030)

    macd_h1 = float(abc._macd(rows["H1"])["score"])
    macd_d1 = float(abc._macd(rows["D1"])["score"])
    macd_mtf = _clamp(0.65 * macd_h1 + 0.35 * macd_d1)

    rsi_h1 = float(abc._rsi(rows["H1"], 14))
    rsi_d1 = float(abc._rsi(rows["D1"], 14))
    rsi_mtf = _clamp(0.65 * ((rsi_h1 - 50.0) / 22.0) + 0.35 * ((rsi_d1 - 50.0) / 22.0))

    boll_h1 = core["bollinger"]["H1"]
    boll_d1 = core["bollinger"]["D1"]
    bb_trend = _clamp(0.65 * float(boll_h1["score"]) + 0.35 * float(boll_d1["score"]))
    stretch = 0.65 * abs(float(boll_h1["score"])) + 0.35 * abs(float(boll_d1["score"]))
    bb_mean_reversion = _clamp(-bb_trend * max(0.0, (stretch - 0.55) / 0.45))

    fast = _clamp((float(core["ma"]["H1"]["score"]) + float(core["ma"]["D1"]["score"])) / 2.0)
    slow = _clamp((float(core["ma"]["W1"]["score"]) + float(core["ma"]["M1"]["score"])) / 2.0)
    conflict = fast * slow < 0 and abs(fast) > 0.30 and abs(slow) > 0.30
    conflict_reversal = _clamp(-fast * min(1.0, abs(slow))) if conflict else 0.0

    return {
        "ma_slope_consensus": round(slope_consensus, 6),
        "ma_compression_breakout": round(compression_breakout, 6),
        "momentum_acceleration": round(momentum_acceleration, 6),
        "macd_mtf": round(macd_mtf, 6),
        "rsi_mtf": round(rsi_mtf, 6),
        "bb_trend": round(bb_trend, 6),
        "bb_mean_reversion": round(bb_mean_reversion, 6),
        "timeframe_conflict_reversal": round(conflict_reversal, 6),
    }


def _normalize_weights(weights: Mapping[str, Any]) -> dict[str, float]:
    raw = {k: float(weights.get(k) or 0.0) for k in SUPPLEMENT_FEATURES}
    total = sum(abs(v) for v in raw.values())
    if total <= 1e-12:
        return {k: 0.0 for k in SUPPLEMENT_FEATURES}
    return {k: v / total for k, v in raw.items()}


def _predict(setup: Mapping[str, Any], core_score: float, belief_score: float | None, features: Mapping[str, float]) -> dict[str, Any]:
    supp_weights = _normalize_weights(setup.get("supplement_weights") or {})
    supplementary = _clamp(sum(supp_weights[k] * float(features.get(k) or 0.0) for k in SUPPLEMENT_FEATURES))
    cw = float(setup.get("core_weight") or 0.0)
    bw = float(setup.get("belief_weight") or 0.0) if belief_score is not None else 0.0
    sw = float(setup.get("supplement_weight") or 0.0)
    denom = cw + bw + sw
    if denom <= 0:
        raise ValueError("setup has no usable weights")
    signed = _clamp((cw * core_score + bw * float(belief_score or 0.0) + sw * supplementary) / denom)
    probability_long = max(0.05, min(0.95, 0.5 + 0.45 * signed))
    if probability_long >= 0.57:
        direction = "LONG"
    elif probability_long <= 0.43:
        direction = "SHORT"
    else:
        direction = "FLAT"
    return {
        "signed_score": round(signed, 6),
        "score": round(50.0 + 50.0 * signed, 2),
        "probability_long": round(probability_long, 6),
        "direction": direction,
        "confidence": round(abs(probability_long - 0.5) * 2.0, 6) if direction != "FLAT" else 0.0,
        "supplementary_score": round(supplementary, 6),
    }


def _initial_state(now: datetime | None = None) -> dict[str, Any]:
    created = _iso(now)
    setups = {k: {**v, "created_at": created, "prospective_from_capture": 0} for k, v in DEFAULT_SETUPS.items()}
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "mode": MODE,
        "created_at": created,
        "updated_at": created,
        "fixed_core_sha256": _canonical_sha(FIXED_CORE),
        "setups": setups,
        "setup_cooldowns": {},
        "champion_setup_id": "X-BASE",
        "last_best_setup_id": "X-BASE",
        "active_challenger_id": "X-TREND",
        "captures": [],
        "calibration_events": [],
        "discoveries": [],
        "last_discovery_bucket": 0,
        "authority": {
            "production_execution": False,
            "active_daily_engine_writeback": False,
            "abc_writeback": False,
            "belief_writeback": False,
            "automatic_production_promotion": False,
            "x_local_shadow_promotion": True,
            "x_local_rollback": True,
        },
    }


def _decision_fingerprint(capture: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "capture_id": capture["capture_id"],
        "market_observed_at": capture["market_observed_at"],
        "reference_price": capture["reference_price"],
        "fixed_core": capture["fixed_core"],
        "supplementary_features": capture["supplementary_features"],
        "belief": capture["belief"],
        "predictions": capture["predictions"],
        "champion_setup_id": capture["champion_setup_id"],
        "active_challenger_id": capture.get("active_challenger_id"),
        "outcomes": {k: {"target_at": v["target_at"]} for k, v in capture["outcomes"].items()},
        "research_boundary": capture["research_boundary"],
    }


def _build_capture(state: Mapping[str, Any], frames: Mapping[str, Sequence[Bar]], belief_payload: Mapping[str, Any] | None, now: datetime) -> dict[str, Any]:
    h1 = list(frames["H1"])
    observed_at = h1[-1].timestamp.astimezone(timezone.utc)
    reference = float(h1[-1].close)
    core = _fixed_core(frames, observed_at, reference)
    features = _supplementary(frames, observed_at, core)
    belief = abc.belief_snapshot(belief_payload, observed_at)
    belief_signed = float(belief["signed_score"]) if belief.get("available") and belief.get("signed_score") is not None else None
    predictions = {
        setup_id: _predict(setup, float(core["signed_score"]), belief_signed, features)
        for setup_id, setup in (state.get("setups") or {}).items()
    }
    capture_id = "eurusd-x-" + _canonical_sha([_iso(observed_at), round(reference, 6), sorted(predictions)])[:18]
    capture = {
        "capture_id": capture_id,
        "engine_version": ENGINE_VERSION,
        "captured_at": _iso(now),
        "market_observed_at": _iso(observed_at),
        "reference_price": round(reference, 6),
        "fixed_core": core,
        "supplementary_features": features,
        "belief": {
            "available": bool(belief.get("available")),
            "signed_score": _number(belief.get("signed_score"), 6),
            "score": _number(belief.get("score"), 2),
            "direction": belief.get("direction") if belief.get("available") else "UNAVAILABLE",
            "confidence": _number(belief.get("confidence"), 6),
            "reason": belief.get("reason"),
        },
        "predictions": predictions,
        "champion_setup_id": state.get("champion_setup_id") or "X-BASE",
        "active_challenger_id": state.get("active_challenger_id"),
        "outcomes": {
            "4h": {"target_at": _iso(observed_at + timedelta(hours=PRIMARY_HORIZON_HOURS)), "resolved": None},
            "24h": {"target_at": _iso(observed_at + timedelta(hours=SECONDARY_HORIZON_HOURS)), "resolved": None},
        },
        "research_boundary": {
            "shadow_only": True,
            "production_execution": False,
            "active_daily_engine_writeback": False,
            "abc_writeback": False,
            "belief_writeback": False,
            "historical_backfill": False,
        },
    }
    capture["decision_sha256"] = _canonical_sha(_decision_fingerprint(capture))
    return capture


def _settle(state: dict[str, Any], h1_rows: Sequence[Bar]) -> int:
    rows = sorted(h1_rows, key=lambda x: x.timestamp)
    changed = 0
    for capture in state.get("captures") or []:
        expected = _canonical_sha(_decision_fingerprint(capture))
        if capture.get("decision_sha256") != expected:
            raise ValueError(f"EURUSD X frozen decision fingerprint mismatch: {capture.get('capture_id')}")
        ref = float(capture["reference_price"])
        for horizon in capture.get("outcomes", {}).values():
            if horizon.get("resolved") is not None:
                continue
            target = _parse(horizon["target_at"])
            bar = next((x for x in rows if x.timestamp.astimezone(timezone.utc) >= target), None)
            if bar is None:
                continue
            ret = float(bar.close) / ref - 1.0
            horizon["resolved"] = {
                "resolved_at": _iso(bar.timestamp),
                "price": round(float(bar.close), 6),
                "return_fraction": round(ret, 8),
                "return_bps": round(ret * 10000.0, 4),
                "up": ret > 0,
            }
            changed += 1
    return changed


def _metric_for_setup(state: Mapping[str, Any], setup_id: str, *, recent: int | None = None) -> dict[str, Any]:
    rows: list[tuple[float, str, float, bool]] = []
    for capture in state.get("captures") or []:
        outcome = ((capture.get("outcomes") or {}).get("4h") or {}).get("resolved")
        pred = (capture.get("predictions") or {}).get(setup_id)
        if not isinstance(outcome, Mapping) or not isinstance(pred, Mapping):
            continue
        p = float(pred["probability_long"])
        y = 1.0 if bool(outcome.get("up")) else 0.0
        direction = str(pred.get("direction") or "FLAT")
        ret_bps = float(outcome.get("return_bps") or 0.0)
        rows.append(((p - y) ** 2, direction, ret_bps, bool(outcome.get("up"))))
    if recent is not None:
        rows = rows[-recent:]
    if not rows:
        return {"n": 0, "brier": None, "hit_rate": None, "mean_signed_return_bps": None, "signal_n": 0}
    brier = sum(x[0] for x in rows) / len(rows)
    signals = [x for x in rows if x[1] in {"LONG", "SHORT"}]
    hits = [((x[1] == "LONG" and x[3]) or (x[1] == "SHORT" and not x[3])) for x in signals]
    signed = [(x[2] if x[1] == "LONG" else -x[2]) for x in signals]
    return {
        "n": len(rows),
        "brier": round(brier, 6),
        "signal_n": len(signals),
        "hit_rate": None if not signals else round(sum(hits) / len(hits), 6),
        "mean_signed_return_bps": None if not signed else round(sum(signed) / len(signed), 4),
    }


def _common_metrics(state: Mapping[str, Any], a: str, b: str, recent: int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    subset = {**state, "captures": [
        c for c in state.get("captures") or []
        if a in (c.get("predictions") or {}) and b in (c.get("predictions") or {})
        and isinstance((((c.get("outcomes") or {}).get("4h") or {}).get("resolved")), Mapping)
    ]}
    if recent is not None:
        subset["captures"] = subset["captures"][-recent:]
    return _metric_for_setup(subset, a), _metric_for_setup(subset, b)


def _discover_anomaly_setup(state: dict[str, Any], now: datetime) -> str | None:
    resolved = [c for c in state.get("captures") or [] if isinstance((((c.get("outcomes") or {}).get("4h") or {}).get("resolved")), Mapping)]
    n = len(resolved)
    if n < ANOMALY_MIN_SAMPLE:
        return None
    bucket = 1 + (n - ANOMALY_MIN_SAMPLE) // ANOMALY_STEP
    if bucket <= int(state.get("last_discovery_bucket") or 0):
        return None
    if sum(1 for x in state.get("setups", {}) if x.startswith("X-AUTO-")) >= MAX_AUTO_SETUPS:
        state["last_discovery_bucket"] = bucket
        return None

    corr: dict[str, float] = {}
    for feature in SUPPLEMENT_FEATURES:
        pairs = []
        for capture in resolved:
            value = float((capture.get("supplementary_features") or {}).get(feature) or 0.0)
            outcome = ((capture.get("outcomes") or {}).get("4h") or {}).get("resolved") or {}
            y = 1.0 if bool(outcome.get("up")) else -1.0
            pairs.append(value * y)
        corr[feature] = sum(pairs) / len(pairs) if pairs else 0.0
    ranked = sorted(corr.items(), key=lambda kv: abs(kv[1]), reverse=True)[:4]
    if not ranked or abs(ranked[0][1]) < 0.04:
        state["last_discovery_bucket"] = bucket
        state.setdefault("discoveries", []).append({
            "at": _iso(now), "sample": n, "status": "NO_MATERIAL_ANOMALY",
            "correlations": {k: round(v, 6) for k, v in ranked},
        })
        return None

    normalized = _normalize_weights({k: v for k, v in ranked})
    setup_id = f"X-AUTO-{bucket:02d}"
    state["setups"][setup_id] = {
        "label": f"Auto anomaly {bucket:02d}",
        "core_weight": 0.60, "belief_weight": 0.15, "supplement_weight": 0.25,
        "supplement_weights": normalized,
        "origin": "prospective_anomaly_hunter",
        "created_at": _iso(now),
        "prospective_from_capture": len(state.get("captures") or []),
        "discovery_sample": n,
        "discovery_correlations": {k: round(v, 6) for k, v in ranked},
    }
    state.setdefault("discoveries", []).append({
        "at": _iso(now), "sample": n, "status": "NEW_PROSPECTIVE_CHALLENGER",
        "setup_id": setup_id, "correlations": {k: round(v, 6) for k, v in ranked},
    })
    state["last_discovery_bucket"] = bucket
    return setup_id


def _recalibrate(state: dict[str, Any], now: datetime) -> None:
    champion = str(state.get("champion_setup_id") or "X-BASE")
    previous_best = str(state.get("last_best_setup_id") or champion)
    resolved_n = sum(1 for c in state.get("captures") or [] if isinstance((((c.get("outcomes") or {}).get("4h") or {}).get("resolved")), Mapping))
    cooldowns = state.setdefault("setup_cooldowns", {})
    event: dict[str, Any] = {"at": _iso(now), "champion_before": champion, "action": "KEEP"}

    if previous_best != champion:
        champ_recent, prev_recent = _common_metrics(state, champion, previous_best, ROLLBACK_WINDOW)
        if champ_recent["n"] >= ROLLBACK_WINDOW and prev_recent["n"] >= ROLLBACK_WINDOW:
            champ_brier = float(champ_recent["brier"])
            prev_brier = float(prev_recent["brier"])
            champ_edge = champ_recent.get("mean_signed_return_bps")
            if (champ_brier >= 0.27 or (champ_edge is not None and float(champ_edge) < 0.0)) and prev_brier + 0.01 < champ_brier:
                state["champion_setup_id"] = previous_best
                state["last_best_setup_id"] = previous_best
                cooldowns[champion] = resolved_n + ROLLBACK_WINDOW
                event.update({
                    "action": "ROLLBACK", "champion_after": previous_best,
                    "reason": "recent champion degradation vs last proven setup",
                    "champion_recent": champ_recent, "rollback_recent": prev_recent,
                    "cooldown_failed_setup_until_resolved_n": cooldowns[champion],
                })
                state.setdefault("calibration_events", []).append(event)
                return

    candidates = []
    for setup_id in (state.get("setups") or {}):
        if setup_id == champion:
            continue
        if int(cooldowns.get(setup_id) or 0) > resolved_n:
            continue
        cm, xm = _common_metrics(state, champion, setup_id)
        if cm["n"] < MIN_COMMON_EVAL or xm["n"] < MIN_COMMON_EVAL:
            continue
        if cm["brier"] is None or xm["brier"] is None:
            continue
        hit_ok = xm["hit_rate"] is None or cm["hit_rate"] is None or float(xm["hit_rate"]) >= float(cm["hit_rate"]) - 0.05
        edge_ok = xm["mean_signed_return_bps"] is None or cm["mean_signed_return_bps"] is None or float(xm["mean_signed_return_bps"]) >= float(cm["mean_signed_return_bps"])
        if float(xm["brier"]) + 0.01 <= float(cm["brier"]) and hit_ok and edge_ok:
            candidates.append((float(xm["brier"]), setup_id, cm, xm))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        _, winner, champ_metric, winner_metric = candidates[0]
        state["last_best_setup_id"] = champion
        state["champion_setup_id"] = winner
        state["active_challenger_id"] = champion
        event.update({
            "action": "PROMOTE_X_SHADOW", "champion_after": winner,
            "reason": "prospective challenger passed common-sample gate",
            "champion_metric": champ_metric, "challenger_metric": winner_metric,
        })
    else:
        ranked = []
        for setup_id in (state.get("setups") or {}):
            if setup_id == champion:
                continue
            metric = _metric_for_setup(state, setup_id)
            if metric["n"]:
                ranked.append((float(metric["brier"]) if metric["brier"] is not None else 999.0, setup_id))
        if ranked:
            ranked.sort()
            state["active_challenger_id"] = ranked[0][1]
        event["champion_after"] = champion

    state.setdefault("calibration_events", []).append(event)
    state["calibration_events"] = state["calibration_events"][-120:]


def _latest_prediction(state: Mapping[str, Any]) -> dict[str, Any] | None:
    captures = state.get("captures") or []
    if not captures:
        return None
    latest = captures[-1]
    champion = str(state.get("champion_setup_id") or latest.get("champion_setup_id") or "X-BASE")
    pred = (latest.get("predictions") or {}).get(champion)
    if pred is None:
        champion = str(latest.get("champion_setup_id") or "X-BASE")
        pred = (latest.get("predictions") or {}).get(champion)
    return None if pred is None else {
        "setup_id": champion, **pred,
        "market_observed_at": latest.get("market_observed_at"),
        "reference_price": latest.get("reference_price"),
    }


def _report(state: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    metrics = {setup_id: _metric_for_setup(state, setup_id) for setup_id in (state.get("setups") or {})}
    champion = str(state.get("champion_setup_id") or "X-BASE")
    return {
        "schema_version": "eurusd-x-shadow-report-v1",
        "engine_version": ENGINE_VERSION,
        "mode": MODE,
        "generated_at": _iso(now),
        "fixed_core": FIXED_CORE,
        "capture_count": len(state.get("captures") or []),
        "resolved_4h": sum(1 for c in state.get("captures") or [] if isinstance((((c.get("outcomes") or {}).get("4h") or {}).get("resolved")), Mapping)),
        "champion_setup_id": champion,
        "last_best_setup_id": state.get("last_best_setup_id"),
        "active_challenger_id": state.get("active_challenger_id"),
        "latest_prediction": _latest_prediction(state),
        "metrics": metrics,
        "setups": state.get("setups") or {},
        "recent_calibration_events": list(state.get("calibration_events") or [])[-12:],
        "recent_discoveries": list(state.get("discoveries") or [])[-8:],
        "calibration_policy": {
            "scheduler": "hourly_weekdays",
            "recalibrate_after_each_resolved_primary_outcome": True,
            "primary_horizon_hours": PRIMARY_HORIZON_HOURS,
            "minimum_common_eval": MIN_COMMON_EVAL,
            "rollback_window": ROLLBACK_WINDOW,
            "automatic_x_shadow_promotion": True,
            "automatic_x_shadow_rollback": True,
            "production_promotion": False,
        },
        "authority": state.get("authority") or {},
    }


def build_public(report: Mapping[str, Any], *, market_status: str = "LIVE") -> dict[str, Any]:
    authority = report.get("authority") or {}
    if authority.get("production_execution") is not False or authority.get("active_daily_engine_writeback") is not False:
        raise ValueError("EURUSD X public projection requires zero production authority")
    setups = report.get("setups") or {}
    metrics = report.get("metrics") or {}
    rows = []
    for setup_id, setup in setups.items():
        m = metrics.get(setup_id) or {}
        rows.append({
            "setup_id": setup_id,
            "label": setup.get("label"),
            "origin": setup.get("origin"),
            "n": int(m.get("n") or 0),
            "brier": _number(m.get("brier"), 6),
            "signal_n": int(m.get("signal_n") or 0),
            "hit_rate": _number(m.get("hit_rate"), 6),
            "mean_signed_return_bps": _number(m.get("mean_signed_return_bps"), 4),
        })
    rows.sort(key=lambda x: (x["setup_id"] != report.get("champion_setup_id"), x["brier"] if x["brier"] is not None else 999.0))
    return {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "generated_at": report.get("generated_at"),
        "engine_version": ENGINE_VERSION,
        "mode": "LIVE_SHADOW",
        "market_status": market_status,
        "title": "EURUSD X",
        "champion_setup_id": report.get("champion_setup_id"),
        "last_best_setup_id": report.get("last_best_setup_id"),
        "active_challenger_id": report.get("active_challenger_id"),
        "capture_count": int(report.get("capture_count") or 0),
        "resolved_4h": int(report.get("resolved_4h") or 0),
        "latest_prediction": report.get("latest_prediction"),
        "fixed_core": FIXED_CORE,
        "calibration_policy": report.get("calibration_policy") or {},
        "setups": rows[:12],
        "recent_calibration_events": report.get("recent_calibration_events") or [],
        "recent_discoveries": report.get("recent_discoveries") or [],
        "public_boundary": {
            "shadow_only": True, "production_execution": False,
            "active_daily_engine_writeback": False, "abc_writeback": False,
            "belief_writeback": False, "raw_belief_evidence_exposed": False,
        },
    }


def _fetch_frames(client: YahooChartClient) -> dict[str, list[Bar]]:
    h1 = client.bars(SYMBOL, "2y", "1h")
    d1 = client.bars(SYMBOL, "max", "1d")
    return {"H1": h1, "D1": d1, "W1": _resample(d1, "W1"), "M1": _resample(d1, "M1")}


def run_cycle(state_dir: Path, belief_state: Path | None = None, now: datetime | None = None, client: YahooChartClient | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state_path = state_dir / STATE_FILENAME
    state = _load(state_path, None)
    if not isinstance(state, Mapping) or state.get("schema_version") != SCHEMA_VERSION:
        state = _initial_state(now)
    else:
        state = dict(state)

    state.setdefault("setups", {})
    state.setdefault("setup_cooldowns", {})
    for setup_id, setup in DEFAULT_SETUPS.items():
        state["setups"].setdefault(setup_id, {
            **setup, "created_at": state.get("created_at") or _iso(now), "prospective_from_capture": 0
        })

    frames = _fetch_frames(client or YahooChartClient())
    h1 = frames["H1"]
    settled = _settle(state, h1)
    if settled:
        _discover_anomaly_setup(state, now)
        _recalibrate(state, now)

    latest_market = h1[-1].timestamp.astimezone(timezone.utc)
    staleness_minutes = (now - latest_market).total_seconds() / 60.0
    market_status = "LIVE" if staleness_minutes <= MAX_H1_STALENESS_MINUTES else "WAITING_FRESH_MARKET"
    belief_payload = _load(belief_state, None) if belief_state else None
    existing_ids = {str(x.get("capture_id")) for x in state.get("captures") or []}

    if market_status == "LIVE":
        capture = _build_capture(state, frames, belief_payload, now)
        if capture["capture_id"] not in existing_ids:
            state.setdefault("captures", []).append(capture)

    state["captures"] = list(state.get("captures") or [])[-800:]
    state["updated_at"] = _iso(now)
    state["last_market_status"] = market_status
    state["last_market_observed_at"] = _iso(latest_market)
    validate_state(state)
    report = _report(state, now)
    public = build_public(report, market_status=market_status)
    _write(state_path, state)
    _write(state_dir / REPORT_FILENAME, report)
    return state, report, public


def validate_state(state: Mapping[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA_VERSION or state.get("mode") != MODE:
        raise ValueError("invalid EURUSD X state schema/mode")
    if state.get("fixed_core_sha256") != _canonical_sha(FIXED_CORE):
        raise ValueError("fixed technical core was mutated")
    authority = state.get("authority") or {}
    for key in ("production_execution", "active_daily_engine_writeback", "abc_writeback", "belief_writeback", "automatic_production_promotion"):
        if authority.get(key) is not False:
            raise ValueError(f"EURUSD X authority invariant violated: {key}")
    champion = state.get("champion_setup_id")
    if champion not in (state.get("setups") or {}):
        raise ValueError("EURUSD X champion setup missing")
    seen: set[str] = set()
    for capture in state.get("captures") or []:
        cid = str(capture.get("capture_id") or "")
        if not cid or cid in seen:
            raise ValueError("duplicate/missing EURUSD X capture_id")
        seen.add(cid)
        if capture.get("decision_sha256") != _canonical_sha(_decision_fingerprint(capture)):
            raise ValueError("EURUSD X immutable decision fingerprint mismatch")
        boundary = capture.get("research_boundary") or {}
        if boundary.get("historical_backfill") is not False or boundary.get("production_execution") is not False:
            raise ValueError("EURUSD X research boundary invalid")
        contract = ((capture.get("fixed_core") or {}).get("contract") or {})
        if contract != FIXED_CORE:
            raise ValueError("EURUSD X fixed core contract drift")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run EURUSD X adaptive shadow research cycle")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--belief-state")
    parser.add_argument("--public-output")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    if args.validate:
        state = _load(state_dir / STATE_FILENAME, {})
        validate_state(state)
        print("EURUSD_X_STATE_OK", len(state.get("captures") or []))
        return 0
    state, report, public = run_cycle(
        state_dir,
        Path(args.belief_state) if args.belief_state else None,
    )
    if args.public_output:
        _write(Path(args.public_output), public)
    print("EURUSD_X_CYCLE_OK", json.dumps({
        "captures": len(state.get("captures") or []),
        "resolved_4h": report.get("resolved_4h"),
        "champion": report.get("champion_setup_id"),
        "challenger": report.get("active_challenger_id"),
        "market_status": public.get("market_status"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
