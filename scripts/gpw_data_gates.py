#!/usr/bin/env python3
"""Canonical GPW Daily freshness and data-quality gates.

P0.4 removes duplicated freshness logic from the primary and mandatory paths.
Every GPW Daily decision must use the same historical-session acceptance,
market-coverage threshold and current-session execution-quote validation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    from scripts import gpw_daily_pick as gpw
except ModuleNotFoundError:
    import gpw_daily_pick as gpw


ENGINE = "gpw-data-gates-v1"
DEFAULT_MINIMUM_COVERAGE = 0.80
DEFAULT_MAX_HISTORICAL_LAG_SESSIONS = 1
DEFAULT_MAX_EXECUTION_QUOTE_AGE_MINUTES = 20
DEFAULT_MAX_FUTURE_CLOCK_SKEW_MINUTES = 2


def settings_from(config: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = config.get("data_gates") or {}
    policy = policy or {}
    minimum_coverage = float(
        raw.get(
            "minimum_market_coverage",
            config.get(
                "minimum_data_completeness",
                policy.get("minimum_market_coverage", DEFAULT_MINIMUM_COVERAGE),
            ),
        )
    )
    maximum_lag = int(
        raw.get(
            "maximum_historical_lag_sessions",
            policy.get("maximum_historical_lag_sessions", DEFAULT_MAX_HISTORICAL_LAG_SESSIONS),
        )
    )
    return {
        "engine": str(raw.get("engine") or ENGINE),
        "minimum_market_coverage": max(0.0, min(1.0, minimum_coverage)),
        "maximum_historical_lag_sessions": max(0, maximum_lag),
        "require_current_session_quote": bool(raw.get("require_current_session_quote", True)),
        "maximum_execution_quote_age_minutes": max(
            1,
            int(raw.get("maximum_execution_quote_age_minutes", DEFAULT_MAX_EXECUTION_QUOTE_AGE_MINUTES)),
        ),
        "maximum_future_clock_skew_minutes": max(
            0,
            int(raw.get("maximum_future_clock_skew_minutes", DEFAULT_MAX_FUTURE_CLOCK_SKEW_MINUTES)),
        ),
        "reject_crosscheck_statuses": tuple(
            str(value).lower()
            for value in raw.get("reject_crosscheck_statuses", ["conflict", "rejected"])
        ),
        "role": str(raw.get("role") or "single_source_of_truth_for_gpw_freshness_and_data_quality"),
    }


def historical_gate(
    bars: list[Any],
    *,
    expected_day,
    config: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings_from(config, policy)
    completed = [bar for bar in list(bars or []) if bar.day <= expected_day]
    if not completed:
        return {
            "status": "missing",
            "accepted": False,
            "reason": "no_completed_history",
            "expected_session": expected_day.isoformat(),
            "latest_session": None,
            "lag_sessions": None,
        }

    latest_day = completed[-1].day
    cursor = expected_day
    for lag in range(settings["maximum_historical_lag_sessions"] + 1):
        if latest_day == cursor:
            return {
                "status": "fresh" if lag == 0 else "accepted_lag",
                "accepted": True,
                "reason": None,
                "expected_session": expected_day.isoformat(),
                "latest_session": latest_day.isoformat(),
                "feature_session": cursor.isoformat(),
                "lag_sessions": lag,
                "completed_bars": completed,
            }
        cursor = gpw.previous_session(cursor, config)

    return {
        "status": "stale",
        "accepted": False,
        "reason": "historical_session_too_old",
        "expected_session": expected_day.isoformat(),
        "latest_session": latest_day.isoformat(),
        "lag_sessions": None,
    }


def historical_universe_report(
    config: dict[str, Any],
    cache: dict[str, list[Any]],
    *,
    expected_day,
    policy: dict[str, Any] | None = None,
    provider_failures: dict[str, str] | None = None,
) -> dict[str, Any]:
    settings = settings_from(config, policy)
    provider_failures = provider_failures or {}
    by_symbol: dict[str, Any] = {}
    accepted = 0
    for company in config["universe"]:
        symbol = str(company["symbol"])
        if symbol not in cache:
            by_symbol[symbol] = {
                "status": "provider_failure",
                "accepted": False,
                "reason": provider_failures.get(symbol, "missing_provider_history"),
                "expected_session": expected_day.isoformat(),
                "latest_session": None,
                "lag_sessions": None,
            }
            continue
        result = historical_gate(
            cache[symbol],
            expected_day=expected_day,
            config=config,
            policy=policy,
        )
        result.pop("completed_bars", None)
        by_symbol[symbol] = result
        if result["accepted"]:
            accepted += 1

    universe_size = len(config["universe"])
    ratio = accepted / max(universe_size, 1)
    return {
        "engine": settings["engine"],
        "status": "healthy" if ratio >= settings["minimum_market_coverage"] else "failed",
        "accepted_symbols": accepted,
        "universe_size": universe_size,
        "complete_ratio": round(ratio, 4),
        "minimum_market_coverage": settings["minimum_market_coverage"],
        "expected_session": expected_day.isoformat(),
        "by_symbol": by_symbol,
    }


def require_market_coverage(report: dict[str, Any]) -> None:
    if report.get("status") != "healthy":
        raise gpw.PublicationError(
            "GPW data gate failed: fresh historical coverage "
            f"{float(report.get('complete_ratio') or 0.0):.0%} below "
            f"{float(report.get('minimum_market_coverage') or 0.0):.0%}."
        )


def execution_gate(
    snapshot: dict[str, Any],
    *,
    now: datetime,
    config: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings_from(config, policy)
    required = ("open", "high", "low", "last")
    values = {key: float(snapshot.get(key) or 0.0) for key in required}
    if min(values.values()) <= 0.0:
        raise ValueError("execution quote contains non-positive OHLC")
    if values["high"] < max(values["open"], values["last"], values["low"]):
        raise ValueError("execution quote has invalid high")
    if values["low"] > min(values["open"], values["last"], values["high"]):
        raise ValueError("execution quote has invalid low")

    if settings["require_current_session_quote"]:
        if str(snapshot.get("date") or "") != now.date().isoformat():
            raise ValueError(f"stale execution session: {snapshot.get('date')}")

    observed_raw = str(snapshot.get("observed_at") or "")
    if not observed_raw:
        raise ValueError("execution quote missing observed_at")
    try:
        observed = datetime.fromisoformat(observed_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid execution observed_at") from exc
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=gpw.WARSAW)
    age_minutes = (now - observed.astimezone(now.tzinfo)).total_seconds() / 60.0
    if age_minutes < -settings["maximum_future_clock_skew_minutes"]:
        raise ValueError(f"execution quote timestamp is in the future: {age_minutes:.1f}m")
    if age_minutes > settings["maximum_execution_quote_age_minutes"]:
        raise ValueError(f"stale execution quote age: {age_minutes:.1f}m")

    crosscheck = snapshot.get("crosscheck") or {}
    status = str(crosscheck.get("status") or "unknown").lower()
    if status in settings["reject_crosscheck_statuses"]:
        raise ValueError(f"execution quote cross-check rejected: {status}")

    return {
        "engine": settings["engine"],
        "status": "accepted",
        "session": str(snapshot.get("date") or ""),
        "observed_at": observed.isoformat(timespec="seconds"),
        "age_minutes": round(max(age_minutes, 0.0), 2),
        "crosscheck_status": status,
        "provider": snapshot.get("provider"),
    }
