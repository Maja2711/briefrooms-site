#!/usr/bin/env python3
"""BRACE-SPX Architecture 3: separately governed Long / Short / Flat research."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
ARCHITECTURE_ID = "spx-directional-lsf-a3"
PROTOCOL_VERSION = "1.0.0"
BASE_ARCHITECTURE_ID = "spx-multisignal-regime-a2"
BASE_CANDIDATE_SIGNATURE = "c5f4ff626f96d29274f0a695b821c7a5a49ef4c2230fa890710d8f5cede990d9"
SEALED_HOLDOUT_START = "2022-08-01"
SEALED_HOLDOUT_END = "2026-07-31"
COST_PER_UNIT_TURNOVER = 0.0005
ANNUAL_SHORT_BORROW_COST = 0.01
DAILY_SHORT_BORROW_COST = (1.0 + ANNUAL_SHORT_BORROW_COST) ** (1.0 / 252.0) - 1.0
EXPECTED_CANDIDATES = 12
PRIOR_TRIAL_COUNT = 644
PRIOR_SHARPE_CROSS_SECTION_STD = 0.649385


@dataclass(frozen=True)
class Candidate:
    name: str
    signal_sources: tuple[str, ...]
    combination: str
    position_policy: str
    regime_policy: str
    weekly_rebalance: bool = True
    daily_crisis_gate: bool = True
    max_abs_exposure: float = 1.0

    def candidate_id(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def candidate_pool() -> list[Candidate]:
    pool = [
        Candidate("directional-trend", ("trend",), "mean", "conservative_short", "trend"),
        Candidate("directional-breadth", ("breadth",), "mean", "conservative_short", "breadth"),
        Candidate("directional-liquidity", ("liquidity",), "mean", "crisis_short", "liquidity"),
        Candidate("directional-options", ("options",), "mean", "crisis_short", "options"),
        Candidate("directional-rates", ("rates",), "mean", "conservative_short", "rates"),
        Candidate("trend-liquidity", ("trend", "liquidity"), "mean", "conservative_short", "deterministic_regime"),
        Candidate("breadth-options", ("breadth", "options"), "mean", "conservative_short", "deterministic_regime"),
        Candidate("liquidity-options", ("liquidity", "options"), "minimum_veto", "crisis_short", "deterministic_regime"),
        Candidate("rates-liquidity", ("rates", "liquidity"), "mean", "conservative_short", "deterministic_regime"),
        Candidate("orthogonal-three", ("rates", "liquidity", "options"), "median", "conservative_short", "deterministic_regime"),
        Candidate("all-source-median", ("trend", "breadth", "liquidity", "options", "rates"), "median", "symmetric", "deterministic_regime"),
        Candidate("crisis-consensus", ("trend", "breadth", "liquidity", "options", "rates"), "consensus", "crisis_short", "deterministic_regime"),
    ]
    if len(pool) != EXPECTED_CANDIDATES or len({c.candidate_id() for c in pool}) != EXPECTED_CANDIDATES:
        raise RuntimeError("Architecture 3 candidate space is not fixed and unique")
    return pool


def candidate_signature(pool: Sequence[Candidate] | None = None) -> str:
    ids = [c.candidate_id() for c in (pool or candidate_pool())]
    return hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()


def _combine(signals: pd.DataFrame, candidate: Candidate) -> pd.Series:
    subset = signals[list(candidate.signal_sources)]
    if candidate.combination == "median":
        score = subset.median(axis=1)
    elif candidate.combination == "minimum_veto":
        score = 0.55 * subset.mean(axis=1) + 0.45 * subset.min(axis=1)
    elif candidate.combination == "consensus":
        agreement = subset.apply(lambda row: abs(float(np.sign(row.dropna()).mean())) if row.notna().any() else 0.0, axis=1)
        score = subset.mean(axis=1) * (0.5 + 0.5 * agreement)
    else:
        score = subset.mean(axis=1)
    return score.clip(-1.0, 1.0)


def _score_to_signed_exposure(score: pd.Series, policy: str) -> pd.Series:
    if policy == "symmetric":
        cuts, values = [-0.55, -0.20, 0.20, 0.55], [-1.0, -0.5, 0.0, 0.5]
    elif policy == "crisis_short":
        cuts, values = [-0.70, -0.45, 0.15, 0.50], [-1.0, -0.5, 0.0, 0.5]
    else:
        cuts, values = [-0.65, -0.35, 0.15, 0.50], [-1.0, -0.5, 0.0, 0.5]
    levels = np.select([score <= cuts[0], score <= cuts[1], score < cuts[2], score < cuts[3]], values, default=1.0)
    return pd.Series(levels, index=score.index, dtype=float)


def candidate_exposure(frame: pd.DataFrame, signals: pd.DataFrame, regime: pd.Series, candidate: Candidate) -> tuple[pd.Series, pd.Series]:
    score = _combine(signals, candidate)
    desired = _score_to_signed_exposure(score, candidate.position_policy)
    if candidate.regime_policy == "deterministic_regime":
        long_m = regime.map({"low_vol": 1.0, "high_vol": 0.70, "panic": 0.0, "recovery": 0.75}).fillna(0.75)
        short_m = regime.map({"low_vol": 0.50, "high_vol": 0.80, "panic": 1.0, "recovery": 0.50}).fillna(0.50)
        desired = desired.clip(lower=0.0) * long_m + desired.clip(upper=0.0) * short_m
    elif candidate.regime_policy == "liquidity":
        desired = np.where(signals["liquidity"] <= -0.65, np.minimum(desired, -0.5), desired)
    elif candidate.regime_policy == "options":
        desired = np.where(signals["options"] <= -0.70, np.minimum(desired, -0.5), desired)
    desired = pd.Series(desired, index=frame.index).clip(-1.0, 1.0)
    exposure = desired.where(frame.index.dayofweek == 4).ffill().fillna(0.0)
    severe = (signals["liquidity"] <= -0.75) & (signals["options"] <= -0.75)
    broad = severe & ((signals["trend"] <= -0.35) | (signals["breadth"] <= -0.35))
    exposure.loc[severe] = np.minimum(exposure.loc[severe], -0.5)
    exposure.loc[broad] = -1.0
    return exposure.clip(-1.0, 1.0), score


def portfolio_returns(frame: pd.DataFrame, target: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series, pd.DataFrame]:
    applied = target.reindex(frame.index).ffill().fillna(0.0).clip(-1.0, 1.0).shift(1).fillna(0.0)
    turnover = applied.diff().abs().fillna(applied.abs())
    asset = frame["asset_return"].fillna(0.0)
    rf = frame["risk_free_return"].fillna(0.0)
    long = applied.clip(lower=0.0)
    short = -applied.clip(upper=0.0)
    cost = turnover * COST_PER_UNIT_TURNOVER
    borrow = short * DAILY_SHORT_BORROW_COST
    returns = applied * asset + (1.0 - long) * rf - borrow - cost
    parts = pd.DataFrame({
        "long_excess": long * (asset - rf),
        "short_excess": short * (-asset - DAILY_SHORT_BORROW_COST),
        "transaction_cost": -cost,
    }, index=frame.index)
    return returns, turnover, applied, parts


def metrics(returns: pd.Series, turnover: pd.Series, rf: pd.Series, exposure: pd.Series) -> dict[str, Any]:
    data = pd.concat([returns.rename("r"), turnover.rename("t"), rf.rename("rf"), exposure.rename("e")], axis=1).dropna()
    r = data["r"]
    years = max(len(r) / 252.0, 1.0 / 252.0)
    factor = float((1.0 + r).prod())
    cagr = factor ** (1.0 / years) - 1.0 if factor > 0 else -1.0
    vol = float(r.std(ddof=1) * math.sqrt(252.0))
    excess = r - data["rf"]
    std = float(excess.std(ddof=1))
    sharpe = float(excess.mean() / std * math.sqrt(252.0)) if std > 0 else 0.0
    equity = (1.0 + r).cumprod()
    dd = float((equity / equity.cummax() - 1.0).min())
    e = data["e"]
    return {
        "total_return": round(factor - 1.0, 6), "cagr": round(cagr, 6),
        "annualized_volatility": round(vol, 6), "sharpe_excess": round(sharpe, 6),
        "max_drawdown": round(dd, 6), "calmar": round(cagr / abs(dd), 6) if dd < 0 else 0.0,
        "annualized_turnover": round(float(data["t"].mean() * 252.0), 6),
        "average_net_exposure": round(float(e.mean()), 6),
        "average_gross_exposure": round(float(e.abs().mean()), 6),
        "time_long": round(float((e > 0).mean()), 6), "time_short": round(float((e < 0).mean()), 6),
        "time_flat": round(float((e == 0).mean()), 6), "days": len(data),
    }


def directional(frame: pd.DataFrame, exposure: pd.Series, parts: pd.DataFrame) -> dict[str, Any]:
    data = pd.concat([exposure.rename("e"), frame["asset_return"].rename("a"), parts], axis=1).dropna()
    active, short = data["e"] != 0, data["e"] < 0
    return {
        "long_excess_contribution_annualized": round(float(data["long_excess"].mean() * 252), 6),
        "short_excess_contribution_annualized": round(float(data["short_excess"].mean() * 252), 6),
        "transaction_cost_contribution_annualized": round(float(data["transaction_cost"].mean() * 252), 6),
        "directional_hit_rate": round(float((np.sign(data.loc[active, "e"]) * data.loc[active, "a"] > 0).mean()), 6) if active.any() else None,
        "short_hit_rate": round(float((data.loc[short, "a"] < 0).mean()), 6) if short.any() else None,
        "long_days": int((data["e"] > 0).sum()), "short_days": int(short.sum()), "flat_days": int((data["e"] == 0).sum()),
    }


def authorize_single_champion(strict_gate_passed: bool, short_contribution_positive: bool, external_validation_passed: bool = False) -> bool:
    return bool(strict_gate_passed and short_contribution_positive and external_validation_passed)


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
