#!/usr/bin/env python3
"""Shadow-only anytime-valid mean inference for BriefRooms research.

This implementation uses a simple alpha-spending confidence sequence. At look
n it allocates alpha_n = alpha / (n(n+1)); the allocations sum to alpha.
A two-sided Hoeffding interval is constructed for the explicitly winsorized
bounded estimand at every n. By the union bound, simultaneous coverage is at
least 1-alpha under the stated bounded independent / martingale-difference
assumptions.

The result has zero promotion or trading authority. Current PR35/PR36 fixed-N
single-look gates remain authoritative research methodology v2.
"""
from __future__ import annotations

import hashlib
import json
import math
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "briefrooms-anytime-valid-shadow-v1"
METHOD = "alpha_spending_hoeffding_confidence_sequence_v1"

DEFAULT_ALPHA = 0.10
DEFAULT_LOWER_PERCENT = -5.0
DEFAULT_UPPER_PERCENT = 5.0


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def plan(
    *,
    alpha: float = DEFAULT_ALPHA,
    lower_bound: float = DEFAULT_LOWER_PERCENT,
    upper_bound: float = DEFAULT_UPPER_PERCENT,
    estimand: str = "winsorized_mean_return_percent",
) -> dict[str, Any]:
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0,1)")
    if not lower_bound < upper_bound:
        raise ValueError("lower_bound must be below upper_bound")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "method": METHOD,
        "alpha": float(alpha),
        "confidence": 1.0 - float(alpha),
        "estimand": estimand,
        "bounds": {"lower": float(lower_bound), "upper": float(upper_bound)},
        "look_schedule": "after_each_new_eligible_observation",
        "alpha_spending": "alpha_n=alpha/(n*(n+1))",
        "two_sided_fixed_look_bound": "Hoeffding",
        "assumptions": [
            "observations_are_bounded_after_explicit_winsorization",
            "independent_or_valid_martingale_difference_sequence",
        ],
        "authority": "shadow_only",
    }
    payload["plan_hash"] = _sha(payload)
    return payload


def _winsorize(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def confidence_sequence(
    values: Sequence[float],
    *,
    alpha: float = DEFAULT_ALPHA,
    lower_bound: float = DEFAULT_LOWER_PERCENT,
    upper_bound: float = DEFAULT_UPPER_PERCENT,
) -> dict[str, Any]:
    """Return the current time-uniform CS and compact path diagnostics."""
    p = plan(alpha=alpha, lower_bound=lower_bound, upper_bound=upper_bound)
    lower = float(lower_bound)
    upper = float(upper_bound)
    width = upper - lower
    clipped = [_winsorize(float(x), lower, upper) for x in values]
    clipped_count = sum(1 for raw, win in zip(values, clipped) if float(raw) != win)

    looks: list[dict[str, Any]] = []
    first_positive_n = None
    first_negative_n = None
    for n in range(1, len(clipped) + 1):
        sample = clipped[:n]
        mean = fmean(sample)
        alpha_n = float(alpha) / (n * (n + 1))
        radius = width * math.sqrt(math.log(2.0 / alpha_n) / (2.0 * n))
        ci_low = max(lower, mean - radius)
        ci_high = min(upper, mean + radius)
        status = "INCONCLUSIVE"
        if ci_low > 0.0:
            status = "POSITIVE"
            if first_positive_n is None:
                first_positive_n = n
        elif ci_high < 0.0:
            status = "NEGATIVE"
            if first_negative_n is None:
                first_negative_n = n
        looks.append(
            {
                "n": n,
                "mean": round(mean, 10),
                "lower": round(ci_low, 10),
                "upper": round(ci_high, 10),
                "alpha_n": alpha_n,
                "status": status,
            }
        )

    if not looks:
        current = {
            "n": 0,
            "mean": None,
            "lower": None,
            "upper": None,
            "status": "INSUFFICIENT_DATA",
        }
    else:
        current = dict(looks[-1])

    path_digest = _sha(
        [
            (row["n"], row["mean"], row["lower"], row["upper"], row["status"])
            for row in looks
        ]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "method": METHOD,
        "authority": "shadow_only",
        "formal_promotion_decision": False,
        "plan": p,
        "current": current,
        "first_positive_n": first_positive_n,
        "first_negative_n": first_negative_n,
        "winsorized_observation_count": clipped_count,
        "raw_observation_count": len(values),
        "path_digest": path_digest,
        "assumption_warning": (
            "Time-uniform coverage applies to the committed bounded estimand under "
            "the listed independence/martingale assumptions; financial regime "
            "dependence can invalidate those assumptions."
        ),
    }


def values_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    field: str = "return_percent",
    subtract: float = 0.0,
) -> list[float]:
    values: list[float] = []
    for row in rows:
        raw = row.get(field)
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number - float(subtract))
    return values


def safety_controls() -> dict[str, bool]:
    return {
        "production_promotion": False,
        "policy_writeback": False,
        "ranking_writeback": False,
        "sizing_writeback": False,
        "trade_execution": False,
    }
