#!/usr/bin/env python3
"""GPW market adapter for the shared Daily Stock Core.

The adapter deliberately leaves the proven GPW event layer, ESPI/EBI evidence,
opening cross-check, outcome monitor and control-loop learning in place.  It
only replaces the duplicated quant/composite mechanics with Daily Stock Core.
"""
from __future__ import annotations

from typing import Any

try:
    from scripts import daily_stock_core as core
    from scripts import gpw_daily_pick as gpw
except ModuleNotFoundError:
    import daily_stock_core as core
    import gpw_daily_pick as gpw

_INSTALLED = False
_ORIGINAL_PUBLISH = gpw.publish


def _history_scorer(
    history: list[dict[str, Any]], sector: str, minimum_sample: int
) -> tuple[float, int]:
    # Resolve dynamically so gpw_daily_control_loop can keep installing its
    # established Bayesian shrinkage learner without losing any learned state.
    return gpw.history_expectancy_score(history, sector, minimum_sample)


def _build_quant_candidate(company, bars, expected_day, config, history):
    return core.build_quant_candidate(
        company,
        bars,
        expected_day,
        config,
        core.GPW_PROFILE,
        history=history,
        history_scorer=_history_scorer,
    )


def methodology(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or gpw.load_config()
    value = core.methodology_contract(core.GPW_PROFILE, config)
    value["adapter"] = {
        "calendar": "GPW / Europe-Warsaw",
        "session_confirmation": "09:05 Europe/Warsaw",
        "currency": "PLN",
        "official_channels": ["ESPI", "EBI", "PAP MediaRoom", "Biznes PAP"],
        "legacy_learning_preserved": True,
        "event_learning_preserved": True,
    }
    return value


def _publish(payload: dict[str, Any]) -> None:
    payload.setdefault("methodology", {})["daily_stock_core"] = methodology()
    _ORIGINAL_PUBLISH(payload)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    gpw.clamp = core.clamp
    gpw.round2 = core.round2
    gpw.true_range = core.true_range
    gpw.return_over = core.return_over
    gpw.percentile_score = core.percentile_score
    gpw.build_quant_candidate = _build_quant_candidate
    gpw.normalize_cross_section = core.normalize_cross_section
    gpw.composite = core.composite_score
    gpw.publish = _publish
    _INSTALLED = True


def main() -> int:
    install()
    try:
        from scripts import gpw_event_driven_loop as runtime
    except ModuleNotFoundError:
        import gpw_event_driven_loop as runtime
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())
