#!/usr/bin/env python3
"""Attribute portfolio-level BRACE regret to actionable construction causes."""
from __future__ import annotations

from typing import Any, Dict, Mapping


def attribute_portfolio_outcome(outcome: Mapping[str, Any]) -> Dict[str, Any]:
    regret = float(outcome.get("regret") or 0.0)
    selected_vs_current_raw = outcome.get("selected_vs_current")
    selected_vs_current = (
        float(selected_vs_current_raw) if selected_vs_current_raw is not None else None
    )
    selected = outcome.get("selected_profile") or {}
    winner = outcome.get("winner_profile") or {}

    hints = []
    if selected_vs_current is not None and selected_vs_current < -0.003:
        hints.append(
            {
                "cause": "TURNOVER_COST_OR_UNNECESSARY_REBALANCE",
                "parameter": "portfolio_turnover_penalty",
                "direction": "UP",
                "strength": min(1.0, abs(selected_vs_current) / 0.02),
            }
        )
        hints.append(
            {
                "cause": "REBALANCE_FREQUENCY",
                "parameter": "portfolio_rebalance_threshold",
                "direction": "UP",
                "strength": min(1.0, abs(selected_vs_current) / 0.02),
            }
        )

    selected_cash = float(selected.get("cash_weight") or 0.0)
    winner_cash = float(winner.get("cash_weight") or 0.0)
    cash_gap = winner_cash - selected_cash
    if cash_gap > 0.04:
        hints.append(
            {
                "cause": "CASH_ALLOCATION_TOO_LOW",
                "parameter": "portfolio_cash_floor",
                "direction": "UP",
                "strength": min(1.0, cash_gap / 0.20),
            }
        )
    elif cash_gap < -0.04:
        hints.append(
            {
                "cause": "CASH_ALLOCATION_TOO_HIGH",
                "parameter": "portfolio_cash_floor",
                "direction": "DOWN",
                "strength": min(1.0, abs(cash_gap) / 0.20),
            }
        )

    selected_positions = int(selected.get("active_positions") or 0)
    winner_positions = int(winner.get("active_positions") or 0)
    if winner_positions and selected_positions:
        if winner_positions < selected_positions:
            hints.append(
                {
                    "cause": "OVER_DIVERSIFICATION",
                    "parameter": "portfolio_max_active_positions",
                    "direction": "DOWN",
                    "strength": min(1.0, (selected_positions - winner_positions) / 4.0),
                }
            )
        elif winner_positions > selected_positions:
            hints.append(
                {
                    "cause": "UNDER_DIVERSIFICATION",
                    "parameter": "portfolio_max_active_positions",
                    "direction": "UP",
                    "strength": min(1.0, (winner_positions - selected_positions) / 4.0),
                }
            )

    selected_hhi = float(selected.get("concentration_hhi") or 0.0)
    winner_hhi = float(winner.get("concentration_hhi") or 0.0)
    if winner_hhi + 0.03 < selected_hhi:
        hints.append(
            {
                "cause": "CONCENTRATION_TOO_HIGH",
                "parameter": "portfolio_diversification_penalty",
                "direction": "UP",
                "strength": min(1.0, (selected_hhi - winner_hhi) / 0.15),
            }
        )
    elif winner_hhi > selected_hhi + 0.03:
        hints.append(
            {
                "cause": "CONCENTRATION_PENALTY_TOO_HIGH",
                "parameter": "portfolio_diversification_penalty",
                "direction": "DOWN",
                "strength": min(1.0, (winner_hhi - selected_hhi) / 0.15),
            }
        )

    winner_name = str(outcome.get("winner") or "")
    if winner_name == "minimum_variance":
        hints.append(
            {
                "cause": "RISK_PENALTY_TOO_LOW",
                "parameter": "portfolio_risk_aversion",
                "direction": "UP",
                "strength": min(1.0, max(regret, 0.0) / 0.02),
            }
        )
    elif winner_name in {"maximum_sharpe", "conviction_growth"}:
        hints.append(
            {
                "cause": "RISK_PENALTY_TOO_HIGH",
                "parameter": "portfolio_risk_aversion",
                "direction": "DOWN",
                "strength": min(1.0, max(regret, 0.0) / 0.02),
            }
        )

    hints = sorted(
        hints,
        key=lambda item: (-float(item["strength"]), item["parameter"], item["direction"]),
    )
    primary = hints[0] if hints and regret >= 0.002 else None
    return {
        "schema_version": "brace-portfolio-learning-attribution-v1",
        "outcome_id": outcome.get("outcome_id"),
        "regret": round(regret, 10),
        "material_regret": regret >= 0.002,
        "primary_attribution": primary,
        "mutation_hints": hints,
        "classification": (
            str(primary["cause"]) if primary else "NO_MATERIAL_PORTFOLIO_CONSTRUCTION_ERROR"
        ),
    }
