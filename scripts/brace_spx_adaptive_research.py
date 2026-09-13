#!/usr/bin/env python3
"""BRACE-SPX unified adaptive research track.

This module belongs to the SAME BRACE-SPX platform as Generation 6. It does
not create a second production engine and it never mutates the frozen G6
candidate set. G6 remains the immutable out-of-sample validation reference.

The adaptive track may create research challengers after observing resolved
prospective evidence. Every challenger receives its own creation boundary;
evidence observed before that boundary is never credited to the challenger.
Promotion is never automatic: a successful challenger can only become a new
frozen generation (for example G7) after a human promotion review.

The sealed 2022-08-01..2026-07-31 holdout remains inaccessible. The adaptive
track uses only post-holdout data beginning at G6.SHADOW_START.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import brace_spx_generation6 as g6
import brace_spx_generation6_shadow as shadow_loop

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / "data" / "research" / "brace_spx_adaptive_state.json"
DEFAULT_PUBLIC = ROOT / "data" / "public" / "brace_spx_platform_public.json"
DEFAULT_G6_REPORT = ROOT / "data" / "research" / "brace_spx_generation6_report.json"
DEFAULT_G6_SHADOW = ROOT / "data" / "shadow" / "brace_spx_generation6_shadow.json"

PLATFORM_ID = "brace-spx"
TRACK_ID = "brace-spx-adaptive-research-v1"
SCHEMA_VERSION = "1.0.0"
CHECKPOINTS = (20, 35, 50, 70)
MIN_SELECTION_N = 20
MIN_PROMOTION_N = 70
MAX_CHALLENGERS = 8
FAMILY_ORDER = tuple(g6.SOURCE_FAMILIES)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    clipped = {family: max(0.05, min(0.70, float(weights.get(family, 0.0)))) for family in FAMILY_ORDER}
    total = sum(clipped.values()) or 1.0
    return {family: value / total for family, value in clipped.items()}


def _initial_challengers(activation_market_date: str) -> list[dict[str, Any]]:
    # These are hypotheses, not winners. Their parameters stay private on the
    # research branch; the public artifact exposes only IDs and evidence.
    specs = [
        ("G6-R-C01", "trend_anchor_macro_veto", {"price_trend": 0.55, "rates": 0.15, "liquidity": 0.15, "options_vix": 0.15}),
        ("G6-R-C02", "balanced_orthogonal", {"price_trend": 0.40, "rates": 0.20, "liquidity": 0.20, "options_vix": 0.20}),
        ("G6-R-C03", "robust_family_median", {"price_trend": 0.25, "rates": 0.25, "liquidity": 0.25, "options_vix": 0.25}),
        ("G6-R-C04", "trend_credit_vol_confirmation", {"price_trend": 0.45, "rates": 0.10, "liquidity": 0.25, "options_vix": 0.20}),
    ]
    result: list[dict[str, Any]] = []
    for candidate_id, rule, weights in specs:
        result.append({
            "candidate_id": candidate_id,
            "rule": rule,
            "weights": _normalize_weights(weights),
            "origin": "initial_g6_diagnostic_lessons",
            "created_market_date": activation_market_date,
            "status": "ACTIVE_RESEARCH",
        })
    return result


def _initial_state(g6_report: dict[str, Any], g6_shadow: dict[str, Any]) -> dict[str, Any]:
    activation_market_date = str(g6_shadow.get("latest_market_date") or g6.SHADOW_START)
    activation_observations = int(g6_shadow.get("observations_collected") or 0)
    rank = (g6_report.get("rank_stability") or {}).get("median_pairwise_fold_rank_correlation")
    raw = g6_report.get("raw_best_diagnostic_only") or {}
    trend = (g6_report.get("baselines") or {}).get("trend_200d_weekly") or {}
    lesson = {
        "event_id": "L0001",
        "event_type": "INITIALIZED_FROM_G6_FAILURE_DIAGNOSTICS",
        "at": _utc_now(),
        "market_date": activation_market_date,
        "evidence": {
            "g6_strict_gate_passed": bool(g6_report.get("strict_gate_passed")),
            "median_fold_rank_correlation": rank,
            "g6_best_sharpe": (raw.get("metrics") or {}).get("sharpe_excess"),
            "trend_200d_sharpe": trend.get("sharpe_excess"),
        },
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "platform_id": PLATFORM_ID,
        "track_id": TRACK_ID,
        "frozen_generation_id": g6.GENERATION_ID,
        "frozen_candidate_signature": g6.candidate_signature(),
        "activated_at": _utc_now(),
        "activation_market_date": activation_market_date,
        "activation_observations": activation_observations,
        "research_cycles": 0,
        "completed_checkpoints": [],
        "challengers": _initial_challengers(activation_market_date),
        "learning_events": [lesson],
        "governance": {
            "mutate_frozen_g6": False,
            "sealed_holdout_access": False,
            "automatic_promotion": False,
            "live_orders": False,
            "trade_execution": False,
            "challenger_parameters_private": True,
        },
    }


def _weighted_score(signals: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    rule = str(spec.get("rule") or "balanced_orthogonal")
    weights = _normalize_weights(spec.get("weights") or {})
    weighted = sum(signals[family] * weights[family] for family in FAMILY_ORDER).clip(-1.0, 1.0)

    if rule == "robust_family_median":
        return signals[list(FAMILY_ORDER)].median(axis=1).clip(-1.0, 1.0)

    if rule == "trend_anchor_macro_veto":
        defensive = signals[["rates", "liquidity", "options_vix"]].min(axis=1)
        return weighted.where(defensive > -0.60, np.minimum(weighted, -0.15)).clip(-1.0, 1.0)

    if rule == "trend_credit_vol_confirmation":
        confirmation = signals[["liquidity", "options_vix"]].mean(axis=1)
        result = weighted.copy()
        weak = (signals["price_trend"] > 0.15) & (confirmation < -0.25)
        result.loc[weak] = np.minimum(result.loc[weak], 0.0)
        return result.clip(-1.0, 1.0)

    return weighted


def _score_to_exposure(score: pd.Series) -> pd.Series:
    levels = np.select(
        [score <= -0.45, score <= -0.15, score < 0.15, score < 0.45],
        [-1.0, -0.50, 0.0, 0.50],
        default=1.0,
    )
    return pd.Series(levels, index=score.index, dtype=float)


def _candidate_target(frame: pd.DataFrame, signals: pd.DataFrame, spec: dict[str, Any]) -> tuple[pd.Series, pd.Series]:
    score = _weighted_score(signals, spec)
    desired = _score_to_exposure(score)
    weekly = desired.where(frame.index.dayofweek == 4).ffill().fillna(0.0)
    shock = (
        (signals["liquidity"] <= -0.65)
        | (signals["options_vix"] <= -0.65)
        | (frame["spy_vol_20"] >= 0.40)
    )
    exposure = weekly.copy()
    exposure.loc[shock] = np.minimum(exposure.loc[shock], 0.0)
    return exposure.clip(-1.0, 1.0), score


def _drawdown(returns: pd.Series) -> float | None:
    returns = pd.to_numeric(returns, errors="coerce").dropna()
    if returns.empty:
        return None
    equity = (1.0 + returns).cumprod()
    dd = equity / equity.cummax() - 1.0
    return float(dd.min())


def _metrics(returns: pd.Series, risk_free: pd.Series, exposure: pd.Series) -> dict[str, Any]:
    joined = pd.concat([
        pd.to_numeric(returns, errors="coerce").rename("ret"),
        pd.to_numeric(risk_free, errors="coerce").rename("rf"),
        pd.to_numeric(exposure, errors="coerce").rename("exposure"),
    ], axis=1).dropna(subset=["ret"])
    n = int(len(joined))
    if n == 0:
        return {"prospective_n": 0, "active_n": 0, "cumulative_return": None, "sharpe_excess": None, "max_drawdown": None, "hit_rate_active": None}
    ret = joined["ret"]
    rf = joined["rf"].fillna(0.0)
    excess = ret - rf
    std = float(excess.std(ddof=1)) if n > 1 else 0.0
    sharpe = float(excess.mean() / std * math.sqrt(252.0)) if std > 1e-12 else None
    active = joined["exposure"].abs() > 1e-12
    hit = float((ret[active] > 0).mean()) if bool(active.any()) else None
    return {
        "prospective_n": n,
        "active_n": int(active.sum()),
        "cumulative_return": float((1.0 + ret).prod() - 1.0),
        "sharpe_excess": sharpe,
        "max_drawdown": _drawdown(ret),
        "hit_rate_active": hit,
    }


def _family_ic(signals: pd.DataFrame, frame: pd.DataFrame, start_date: str) -> dict[str, float | None]:
    future_return = frame["asset_return"].shift(-1)
    mask = signals.index > pd.Timestamp(start_date)
    result: dict[str, float | None] = {}
    for family in FAMILY_ORDER:
        pair = pd.concat([signals.loc[mask, family], future_return.loc[mask]], axis=1).dropna()
        corr = pair.iloc[:, 0].corr(pair.iloc[:, 1]) if len(pair) >= 10 else np.nan
        result[family] = float(corr) if pd.notna(corr) else None
    return result


def _selection_score(metrics: dict[str, Any]) -> float:
    sharpe = metrics.get("sharpe_excess")
    drawdown = metrics.get("max_drawdown")
    cumulative = metrics.get("cumulative_return")
    if metrics.get("prospective_n", 0) < MIN_SELECTION_N or sharpe is None or cumulative is None:
        return -1e9
    dd_penalty = abs(float(drawdown or 0.0))
    return float(sharpe) + 0.35 * float(cumulative) - 0.20 * dd_penalty


def _spawn_adaptive_challenger(state: dict[str, Any], parent: dict[str, Any], family_ic: dict[str, float | None], checkpoint: int, market_date: str) -> dict[str, Any]:
    parent_weights = _normalize_weights(parent.get("weights") or {})
    raw: dict[str, float] = {}
    for family in FAMILY_ORDER:
        ic = family_ic.get(family)
        raw[family] = parent_weights[family] + 0.20 * float(ic or 0.0)
    weights = _normalize_weights(raw)
    next_number = 1 + max(int(item["candidate_id"].split("C")[-1]) for item in state["challengers"])
    return {
        "candidate_id": f"G6-R-C{next_number:02d}",
        "rule": parent.get("rule") or "balanced_orthogonal",
        "weights": weights,
        "origin": f"adaptive_checkpoint_{checkpoint}",
        "parent_candidate_id": parent["candidate_id"],
        "created_market_date": market_date,
        "status": "ACTIVE_RESEARCH",
    }


def _public_candidate(candidate: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate["candidate_id"],
        "rule": candidate.get("rule"),
        "origin": candidate.get("origin"),
        "parent_candidate_id": candidate.get("parent_candidate_id"),
        "created_market_date": candidate.get("created_market_date"),
        "status": candidate.get("status"),
        "prospective_n": metrics.get("prospective_n", 0),
        "active_n": metrics.get("active_n", 0),
        "cumulative_return": metrics.get("cumulative_return"),
        "sharpe_excess": metrics.get("sharpe_excess"),
        "max_drawdown": metrics.get("max_drawdown"),
        "hit_rate_active": metrics.get("hit_rate_active"),
    }


def run(state: dict[str, Any], prices: pd.DataFrame, g6_report: dict[str, Any], g6_shadow: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if state.get("frozen_candidate_signature") != g6.candidate_signature():
        raise RuntimeError("Frozen G6 signature changed; adaptive track must fail closed")
    if state.get("governance", {}).get("mutate_frozen_g6") is not False:
        raise RuntimeError("Adaptive state attempted to mutate frozen G6")
    if prices.empty:
        raise RuntimeError("Adaptive research received no post-holdout prices")
    if prices.index.min() < pd.Timestamp(g6.SHADOW_START):
        raise RuntimeError("Adaptive research entered the sealed holdout")

    state["research_cycles"] = int(state.get("research_cycles") or 0) + 1
    state["last_cycle_at"] = _utc_now()
    state["latest_market_date"] = prices.index.max().date().isoformat()
    state["post_holdout_observations"] = int(len(prices))

    frame = g6.build_features(prices, research_mode=False)
    signals = g6.signal_frame(frame)
    valid_idx = signals.dropna().index
    feature_ready = len(valid_idx) >= 2
    candidate_metrics: dict[str, dict[str, Any]] = {}
    candidate_returns: dict[str, pd.Series] = {}

    if feature_ready:
        frame_valid = frame.loc[valid_idx]
        signals_valid = signals.loc[valid_idx]
        for candidate in state["challengers"]:
            target, _ = _candidate_target(frame_valid, signals_valid, candidate)
            returns, _, applied = g6.a2s.portfolio_returns(frame_valid, target)
            created = pd.Timestamp(candidate["created_market_date"])
            eligible = returns.index > created
            candidate_returns[candidate["candidate_id"]] = returns.loc[eligible]
            candidate_metrics[candidate["candidate_id"]] = _metrics(
                returns.loc[eligible],
                frame_valid.loc[eligible, "risk_free_return"],
                applied.loc[eligible],
            )
        family_ic = _family_ic(signals_valid, frame_valid, state["activation_market_date"])
    else:
        for candidate in state["challengers"]:
            candidate_metrics[candidate["candidate_id"]] = _metrics(pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float))
        family_ic = {family: None for family in FAMILY_ORDER}

    initial_ids = {"G6-R-C01", "G6-R-C02", "G6-R-C03", "G6-R-C04"}
    initial_n = max((candidate_metrics.get(cid, {}).get("prospective_n", 0) for cid in initial_ids), default=0)
    completed = set(int(x) for x in state.get("completed_checkpoints", []))
    for checkpoint in CHECKPOINTS:
        if checkpoint in completed or initial_n < checkpoint or len(state["challengers"]) >= MAX_CHALLENGERS:
            continue
        eligible_specs = [c for c in state["challengers"] if candidate_metrics.get(c["candidate_id"], {}).get("prospective_n", 0) >= MIN_SELECTION_N]
        if not eligible_specs:
            break
        parent = max(eligible_specs, key=lambda c: _selection_score(candidate_metrics[c["candidate_id"]]))
        child = _spawn_adaptive_challenger(state, parent, family_ic, checkpoint, state["latest_market_date"])
        state["challengers"].append(child)
        state.setdefault("learning_events", []).append({
            "event_id": f"L{len(state.get('learning_events', [])) + 1:04d}",
            "event_type": "CHECKPOINT_CHALLENGER_CREATED",
            "at": _utc_now(),
            "market_date": state["latest_market_date"],
            "checkpoint_n": checkpoint,
            "parent_candidate_id": parent["candidate_id"],
            "new_candidate_id": child["candidate_id"],
            "family_ic": family_ic,
        })
        completed.add(checkpoint)
        candidate_metrics[child["candidate_id"]] = _metrics(pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float))
    state["completed_checkpoints"] = sorted(completed)

    ranked = [c for c in state["challengers"] if candidate_metrics.get(c["candidate_id"], {}).get("prospective_n", 0) >= MIN_SELECTION_N]
    best = max(ranked, key=lambda c: _selection_score(candidate_metrics[c["candidate_id"]])) if ranked else None
    best_metrics = candidate_metrics.get(best["candidate_id"], {}) if best else {}

    # Buy & Hold comparison uses only the same prospective window as the best
    # challenger. A 200D trend comparison remains a development diagnostic until
    # enough clean post-holdout history exists to calculate it without leakage.
    buy_hold_metrics: dict[str, Any] | None = None
    if best and feature_ready:
        created = pd.Timestamp(best["created_market_date"])
        idx = frame.loc[valid_idx].index > created
        bh_ret = frame.loc[valid_idx, "asset_return"].loc[idx]
        bh_exp = pd.Series(1.0, index=bh_ret.index)
        buy_hold_metrics = _metrics(bh_ret, frame.loc[valid_idx, "risk_free_return"].loc[idx], bh_exp)

    blockers: list[str] = []
    if not best:
        blockers.append("NO_CHALLENGER_WITH_MINIMUM_PROSPECTIVE_EVIDENCE")
    else:
        if int(best_metrics.get("prospective_n") or 0) < MIN_PROMOTION_N:
            blockers.append("PROSPECTIVE_N_BELOW_PROMOTION_MINIMUM")
        if buy_hold_metrics:
            if best_metrics.get("sharpe_excess") is None or buy_hold_metrics.get("sharpe_excess") is None or float(best_metrics["sharpe_excess"]) <= float(buy_hold_metrics["sharpe_excess"]):
                blockers.append("NO_RISK_ADJUSTED_EDGE_VS_BUY_HOLD")
            if best_metrics.get("max_drawdown") is None or buy_hold_metrics.get("max_drawdown") is None or float(best_metrics["max_drawdown"]) < float(buy_hold_metrics["max_drawdown"]):
                blockers.append("DRAWDOWN_NOT_IMPROVED_VS_BUY_HOLD")
    ready_for_review = bool(best) and not blockers

    next_checkpoint = next((c for c in CHECKPOINTS if c not in completed), None)
    public = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "platform": {
            "id": PLATFORM_ID,
            "name": "BRACE-SPX",
            "architecture": "ONE_PLATFORM_FROZEN_PLUS_ADAPTIVE",
            "target": "S&P 500 / SPY",
            "research_only": True,
            "live_orders": False,
        },
        "frozen_track": {
            "generation_id": g6.GENERATION_ID,
            "candidate_signature": g6.candidate_signature(),
            "strict_gate_passed": bool(g6_report.get("strict_gate_passed")),
            "single_champion_authorized": bool(g6_report.get("single_champion_authorized", False)),
            "shadow_status": g6_shadow.get("status"),
            "shadow_start": g6_shadow.get("shadow_start"),
            "latest_market_date": g6_shadow.get("latest_market_date"),
            "observations_collected": int(g6_shadow.get("observations_collected") or 0),
            "warmup_required": int(g6_shadow.get("warmup_required") or g6.SHADOW_WARMUP_OBSERVATIONS),
            "observations_remaining": int(g6_shadow.get("observations_remaining") or 0),
            "immutable": True,
            "parameter_mutation_allowed": False,
        },
        "adaptive_research": {
            "track_id": TRACK_ID,
            "status": "ACTIVE" if feature_ready else "ACTIVE_AWAITING_FEATURE_READINESS",
            "activated_at": state.get("activated_at"),
            "activation_market_date": state.get("activation_market_date"),
            "activation_observations": int(state.get("activation_observations") or 0),
            "research_cycles": int(state.get("research_cycles") or 0),
            "feature_ready": feature_ready,
            "feature_eligible_rows": int(len(valid_idx)),
            "active_challengers": len(state["challengers"]),
            "initial_challenger_prospective_n": int(initial_n),
            "completed_checkpoints": sorted(completed),
            "next_checkpoint_n": next_checkpoint,
            "best_challenger": best["candidate_id"] if best else None,
            "best_challenger_metrics": best_metrics if best else None,
            "family_information_coefficients": family_ic,
            "challengers": [_public_candidate(c, candidate_metrics.get(c["candidate_id"], {})) for c in state["challengers"]],
            "last_learning_event": (state.get("learning_events") or [None])[-1],
            "prospective_evidence_backfill_allowed": False,
        },
        "promotion_gate": {
            "target_generation": "G7",
            "status": "READY_FOR_HUMAN_REVIEW" if ready_for_review else "NOT_READY",
            "automatic_promotion": False,
            "human_approval_required": True,
            "minimum_prospective_n": MIN_PROMOTION_N,
            "best_challenger": best["candidate_id"] if best else None,
            "blockers": blockers,
            "prospective_buy_hold": buy_hold_metrics,
            "trend_200d_policy": "development_reference_only_until_clean_post_holdout_history_is_long_enough",
        },
        "governance": {
            "one_platform": True,
            "separate_engine_created": False,
            "frozen_g6_mutation": False,
            "sealed_holdout_accessed": False,
            "historical_backfill_for_new_challengers": False,
            "automatic_cross_engine_writeback": False,
            "trade_execution": False,
        },
    }
    return state, public


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--g6-report", type=Path, default=DEFAULT_G6_REPORT)
    parser.add_argument("--g6-shadow", type=Path, default=DEFAULT_G6_SHADOW)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    g6_report = _read_json(args.g6_report, {})
    g6_shadow = _read_json(args.g6_shadow, {})
    if not g6_report or not g6_shadow:
        raise RuntimeError("G6 development and shadow evidence must exist before adaptive research runs")
    if g6_shadow.get("holdout_accessed") is not False:
        raise RuntimeError("G6 shadow indicates holdout access; fail closed")

    state = _read_json(args.state)
    if state is None:
        state = _initial_state(g6_report, g6_shadow)

    prices = shadow_loop.download_shadow_prices(args.end)
    state, public = run(state, prices, g6_report, g6_shadow)
    _write_json(args.state, state)
    _write_json(args.public, public)
    print(
        "BRACE-SPX unified platform "
        f"frozen={public['frozen_track']['observations_collected']}/{public['frozen_track']['warmup_required']} "
        f"adaptive={public['adaptive_research']['status']} "
        f"challengers={public['adaptive_research']['active_challengers']} "
        f"prospective_n={public['adaptive_research']['initial_challenger_prospective_n']} "
        f"promotion={public['promotion_gate']['status']}"
    )


if __name__ == "__main__":
    main()
