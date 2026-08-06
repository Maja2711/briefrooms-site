#!/usr/bin/env python3
"""Preserve portfolio decision state during Analytics-news refreshes.

The Analytics pipeline may replace ``recent_news`` and material-report records,
but it must not rewrite model scores, signals, BRACE-facing review flags or
append-only review history. A targeted baseline repair reverses the one GOOGL
state change caused by the first analysis-news-v2 production refresh.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "data/investments/portfolio_10k.json"
DECISION_FIELDS = ("model_score", "positive_signals", "risk_signals", "review_flag")


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def capture(portfolio: dict[str, Any]) -> dict[str, Any]:
    positions: dict[str, dict[str, Any]] = {}
    for position in portfolio.get("positions") or []:
        position_id = str(position.get("id") or "")
        positions[position_id] = {
            field: deepcopy(position.get(field)) for field in DECISION_FIELDS
        }
    return {
        "positions": positions,
        "snapshots": deepcopy(portfolio.get("snapshots")),
        "weekly_reviews": deepcopy(portfolio.get("weekly_reviews")),
    }


def restore(portfolio: dict[str, Any], state: dict[str, Any]) -> None:
    saved = state.get("positions") or {}
    for position in portfolio.get("positions") or []:
        values = saved.get(str(position.get("id") or ""))
        if not isinstance(values, dict):
            continue
        for field in DECISION_FIELDS:
            if field in values:
                position[field] = deepcopy(values[field])
    if "snapshots" in state:
        portfolio["snapshots"] = deepcopy(state.get("snapshots"))
    if "weekly_reviews" in state:
        portfolio["weekly_reviews"] = deepcopy(state.get("weekly_reviews"))


def repair_initial_side_effect(portfolio: dict[str, Any]) -> None:
    """Restore the exact pre-refresh GOOGL decision state.

    This does not change market data or the new Analytics source report. It only
    reverses the accidental score/flag rewrite introduced by the first v2 run.
    """
    for position in portfolio.get("positions") or []:
        if position.get("id") != "googl":
            continue
        position["model_score"] = 80
        position["review_flag"] = "HOLD"
        position["positive_signals"] = [
            "price_above_ma200",
            "ma50_above_ma200",
            "positive_six_month_momentum",
            "drawdown_below_twenty_percent",
        ]
        position["risk_signals"] = ["material_news_headline_requires_review"]

    snapshots = portfolio.get("snapshots") or []
    if snapshots:
        entry = ((snapshots[-1].get("positions") or {}).get("googl"))
        if isinstance(entry, dict):
            entry["review_flag"] = "HOLD"

    reviews = portfolio.get("weekly_reviews") or []
    if reviews:
        for flag in reviews[-1].get("position_flags") or []:
            if flag.get("id") == "googl":
                flag["flag"] = "HOLD"
                flag["model_score"] = 80
                flag["risk_signals"] = ["material_news_headline_requires_review"]


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--save", type=Path)
    mode.add_argument("--restore", type=Path)
    mode.add_argument("--repair-initial-side-effect", action="store_true")
    args = parser.parse_args()

    portfolio = read(PORTFOLIO)
    if args.save:
        write(args.save, capture(portfolio))
        print(f"Analytics decision state saved to {args.save}")
        return 0
    if args.restore:
        state = read(args.restore)
        restore(portfolio, state)
        write(PORTFOLIO, portfolio)
        print("Analytics refresh decision state restored")
        return 0

    repair_initial_side_effect(portfolio)
    write(PORTFOLIO, portfolio)
    print("Initial Analytics decision-state side effect repaired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
