#!/usr/bin/env python3
"""Daily cold-start shadow loop for BRACE-SPX Generation 6.

Only post-holdout observations are downloaded. The tracker counts every new
market observation immediately and activates candidate shadow scoring only
after 70 clean post-holdout observations. It emits no orders.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import brace_spx_generation6 as g6

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "shadow" / "brace_spx_generation6_shadow.json"


def download_shadow_prices(end: str | None = None) -> pd.DataFrame:
    import yfinance as yf

    start = g6.SHADOW_START
    end_date = end or (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    if pd.Timestamp(start) <= pd.Timestamp(g6.SEALED_HOLDOUT_END):
        raise RuntimeError("Generation 6 shadow start must be after the sealed holdout")

    data = yf.download(
        g6.required_symbols(),
        start=start,
        end=end_date,
        auto_adjust=True,
        progress=False,
        group_by="column",
        threads=True,
    )
    if data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        if "Close" in data.columns.get_level_values(0):
            close = data["Close"]
        elif "Close" in data.columns.get_level_values(1):
            close = data.xs("Close", axis=1, level=1)
        else:
            raise RuntimeError("Generation 6 shadow response has no Close field")
    else:
        close = data[["Close"]].rename(columns={"Close": g6.TARGET_SYMBOL})
    if isinstance(close, pd.Series):
        close = close.to_frame(g6.TARGET_SYMBOL)
    close.index = pd.to_datetime(close.index).tz_localize(None)
    close = close.sort_index()
    if len(close.index) and close.index.min() < pd.Timestamp(g6.SHADOW_START):
        raise RuntimeError("Generation 6 shadow download entered the sealed holdout")
    return close


def run(prices: pd.DataFrame) -> dict[str, Any]:
    collected = int(len(prices))
    base = {
        "schema_version": "6.0.0",
        "generation_id": g6.GENERATION_ID,
        "candidate_signature": g6.candidate_signature(),
        "shadow_start": g6.SHADOW_START,
        "sealed_holdout_end": g6.SEALED_HOLDOUT_END,
        "holdout_accessed": False,
        "live_orders": False,
        "autonomous_trading": False,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "observations_collected": collected,
        "warmup_required": g6.SHADOW_WARMUP_OBSERVATIONS,
        "source_families": list(g6.SOURCE_FAMILIES),
    }
    if prices.empty or collected < g6.SHADOW_WARMUP_OBSERVATIONS:
        base.update({
            "status": "warming_up",
            "observations_remaining": max(0, g6.SHADOW_WARMUP_OBSERVATIONS - collected),
            "latest_market_date": prices.index.max().date().isoformat() if collected else None,
            "candidate_snapshots": [],
            "policy": "Count clean post-holdout observations; do not emit exposures before warm-up.",
        })
        return base

    frame = g6.build_features(prices, research_mode=False)
    signals = g6.signal_frame(frame)
    valid = signals.dropna().index
    frame = frame.loc[valid]
    signals = signals.loc[valid]
    if len(frame) < 2:
        base.update({
            "status": "warming_up",
            "observations_remaining": 1,
            "latest_market_date": prices.index.max().date().isoformat(),
            "candidate_snapshots": [],
            "policy": "Feature warm-up incomplete.",
        })
        return base

    regime = g6.deterministic_regime(frame, signals)
    latest = frame.index[-1]
    snapshots: list[dict[str, Any]] = []
    for candidate in g6.candidate_pool():
        target, score = g6.candidate_exposure(frame, signals, regime, candidate)
        returns, turnover, applied = g6.a2s.portfolio_returns(frame, target)
        snapshots.append({
            "candidate_name": candidate.name,
            "families": list(candidate.signal_sources),
            "signal": round(float(score.loc[latest]), 6),
            "regime": str(regime.loc[latest]),
            "target_exposure_next_session": round(float(target.loc[latest]), 6),
            "applied_exposure_latest_session": round(float(applied.loc[latest]), 6),
            "shadow_cumulative_return": round(float((1.0 + returns.dropna()).prod() - 1.0), 6),
            "shadow_turnover": round(float(turnover.sum()), 6),
        })

    base.update({
        "status": "shadow_active_no_orders",
        "observations_remaining": 0,
        "latest_market_date": latest.date().isoformat(),
        "latest_regime": str(regime.loc[latest]),
        "family_scores": {family: round(float(signals.loc[latest, family]), 6) for family in g6.SOURCE_FAMILIES},
        "candidate_snapshots": snapshots,
        "single_champion_selected": False,
        "policy": "Track all eight predeclared candidates in parallel; never place orders or mutate parameters.",
    })
    return base


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()
    payload = run(download_shadow_prices(args.end))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"BRACE-SPX G6 shadow status={payload['status']} "
        f"observations={payload['observations_collected']} remaining={payload['observations_remaining']}"
    )


if __name__ == "__main__":
    main()
