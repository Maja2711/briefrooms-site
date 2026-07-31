#!/usr/bin/env python3
"""Candidate filtering and ranking using the same score as live holdings."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence

from brace_portfolio_config import EngineConfig


def is_duplicate_exposure(
    candidate: Mapping[str, Any],
    holdings: Sequence[Mapping[str, Any]],
) -> bool:
    key = str(candidate.get("exposure_key") or "")
    if not key:
        return False
    return any(
        str(holding.get("exposure_key") or "") == key
        and str(holding.get("instrument_id") or holding.get("id"))
        != str(candidate.get("instrument_id"))
        for holding in holdings
    )


def rank_candidates(
    analyses: Iterable[Mapping[str, Any]],
    current_ids: Iterable[str],
    holdings: Sequence[Mapping[str, Any]],
    config: EngineConfig,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    current = {str(item) for item in current_ids}
    accepted: List[Dict[str, Any]] = []
    for raw in analyses:
        item = dict(raw)
        instrument_id = str(item.get("instrument_id") or "")
        if not instrument_id or instrument_id in current:
            continue
        if item.get("availability") != "AVAILABLE" or not item.get("active", True):
            continue
        minimum_quality = float(item.get("minimum_data_quality") or 0.0)
        confidence = float(item.get("confidence_score") or 0.0)
        if confidence < max(minimum_quality, config.minimum_confidence):
            continue
        if int(item.get("observations") or 0) < 126:
            continue
        if item.get("price_age_days") is None or int(item["price_age_days"]) > 7:
            continue
        if is_duplicate_exposure(item, holdings):
            item["duplicate_exposure"] = True
            item["eligible_for_rotation"] = False
        else:
            item["duplicate_exposure"] = False
            item["eligible_for_rotation"] = True
        accepted.append(item)
    accepted.sort(
        key=lambda item: (
            bool(item.get("eligible_for_rotation")),
            float(item.get("risk_adjusted_score") or -99.0),
            float(item.get("final_score") or 0.0),
            str(item.get("instrument_id")),
        ),
        reverse=True,
    )
    for index, item in enumerate(accepted[:limit], 1):
        item["rank"] = index
    return accepted[:limit]
