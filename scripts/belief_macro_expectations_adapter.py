from __future__ import annotations

"""Sourced macro-expectations adapter for EUR/USD event risk.

This adapter deliberately does not scrape or invent bank forecasts. It consumes
an explicitly sourced JSON payload supplied by an upstream collector/licensed
feed and converts it into an auditable Observation for Belief Core / LLM use.
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from belief_adapter_contract import AdapterResult, Observation
from belief_core import iso_z

EXPECTATIONS_PATH_ENV = "BELIEF_MACRO_EXPECTATIONS_PATH"
MAX_EXPECTATION_AGE_HOURS = 72.0


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _weighted_mean(rows: Sequence[tuple[float, float]]) -> float | None:
    total = sum(max(0.0, weight) for _, weight in rows)
    if total <= 0:
        return None
    return sum(value * max(0.0, weight) for value, weight in rows) / total


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _probability_below_market_consensus(
    forecasts: Sequence[Mapping[str, Any]],
    market_consensus: float,
) -> float | None:
    components: list[tuple[float, float, float]] = []
    for row in forecasts:
        try:
            forecast = float(row["forecast"])
            mae = float(row["historical_mae"])
            weight = float(row.get("accuracy_weight") or 1.0)
        except (KeyError, TypeError, ValueError):
            continue
        if mae <= 0 or weight <= 0:
            continue
        sigma = mae * math.sqrt(math.pi / 2.0)
        components.append((forecast, sigma, weight))
    if not components:
        return None
    total = sum(weight for _, _, weight in components)
    probability = sum(
        weight * _normal_cdf((market_consensus - forecast) / sigma)
        for forecast, sigma, weight in components
    ) / total
    return max(0.0, min(1.0, probability))


def _valid_forecasts(rows: Any, now: datetime) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    valid: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        institution = str(row.get("institution") or "").strip()
        source_ref = str(row.get("source_ref") or "").strip()
        published_at = _dt(row.get("published_at"))
        try:
            forecast = float(row.get("forecast"))
        except (TypeError, ValueError):
            continue
        if not institution or not source_ref or published_at is None:
            continue
        age_hours = (now.astimezone(timezone.utc) - published_at).total_seconds() / 3600.0
        if age_hours < -0.25 or age_hours > MAX_EXPECTATION_AGE_HOURS:
            continue
        item = dict(row)
        item["institution"] = institution
        item["source_ref"] = source_ref
        item["published_at"] = iso_z(published_at)
        item["forecast"] = forecast
        try:
            historical_mae = float(row.get("historical_mae"))
            item["historical_mae"] = historical_mae if historical_mae > 0 else None
        except (TypeError, ValueError):
            item["historical_mae"] = None
        # Do not trust an externally asserted "accuracy_weight". The canonical
        # weight is derived below from observed historical forecast error.
        item["accuracy_weight"] = 1.0
        valid.append(item)

    mae_rows = [float(row["historical_mae"]) for row in valid if row.get("historical_mae") is not None]
    if len(mae_rows) >= 2:
        benchmark_mae = median(mae_rows)
        for item in valid:
            mae = item.get("historical_mae")
            if mae is None:
                item["accuracy_weight"] = 1.0
                item["accuracy_weight_source"] = "equal_weight_missing_history"
                continue
            item["accuracy_weight"] = max(0.5, min(2.0, benchmark_mae / float(mae)))
            item["accuracy_weight_source"] = "inverse_historical_mae_normalized"
    else:
        for item in valid:
            item["accuracy_weight"] = 1.0
            item["accuracy_weight_source"] = "equal_weight_insufficient_history"
    return valid


def _observation(release: Mapping[str, Any], now: datetime, provider: str) -> Observation | None:
    forecasts = _valid_forecasts(release.get("forecasts"), now)
    if len(forecasts) < 2:
        return None

    event_at = _dt(release.get("event_at"))
    if event_at is None:
        return None
    hours_until = (event_at - now.astimezone(timezone.utc)).total_seconds() / 3600.0
    if hours_until < -1.0 or hours_until > 24.0:
        return None

    weighted_rows = [(float(row["forecast"]), float(row["accuracy_weight"])) for row in forecasts]
    weighted_mean = _weighted_mean(weighted_rows)
    plain_median = median(float(row["forecast"]) for row in forecasts)
    market_consensus = None
    try:
        if release.get("market_consensus") is not None:
            market_consensus = float(release["market_consensus"])
    except (TypeError, ValueError):
        market_consensus = None

    p_below = (
        _probability_below_market_consensus(forecasts, market_consensus)
        if market_consensus is not None
        else None
    )

    indicator = str(release.get("indicator") or "").strip()
    period = str(release.get("period") or "").strip()
    region = str(release.get("region") or "").strip().upper()
    if not indicator or not period or region not in {"US", "EU"}:
        return None

    source_refs = sorted({str(row["source_ref"]) for row in forecasts})
    return Observation.make(
        adapter="macro_expectations",
        metric="macro_expectation_distribution",
        entity="EURUSD",
        observed_at=iso_z(now),
        value={
            "region": region,
            "indicator": indicator,
            "period": period,
            "event_at": iso_z(event_at),
            "hours_until": round(hours_until, 4),
            "forecast_count": len(forecasts),
            "weighted_mean": None if weighted_mean is None else round(weighted_mean, 6),
            "median": round(float(plain_median), 6),
            "market_consensus": market_consensus,
            "p_actual_below_market_consensus_proxy": None if p_below is None else round(p_below, 6),
            "p_actual_above_market_consensus_proxy": None if p_below is None else round(1.0 - p_below, 6),
        },
        unit=str(release.get("unit") or "unknown"),
        source=f"Sourced macro expectations bundle: {provider}",
        source_type="secondary",
        source_ref=";".join(source_refs),
        reliability=0.78,
        independence_cluster=f"macro_expectations:{region}:{indicator}:{period}",
        tags=("macro_expectations", "EURUSD", region, "pre_event"),
        metadata={
            "provider": provider,
            "region": region,
            "indicator": indicator,
            "period": period,
            "event_at": iso_z(event_at),
            "market_consensus": market_consensus,
            "forecast_count": len(forecasts),
            "forecasts": forecasts,
            "probability_proxy_requires_historical_mae": True,
            "accuracy_weight_method": (
                "inverse_historical_mae_normalized"
                if sum(1 for row in forecasts if row.get("historical_mae") is not None) >= 2
                else "equal_weight_insufficient_history"
            ),
            "probability_proxy_calibrated": False,
            "note": (
                "Probability proxy is a weighted forecast-error mixture when historical_mae is supplied; "
                "it is not presented as a calibrated market probability."
            ),
        },
    )


class MacroExpectationsAdapter:
    name = "macro_expectations"
    version = "1.0.0"

    def __init__(self, path: Path | None = None) -> None:
        raw = str(os.environ.get(EXPECTATIONS_PATH_ENV) or "").strip()
        self.path = path or (Path(raw) if raw else None)

    def run(self, now: datetime) -> AdapterResult:
        if self.path is None or not self.path.exists():
            return AdapterResult(self.name, (), ())
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return AdapterResult(self.name, (), ())
        provider = str(payload.get("provider") or "").strip()
        if not provider:
            return AdapterResult(self.name, (), ())
        observations = []
        for release in payload.get("releases") or []:
            if not isinstance(release, Mapping):
                continue
            obs = _observation(release, now, provider)
            if obs is not None:
                observations.append(obs)
        return AdapterResult(self.name, tuple(observations), ())
