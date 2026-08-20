#!/usr/bin/env python3
"""PR22 direct-signal admission layer for the active Daily EUR/USD engine.

The underlying v1.1 engine still owns scoring, position lifecycle, SL/TP/TIME exits,
and bounded component-weight learning. PR22 removes admission vetoes between a
raw LONG/SHORT signal and position opening: LONG/SHORT is admitted directly;
only FLAT remains a no-trade state. One-open-position-at-a-time is still enforced
by the lifecycle runner.
"""
from __future__ import annotations

from typing import Any, Mapping

import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_spot as base

ENGINE_VERSION = "eurusd-daily-spot-v1.2.0"
SIGNAL_LONG_THRESHOLD = 60.0
SIGNAL_SHORT_THRESHOLD = 40.0

_original_learning_state = lifecycle.learning_state


def direct_learning_state(trades: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    """Keep bounded weight learning, but remove learning-based admission vetoes."""
    state = dict(_original_learning_state(trades))
    state["entry_thresholds"] = {
        "long": SIGNAL_LONG_THRESHOLD,
        "short": SIGNAL_SHORT_THRESHOLD,
        "min_confidence": 0.0,
    }
    state["cooldown_until"] = None
    state["policy"] = {
        "signal_admission": "direct_long_short",
        "daily_entry_limit": None,
        "loss_cooldown_hours": 0,
        "blocking_filters": [],
        "one_open_position_at_a_time": True,
        "position_horizon_hours": 24,
        "weight_update": "bounded outcome feedback only; learning may change component weights but cannot veto LONG/SHORT admission",
    }
    return state


def direct_entry_gate(
    *,
    direction: str,
    score: float,
    confidence: float,
    history: Mapping[str, Any],
    observed_at: Any,
    previous_score: float | None,
    stretch_atr: float | None,
    shock_ratio: float | None,
) -> dict[str, Any]:
    """Admit every raw LONG/SHORT signal; reject only a genuinely FLAT signal."""
    normalized = str(direction).upper()
    accepted = normalized in {"LONG", "SHORT"}
    return {
        "accepted": accepted,
        "reasons": [] if accepted else ["raw_score_neutral"],
        "learning": direct_learning_state(list(history.get("trades") or [])),
        "previous_score": None if previous_score is None else round(float(previous_score), 2),
        "stretch_atr": None if stretch_atr is None else round(float(stretch_atr), 4),
        "shock_ratio": None if shock_ratio is None else round(float(shock_ratio), 4),
    }


def _install() -> None:
    base.ENGINE_VERSION = ENGINE_VERSION
    base.entry_gate = direct_entry_gate
    base.learning_state = direct_learning_state


_install()


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
