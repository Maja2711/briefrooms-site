#!/usr/bin/env python3
"""Global NO RETROACTIVE EXECUTION policy for BriefRooms trading engines.

LIVE execution is append-only in time. A later run may continue or close a
previously persisted position, but it may never manufacture a historical
opening decision/fill from bars that were already in the past when the run
started. SHADOW/REPLAY/COUNTERFACTUAL research may use historical observations,
but those observations are non-executable and must stay outside canonical LIVE
state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
CLOCK_SKEW = timedelta(minutes=5)
MAX_LIVE_MARKET_DATA_LAG = timedelta(minutes=20)
LIVE_MODES = {"LIVE", "PRODUCTION", "PAPER_LIVE"}
SHADOW_MODES = {"SHADOW", "REPLAY", "COUNTERFACTUAL", "BACKTEST", "RESEARCH"}


class RetroactiveExecutionError(RuntimeError):
    """Raised when a LIVE event would be created retroactively."""


def parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_mode(value: Any) -> str:
    mode = str(value or "LIVE").strip().upper()
    if mode in LIVE_MODES or mode in SHADOW_MODES:
        return mode
    raise RetroactiveExecutionError(f"unknown execution mode: {mode!r}")


def execution_floor(run_started_at: datetime, *, max_market_data_lag: timedelta = MAX_LIVE_MARKET_DATA_LAG) -> datetime:
    run = run_started_at.astimezone(UTC)
    return run - max_market_data_lag


def assert_live_decision(
    *,
    decision_at: Any,
    run_started_at: datetime,
    persisted_before_run: bool,
    mode: str = "LIVE",
) -> None:
    mode = normalize_mode(mode)
    if mode in SHADOW_MODES:
        return
    decision = parse_ts(decision_at)
    if decision is None:
        raise RetroactiveExecutionError("missing_execution_provenance: decision timestamp missing")
    if not persisted_before_run and decision < run_started_at.astimezone(UTC) - CLOCK_SKEW:
        raise RetroactiveExecutionError(
            "retroactive_decision_created_current_run: "
            f"decision_at={decision.isoformat()} run_started_at={run_started_at.astimezone(UTC).isoformat()}"
        )


def assert_live_fill(
    *,
    entry_at: Any,
    decision_at: Any,
    run_started_at: datetime,
    fill_persisted_before_run: bool,
    decision_persisted_before_run: bool,
    mode: str = "LIVE",
    max_market_data_lag: timedelta = MAX_LIVE_MARKET_DATA_LAG,
) -> None:
    mode = normalize_mode(mode)
    if mode in SHADOW_MODES:
        return
    entry = parse_ts(entry_at)
    decision = parse_ts(decision_at)
    if entry is None or decision is None:
        raise RetroactiveExecutionError("missing_execution_provenance: LIVE fill needs entry_at and decision_at")
    if entry < decision:
        raise RetroactiveExecutionError(
            f"entry_before_decision: entry_at={entry.isoformat()} decision_at={decision.isoformat()}"
        )
    if fill_persisted_before_run:
        return
    assert_live_decision(
        decision_at=decision,
        run_started_at=run_started_at,
        persisted_before_run=decision_persisted_before_run,
        mode=mode,
    )
    floor = execution_floor(run_started_at, max_market_data_lag=max_market_data_lag)
    if entry < floor:
        reason = (
            "historical_fill_without_preexisting_frozen_decision"
            if not decision_persisted_before_run
            else "retroactive_fill_created_current_run"
        )
        raise RetroactiveExecutionError(
            f"{reason}: entry_at={entry.isoformat()} execution_floor={floor.isoformat()}"
        )


def assert_recovery_execution_allowed(*, recovery_mode: bool, fill_persisted_before_run: bool, mode: str = "LIVE") -> None:
    mode = normalize_mode(mode)
    if mode in SHADOW_MODES:
        return
    if recovery_mode and not fill_persisted_before_run:
        raise RetroactiveExecutionError("recovery_mode_execution_disabled: recovery may reconstruct analysis, never a LIVE historical fill")


def assert_shadow_cannot_publish_live(*, source_mode: str, target_mode: str) -> None:
    source = normalize_mode(source_mode)
    target = normalize_mode(target_mode)
    if source in SHADOW_MODES and target in LIVE_MODES:
        raise RetroactiveExecutionError("shadow_execution_cannot_publish_live: shadow/replay fills are non-executable")


def provenance(
    *,
    run_started_at: datetime,
    decision_created_at: Any,
    decision_persisted_before_run: bool,
    mode: str = "LIVE",
) -> dict[str, Any]:
    return {
        "policy": "NO_RETROACTIVE_EXECUTION_V1",
        "mode": normalize_mode(mode),
        "run_started_at": run_started_at.astimezone(UTC).isoformat(timespec="seconds"),
        "decision_created_at": parse_ts(decision_created_at).isoformat(timespec="seconds") if parse_ts(decision_created_at) else None,
        "decision_persisted_before_run": bool(decision_persisted_before_run),
        "retroactive": False,
    }
