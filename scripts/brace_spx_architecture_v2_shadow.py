#!/usr/bin/env python3
"""Daily no-order shadow tracker for BRACE-SPX Architecture 2.

The tracker begins after the sealed holdout and deliberately cold-starts. It
never downloads the holdout period and emits no trading orders. Until 252 new
post-holdout market observations are collected, status remains `warming_up`.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import brace_spx_architecture_v2 as architecture

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "shadow" / "brace_spx_architecture_v2_shadow.json"
WARMUP_DAYS = 252


def download_shadow_prices(end: str | None = None) -> pd.DataFrame:
    import yfinance as yf

    start = architecture.SHADOW_START
    end_date = end or (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    if pd.Timestamp(start) <= pd.Timestamp(architecture.SEALED_HOLDOUT_END):
        raise RuntimeError("Shadow start must be after the sealed holdout")
    data = yf.download(
        architecture.required_symbols(),
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
            raise RuntimeError("Shadow response has no Close field")
    else:
        close = data[["Close"]]
    if isinstance(close, pd.Series):
        close = close.to_frame(architecture.TARGET_SYMBOL)
    close.index = pd.to_datetime(close.index).tz_localize(None)
    close = close.sort_index()
    if len(close.index) and close.index.min() < pd.Timestamp(architecture.SHADOW_START):
        raise RuntimeError("Shadow download entered the sealed holdout")
    return close


def run(prices: pd.DataFrame) -> dict[str, Any]:
    collected = int(len(prices))
    base = {
        "schema_version": "1.0.0",
        "architecture_id": architecture.ARCHITECTURE_ID,
        "candidate_signature": architecture.candidate_signature(),
        "shadow_start": architecture.SHADOW_START,
        "sealed_holdout_end": architecture.SEALED_HOLDOUT_END,
        "holdout_accessed": False,
        "live_orders": False,
        "autonomous_trading": False,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "observations_collected": collected,
        "warmup_required": WARMUP_DAYS,
    }
    if prices.empty or collected < WARMUP_DAYS:
        base.update({
            "status": "warming_up",
            "observations_remaining": max(0, WARMUP_DAYS - collected),
            "latest_market_date": prices.index.max().date().isoformat() if collected else None,
            "candidate_snapshots": [],
            "policy": "No exposure is emitted before a clean post-holdout warm-up is complete."
        })
        return base

    frame = architecture.build_features(prices, research_mode=False)
    signals = architecture.signal_frame(frame)
    valid_index = signals.dropna().index
    frame = frame.loc[valid_index]
    signals = signals.loc[valid_index]
    if len(frame) < 2:
        base.update({"status": "warming_up", "observations_remaining": WARMUP_DAYS, "candidate_snapshots": []})
        return base
    regime = architecture.deterministic_regime(frame, signals)
    latest = frame.index[-1]
    snapshots: list[dict[str, Any]] = []
    for candidate in architecture.candidate_pool():
        target, score = architecture.candidate_exposure(frame, signals, regime, candidate)
        returns, turnover, applied = architecture.portfolio_returns(frame, target)
        snapshots.append({
            "candidate_id": candidate.candidate_id(),
            "candidate_name": candidate.name,
            "signal": round(float(score.loc[latest]), 6),
            "regime": str(regime.loc[latest]),
            "target_exposure_next_session": round(float(target.loc[latest]), 6),
            "applied_exposure_latest_session": round(float(applied.loc[latest]), 6),
            "shadow_cumulative_return": round(float((1.0 + returns.dropna()).prod() - 1.0), 6),
            "shadow_turnover": round(float(turnover.sum()), 6)
        })
    base.update({
        "status": "shadow_active_no_orders",
        "observations_remaining": 0,
        "latest_market_date": latest.date().isoformat(),
        "candidate_snapshots": snapshots,
        "single_champion_selected": False,
        "policy": "All predeclared candidates are tracked in parallel; no orders are generated."
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
    print(f"BRACE-SPX shadow: status={payload['status']} observations={payload['observations_collected']}")


if __name__ == "__main__":
    main()
