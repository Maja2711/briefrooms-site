#!/usr/bin/env python3
"""GPW market adapter for the shared Daily Stock Core.

The adapter deliberately leaves the proven GPW event layer, ESPI/EBI evidence,
opening cross-check, outcome monitor and control-loop learning in place. It
uses Daily Stock Core as the primary quant/composite implementation while
retaining the proven legacy GPW quant screen as a compatibility fallback.

Historical non-trade publications may legitimately contain ``selection: null``.
Those rows are not trade outcomes and are filtered before the legacy GPW
history scorer so P0.3/full ranking and the live primary path share the same
safe learning input contract.
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
_CONTROL_RETRY_INSTALLED = False
_ORIGINAL_PUBLISH = gpw.publish
_LEGACY_BUILD_QUANT_CANDIDATE = gpw.build_quant_candidate


def _learning_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only records with an actual selection mapping for trade learning."""
    return [
        row
        for row in history
        if isinstance(row, dict) and isinstance(row.get("selection"), dict)
    ]


def _history_scorer(
    history: list[dict[str, Any]], sector: str, minimum_sample: int
) -> tuple[float, int]:
    # Resolve dynamically so gpw_daily_control_loop can keep installing its
    # established Bayesian shrinkage learner without losing learned state.
    return gpw.history_expectancy_score(
        _learning_history(history), sector, minimum_sample
    )


def _build_quant_candidate(company, bars, expected_day, config, history):
    learning_history = _learning_history(history or [])
    candidate = core.build_quant_candidate(
        company,
        bars,
        expected_day,
        config,
        core.GPW_PROFILE,
        history=learning_history,
        history_scorer=_history_scorer,
    )
    if candidate is not None:
        candidate["quant_engine"] = "daily-stock-core-v1"
        return candidate

    # Compatibility guardrail: the shared core must never make the mature GPW
    # engine less capable of producing a valid candidate. If the common layer
    # rejects a name for a core-specific history/data-shape constraint, retry
    # the exact proven GPW quant screen. Its liquidity, ATR/risk and bounded
    # historical-learning rules remain in force, so this is not a forced pick.
    candidate = _LEGACY_BUILD_QUANT_CANDIDATE(
        company, bars, expected_day, config, learning_history
    )
    if candidate is not None:
        candidate["quant_engine"] = "gpw-legacy-compatible-fallback"
    return candidate


def _install_control_retry() -> None:
    """Keep BRAK_TRANSAKCJI provisional until the post-open cutoff."""
    global _CONTROL_RETRY_INSTALLED
    if _CONTROL_RETRY_INSTALLED:
        return
    try:
        from scripts import gpw_daily_control_loop as loop
    except ModuleNotFoundError:
        import gpw_daily_control_loop as loop

    original = loop.control_once

    def retrying_control_once(*args, **kwargs):
        explicit_now = kwargs.get("now")
        cycles = 1 if explicit_now is not None else 3
        last = None

        for _ in range(cycles):
            now = explicit_now or gpw.now_warsaw()
            config = gpw.load_config()
            cutoff = gpw.cutoff_for(now.date(), config)
            current = gpw.load_json(gpw.PUBLIC_PATH)

            if (
                isinstance(current, dict)
                and current.get("date") == now.date().isoformat()
                and current.get("decision") == "BRAK_TRANSAKCJI"
                and now < cutoff
            ):
                gpw.PUBLIC_PATH.unlink(missing_ok=True)

            last = original(*args, **kwargs)
            if not isinstance(last, dict):
                return last
            if last.get("decision") == "TRANSAKCJA":
                return last
            if last.get("decision") != "BRAK_TRANSAKCJI":
                return last
            if explicit_now is not None or gpw.now_warsaw() >= cutoff:
                return last

        return last

    loop.control_once = retrying_control_once
    _CONTROL_RETRY_INSTALLED = True


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
        "legacy_quant_fallback": True,
        "automatic_no_trade_retries": True,
        "non_trade_history_filtered": True,
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
    _install_control_retry()
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
